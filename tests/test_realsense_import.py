"""Tests for `nina.sensors.realsense_d435._import_pyrealsense2`.

The librealsense Python bindings ship in two package layouts that look
identical from `import pyrealsense2` but expose the C symbols at
different attribute paths:

    Layout A (flat / re-exported, common on x86 wheels):
        rs = import pyrealsense2 ; rs.pipeline()  works

    Layout B (submodule-only, common on Jetson cmake builds with
             librealsense >= 2.55):
        import pyrealsense2 ; pyrealsense2.pipeline  -> AttributeError
        from pyrealsense2 import pyrealsense2 as rs  -> works

The first real Jetson run after the depth-camera installer caught
this: `pyrealsense2 imported but missing symbols`. The fix was the
import helper - these tests pin the contract so we don't accidentally
revert to a single-layout import that breaks one half of the install
base.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Optional

import pytest

from nina.sensors import realsense_d435


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


REQUIRED_SYMBOLS = ("pipeline", "config", "stream", "format")


def _make_fake_module(with_symbols: bool) -> ModuleType:
    """Build a fake C-extension module that either has or doesn't
    have the symbols the driver checks for. We use the absence /
    presence of these to simulate the two layouts without touching
    actual librealsense."""
    mod = ModuleType("fake_realsense")
    if with_symbols:
        for name in REQUIRED_SYMBOLS:
            # The values aren't called - the helper only asks
            # `hasattr(mod, name)`. Sentinels are fine.
            setattr(mod, name, object())
    return mod


@pytest.fixture
def cleanup_sys_modules():
    """Snapshot/restore sys.modules entries the tests touch so
    monkeypatched fakes don't leak across tests."""
    saved = {
        k: v for k, v in sys.modules.items()
        if k == "pyrealsense2" or k.startswith("pyrealsense2.")
    }
    yield
    # Drop everything we added, then restore the originals.
    for k in list(sys.modules):
        if k == "pyrealsense2" or k.startswith("pyrealsense2."):
            del sys.modules[k]
    sys.modules.update(saved)


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_layout_a_flat_returns_top_level(cleanup_sys_modules) -> None:
    """When the top-level `pyrealsense2` exposes the symbols
    directly (x86 wheel layout), the helper returns it without
    falling through to the submodule probe."""
    sys.modules["pyrealsense2"] = _make_fake_module(with_symbols=True)
    # Deliberately don't register a .pyrealsense2 submodule - if the
    # helper tries to import it we'd get ImportError, which would
    # surface here.

    mod = realsense_d435._import_pyrealsense2()
    assert all(hasattr(mod, n) for n in REQUIRED_SYMBOLS)
    assert mod is sys.modules["pyrealsense2"]


def test_layout_b_submodule_falls_through(cleanup_sys_modules) -> None:
    """When the top-level `pyrealsense2` is empty (no `pipeline`
    etc.) but `pyrealsense2.pyrealsense2` has the symbols (Jetson
    cmake build layout), the helper returns the submodule. This is
    the case the user's Jetson install hit - failing here means
    `nina/sensors/realsense_d435.py` would crash on `rs.pipeline()`
    on every Jetson with librealsense built from source."""
    empty_top = _make_fake_module(with_symbols=False)
    inner = _make_fake_module(with_symbols=True)
    # Make the top-level package act like a real package whose
    # __init__.py forgot to re-export the C bindings.
    setattr(empty_top, "__path__", ["/fake/path"])
    sys.modules["pyrealsense2"] = empty_top
    sys.modules["pyrealsense2.pyrealsense2"] = inner

    mod = realsense_d435._import_pyrealsense2()
    assert mod is inner
    assert all(hasattr(mod, n) for n in REQUIRED_SYMBOLS)


def test_neither_layout_raises_descriptive_error(cleanup_sys_modules) -> None:
    """When neither layout has the symbols (broken install: the C
    extension didn't build, or only an empty stub got installed),
    the helper must raise something descriptive enough that the
    operator can act on it. Silently returning a useless module
    would leave the autonomy stack 'enabled' but every depth read
    would crash."""
    sys.modules["pyrealsense2"] = _make_fake_module(with_symbols=False)
    sys.modules["pyrealsense2.pyrealsense2"] = _make_fake_module(with_symbols=False)

    with pytest.raises((ImportError, Exception)) as exc_info:
        realsense_d435._import_pyrealsense2()
    # Message must mention the actual remediation path - what file
    # to check - not just "module not found".
    msg = str(exc_info.value)
    assert "__init__.py" in msg or "C bindings" in msg, (
        f"helper raised but the error message {msg!r} doesn't tell "
        f"the operator what to fix"
    )


def test_completely_missing_pyrealsense2_propagates(cleanup_sys_modules) -> None:
    """Both candidates fail to import (pyrealsense2 not installed at
    all). The helper must propagate the underlying ImportError so
    `is_available()` can report it verbatim - that's the message
    the operator sees on the Health screen Depth row."""
    # Strip any cached entries so importlib actually attempts and fails.
    for k in list(sys.modules):
        if k == "pyrealsense2" or k.startswith("pyrealsense2."):
            del sys.modules[k]

    with pytest.raises(ImportError):
        realsense_d435._import_pyrealsense2()


def test_is_available_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """NINA_DEPTH_DISABLE=1 short-circuits availability checks BEFORE
    the import attempt - operators on bots without a D435 set this
    so the autonomy stack doesn't waste a second probing on every
    enable. Verifying here so a refactor that moves the env-var
    check past the import doesn't break that workflow."""
    monkeypatch.setenv("NINA_DEPTH_DISABLE", "1")
    available, msg = realsense_d435.is_available()
    assert available is False
    assert "NINA_DEPTH_DISABLE" in msg


def test_is_available_passes_through_helper(
    monkeypatch: pytest.MonkeyPatch, cleanup_sys_modules
) -> None:
    """`is_available()` is the public 'should we even try?' probe
    called from the autonomy enable path. It must use the same
    layout-tolerant helper so a Jetson submodule-layout install
    doesn't make is_available() report False (and disable depth
    autonomy) while the actual driver code would have worked
    fine."""
    monkeypatch.delenv("NINA_DEPTH_DISABLE", raising=False)
    empty_top = _make_fake_module(with_symbols=False)
    inner = _make_fake_module(with_symbols=True)
    setattr(empty_top, "__path__", ["/fake/path"])
    sys.modules["pyrealsense2"] = empty_top
    sys.modules["pyrealsense2.pyrealsense2"] = inner

    available, msg = realsense_d435.is_available()
    assert available is True, (
        f"is_available() said False on a working submodule-layout "
        f"install: {msg!r}"
    )
