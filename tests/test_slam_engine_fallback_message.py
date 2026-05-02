"""SlamEngine.open() must surface an actionable install hint when
breezyslam is missing.

Operators were seeing the SLAM pill say "breezyslam not installed"
with no idea what to do. On Jetson the install needs apt build
deps + a PEP 668 escape hatch, which is exactly what
scripts/install-breezyslam-jetson.sh handles. The pill text now
points at that script (or the plain `pip install` path on
non-Jetson hosts) so the operator can self-serve without grepping
the source.
"""

from __future__ import annotations

import sys

import pytest

from nina.slam import engine as slam_engine


def _force_breezyslam_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block `import breezyslam.*` no matter what's actually installed
    on the dev host so this test is deterministic."""

    # Drop any cached submodules so the import statement inside
    # SlamEngine.open() actually runs.
    for mod in list(sys.modules):
        if mod == "breezyslam" or mod.startswith("breezyslam."):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __builtins__.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "breezyslam" or name.startswith("breezyslam."):
            raise ImportError("No module named 'breezyslam'")
        return real_import(name, globals, locals, fromlist, level)

    if isinstance(__builtins__, dict):
        monkeypatch.setitem(__builtins__, "__import__", fake_import)
    else:
        monkeypatch.setattr(__builtins__, "__import__", fake_import)


def test_jetson_message_points_to_install_script(monkeypatch):
    """On aarch64 the pill must name `install-breezyslam-jetson.sh`
    by exact filename so the operator can copy-paste from the
    Map / Perception screen straight into a terminal."""
    _force_breezyslam_missing(monkeypatch)
    monkeypatch.setattr(
        "platform.machine",
        lambda: "aarch64",
    )

    eng = slam_engine.SlamEngine(map_size_pixels=200, map_size_meters=5.0)
    eng.open()

    assert eng.is_fallback() is True
    reason = eng.fallback_reason()
    assert "install-breezyslam-jetson.sh" in reason, (
        f"fallback reason {reason!r} should name the install script "
        "verbatim so operators can run it without searching the repo"
    )
    # The original ImportError text must STILL be in there - it's the
    # one piece operators paste into a search box when the script
    # itself fails.
    assert "No module named" in reason


def test_non_jetson_message_points_to_pip(monkeypatch):
    """On x86 / Mac dev hosts the operator doesn't need the script -
    plain `pip install` works. Don't tell them to run a Jetson
    script that bails on `uname -m != aarch64`."""
    _force_breezyslam_missing(monkeypatch)
    monkeypatch.setattr(
        "platform.machine",
        lambda: "x86_64",
    )

    eng = slam_engine.SlamEngine(map_size_pixels=200, map_size_meters=5.0)
    eng.open()

    assert eng.is_fallback() is True
    reason = eng.fallback_reason()
    assert "pip install breezyslam" in reason, (
        f"fallback reason {reason!r} should suggest `pip install "
        "breezyslam` on non-Jetson hosts"
    )
    assert "install-breezyslam-jetson.sh" not in reason, (
        f"fallback reason {reason!r} mentions the Jetson script on a "
        "non-Jetson host; the script aborts cleanly there but it's "
        "still confusing as a hint"
    )
