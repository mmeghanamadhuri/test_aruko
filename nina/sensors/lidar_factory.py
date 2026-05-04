"""Factory: pick the right lidar driver based on `NINA_LIDAR_MODEL`.

Nina has shipped on two lidars:

  * ``a1`` / ``rplidar_a1`` - SLAMTEC RPLIDAR A1M8, USB-serial
    (``/dev/ttyUSB0`` @ 115200). The historical default. Range ~12 m,
    scan rate ~5.5 Hz, 8 kHz sample rate. Driver: `RPLidarA1`.

  * ``s2e`` / ``slamtec_s2e`` - SLAMTEC RPLIDAR S2E, Ethernet (UDP at
    ``192.168.11.2:8089``). Range ~30 m, scan rate ~10-15 Hz, 32 kHz
    sample rate, IP65, dToF. Driver: `SlamtecS2E`. This is the
    current build's default.

The Map screen and autonomy controller never need to know which
physical model is hooked up - they always go through this factory
and read scans through the common ``LidarLike`` protocol below.

Configuration:

  ``NINA_LIDAR_MODEL=s2e``  (default)
      Use the Slamtec S2E driver (UDP). Honours
      ``NINA_LIDAR_HOST`` (default 192.168.11.2) and
      ``NINA_LIDAR_UDP_PORT`` (default 8089).

  ``NINA_LIDAR_MODEL=a1``
      Use the legacy A1M8 driver (USB-serial). Honours
      ``NINA_LIDAR_PORT`` (default /dev/ttyUSB0) and
      ``NINA_LIDAR_BAUD`` (default 115200).

  ``NINA_LIDAR_MODEL=auto`` (or unset)
      Try S2E first. On failure (UDP unreachable / pyrplidarsdk
      missing) fall back to A1. Useful when the same disk image
      gets flashed onto bots in mixed lidar generations.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol, Tuple

from nina.sensors.types import LidarScan


log = logging.getLogger("nina.sensors.lidar_factory")


# Model strings we accept. Aliases collapse to a canonical form.
_MODEL_S2E = "s2e"
_MODEL_A1 = "a1"
_MODEL_AUTO = "auto"

_MODEL_ALIASES = {
    "s2e": _MODEL_S2E,
    "slamtec_s2e": _MODEL_S2E,
    "slamtec-s2e": _MODEL_S2E,
    "rplidar_s2e": _MODEL_S2E,
    "rplidar-s2e": _MODEL_S2E,
    "a1": _MODEL_A1,
    "a1m8": _MODEL_A1,
    "rplidar_a1": _MODEL_A1,
    "rplidar-a1": _MODEL_A1,
    "rplidar_a1m8": _MODEL_A1,
    "rplidar-a1m8": _MODEL_A1,
    "rplidar": _MODEL_A1,
    "auto": _MODEL_AUTO,
    "": _MODEL_AUTO,
}


class LidarLike(Protocol):
    """Structural type every lidar driver in `nina.sensors` honours."""

    def open(self) -> None: ...
    def close(self) -> None: ...
    def read(self) -> Optional[LidarScan]: ...
    def status(self) -> Tuple[bool, str]: ...


def configured_model() -> str:
    raw = os.environ.get("NINA_LIDAR_MODEL", "").strip().lower()
    return _MODEL_ALIASES.get(raw, raw or _MODEL_AUTO)


def model_label(model: Optional[str] = None) -> str:
    """Return a human-readable name for the active model. Used for
    UI pills and health-row labels so the operator sees the actual
    hardware in their build, not a generic 'Lidar'.
    """
    if model is not None:
        normalised = model.strip().lower()
        m = _MODEL_ALIASES.get(normalised, normalised)
    else:
        m = configured_model()
    if m == _MODEL_S2E:
        return "Slamtec S2E"
    if m == _MODEL_A1:
        return "RPLIDAR A1"
    return "Lidar"


def build_lidar(model: Optional[str] = None) -> LidarLike:
    """Construct (but do not open) a lidar driver instance.

    `model` overrides ``NINA_LIDAR_MODEL`` when provided; the override
    is the seam the test suite uses to exercise both branches without
    monkey-patching env vars.
    """
    if model is not None:
        # Normalise alias / casing the same way `configured_model()`
        # does for env-driven values, so callers passing "A1" or
        # "rplidar_a1" don't fall through to the unknown-model
        # error path.
        normalised = model.strip().lower()
        requested = _MODEL_ALIASES.get(normalised, normalised)
    else:
        requested = configured_model()
    if requested == _MODEL_AUTO:
        # Try S2E first because the current bring-up doc tells
        # operators that's the default. If the package isn't even
        # installed (dev Mac) we go straight to the A1 driver - it
        # also degrades gracefully on dev hosts without the device.
        try:
            from nina.sensors.slamtec_s2e import SlamtecS2E, is_available as s2e_avail
            ok, _msg = s2e_avail()
            if ok:
                log.info("lidar factory: auto -> SlamtecS2E")
                return SlamtecS2E()
        except Exception as exc:
            log.info(
                "lidar factory: SlamtecS2E unavailable (%s); falling back to A1",
                exc,
            )
        from nina.sensors.rplidar_a1 import RPLidarA1
        log.info("lidar factory: auto -> RPLidarA1 (fallback)")
        return RPLidarA1()

    if requested == _MODEL_S2E:
        from nina.sensors.slamtec_s2e import SlamtecS2E
        return SlamtecS2E()

    if requested == _MODEL_A1:
        from nina.sensors.rplidar_a1 import RPLidarA1
        return RPLidarA1()

    raise ValueError(
        f"Unknown NINA_LIDAR_MODEL={requested!r}; "
        "expected one of: s2e, a1, auto"
    )
