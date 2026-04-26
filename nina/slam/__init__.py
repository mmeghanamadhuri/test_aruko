"""SLAM stack for Nina (BreezySLAM-based).

The engine consumes `LidarScan` objects and produces an occupancy grid
(numpy uint8 array, 0 = occupied, 255 = free, 127 = unknown) plus a
robot pose in mm + degrees. See `engine.py` for details.
"""

from nina.slam.engine import SlamEngine, SlamPose, SlamSnapshot

__all__ = ["SlamEngine", "SlamPose", "SlamSnapshot"]
