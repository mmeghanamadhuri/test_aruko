"""Lock the lidar factory dispatch contract.

The factory in `nina.sensors.lidar_factory` is the single seam every
caller (SlamWorker, AutonomyController, the Map / Perception screens)
goes through to get a lidar driver. The contract is:

  * `NINA_LIDAR_MODEL=s2e` (or unset, since 'auto' falls through to
    S2E when the SDK is importable) must hand back a `SlamtecS2E`
    instance.
  * `NINA_LIDAR_MODEL=a1` must hand back the legacy `RPLidarA1`
    driver - so existing bots can opt out of the new default with
    one env var instead of editing code.
  * 'auto' on a host without `pyrplidarsdk` must NOT raise; it
    falls back to the A1 driver.
  * Unknown models raise `ValueError` with a helpful list of
    accepted strings.

All four cases are common operator workflows; if the factory ever
silently picks the wrong driver, the symptom is "lidar pill
permanently red" with no obvious cause - exactly the kind of bug
that's expensive to track down on a live bot. Hence the test.
"""

from __future__ import annotations

import os
import sys
from types import ModuleType

import pytest

from nina.sensors import lidar_factory
from nina.sensors.rplidar_a1 import RPLidarA1


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("NINA_LIDAR_"):
            monkeypatch.delenv(key, raising=False)
    yield


def test_explicit_a1_returns_legacy_driver() -> None:
    drv = lidar_factory.build_lidar("a1")
    assert isinstance(drv, RPLidarA1)


def test_aliases_collapse_to_canonical_form() -> None:
    """The factory accepts a handful of obvious aliases so an env
    var typo (`rplidar_a1` vs `a1`) doesn't fall through to 'auto'
    and silently pick the wrong driver."""
    for alias in ("a1", "A1", "rplidar_a1", "rplidar", "A1M8"):
        drv = lidar_factory.build_lidar(alias)
        assert isinstance(drv, RPLidarA1), (
            f"alias {alias!r} should resolve to RPLidarA1 but got "
            f"{type(drv).__name__}"
        )


def test_unknown_model_raises_with_helpful_message() -> None:
    with pytest.raises(ValueError) as exc:
        lidar_factory.build_lidar("rplidar_xx_imaginary_99")
    msg = str(exc.value)
    assert "s2e" in msg and "a1" in msg, (
        f"unknown-model error must list accepted values; got: {msg!r}"
    )


def test_auto_falls_back_to_a1_when_s2e_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipping default is 's2e', but on a dev host without
    pyrplidarsdk the factory MUST silently fall back to the A1
    driver instead of raising. That keeps `pytest` runnable on a
    laptop and matches the `auto` mode contract for mixed-lidar
    fleets."""
    # Force is_available() to report False without actually trying
    # to import the SDK package.
    from nina.sensors import slamtec_s2e

    monkeypatch.setattr(
        slamtec_s2e,
        "is_available",
        lambda: (False, "test-only: pyrplidarsdk unavailable"),
    )
    drv = lidar_factory.build_lidar("auto")
    assert isinstance(drv, RPLidarA1)


def test_auto_picks_s2e_when_sdk_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """When pyrplidarsdk imports cleanly, `auto` must return the
    SlamtecS2E driver. We can't actually instantiate the driver in
    a unit test (it would dial out to 192.168.11.2 on open(), which
    we never call), but the constructor itself is side-effect free
    so the type check is enough."""
    from nina.sensors import slamtec_s2e

    # Pretend pyrplidarsdk is importable AND make the constructor
    # not try anything network-y. SlamtecS2E.__init__ doesn't open
    # the device, so we only need is_available() to report True.
    monkeypatch.setattr(slamtec_s2e, "is_available", lambda: (True, ""))
    # Stub pyrplidarsdk so even import succeeds on a host that
    # genuinely doesn't have it (CI / dev Mac).
    fake = ModuleType("pyrplidarsdk")
    fake.RplidarDriver = lambda **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyrplidarsdk", fake)

    drv = lidar_factory.build_lidar("auto")
    assert type(drv).__name__ == "SlamtecS2E", (
        f"auto with SDK present should choose SlamtecS2E; got "
        f"{type(drv).__name__}"
    )


def test_configured_model_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINA_LIDAR_MODEL", "a1")
    assert lidar_factory.configured_model() == "a1"
    monkeypatch.setenv("NINA_LIDAR_MODEL", "RPLIDAR_A1")
    assert lidar_factory.configured_model() == "a1"
    monkeypatch.setenv("NINA_LIDAR_MODEL", "s2e")
    assert lidar_factory.configured_model() == "s2e"
    monkeypatch.setenv("NINA_LIDAR_MODEL", "")
    assert lidar_factory.configured_model() == "auto"
    monkeypatch.delenv("NINA_LIDAR_MODEL", raising=False)
    assert lidar_factory.configured_model() == "auto"


def test_model_label_human_readable() -> None:
    """The label is the user-facing string in the Health table /
    Map screen pill. It must be a recognisable product name, not
    the env-var key, so an operator sees 'Slamtec S2E' rather than
    's2e'."""
    assert lidar_factory.model_label("s2e") == "Slamtec S2E"
    assert lidar_factory.model_label("a1") == "RPLIDAR A1"
    # Unknown labels collapse to a generic fallback so the UI never
    # crashes on an env-var typo.
    assert lidar_factory.model_label("invalid") == "Lidar"
