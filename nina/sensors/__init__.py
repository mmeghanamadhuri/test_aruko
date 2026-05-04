"""Sensor abstractions for Nina (lidar, ultrasonic, IR, depth camera).

All drivers in this package follow the same conventions:

  * `is_available() -> tuple[bool, str]`  - cheap import-time probe;
    returns (False, reason) on dev hosts so callers can render a clean
    "not available" pill instead of crashing.
  * `open()`                              - actually contact the
    hardware. Raises on failure with a human-readable message.
  * `close()`                             - release the device.
  * `read()`                              - non-blocking read of the
    latest sample (or `None` if the device isn't ready / open).

Distances are reported in **millimetres** throughout the stack so
SLAM / autonomy don't need unit conversions.
"""

from nina.sensors.lidar_factory import build_lidar, configured_model, model_label
from nina.sensors.types import (
    DepthFrame,
    IRReading,
    LidarScan,
    SensorHealth,
    UltrasonicReading,
)

__all__ = [
    "DepthFrame",
    "IRReading",
    "LidarScan",
    "SensorHealth",
    "UltrasonicReading",
    "build_lidar",
    "configured_model",
    "model_label",
]
