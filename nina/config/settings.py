from dataclasses import dataclass
from pathlib import Path
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


@dataclass(frozen=True)
class NavigationSettings:
    backend_name: str
    pwm_frequency_hz: int
    default_speed_percent: int
    turn_duration_sec: float
    min_duty_percent: float
    max_duty_percent: float
    kick_start_duty_percent: float
    kick_start_duration_sec: float
    invert_left_dir: bool
    invert_right_dir: bool


@dataclass(frozen=True)
class AutonomySettings:
    """Shared knobs for the autonomous pilot.

    All distances are millimetres so they line up with the sensor data
    types. Speeds are percent (0..100) feeding straight into
    `NavigationManager.set_wheels()`.
    """
    tick_hz: float                       # autonomy decision rate
    cruise_speed_pct: int                # straight-line speed
    turn_speed_pct: int                  # in-place spin speed
    forward_clear_mm: int                # at >= this, go straight
    side_clear_mm: int                   # min side clearance for forward
    emergency_stop_mm: int               # below this any direction => stop+back-off
    cliff_min_mm: int                    # IR reading below this = abort
    turn_duration_ms: int                # min time committed to a chosen turn
    backoff_duration_ms: int             # time to reverse before re-evaluating


@dataclass(frozen=True)
class SlamSettings:
    """BreezySLAM map / pose configuration."""
    map_size_pixels: int                 # square map; default 800
    map_size_meters: float               # physical extent of the map
    update_hz: float                     # SLAM update rate (cap)
    hole_width_mm: int                   # smallest passable opening
    random_seed: int


@dataclass(frozen=True)
class NinaSettings:
    serial_port: str
    baudrate: int
    neutral_action_name: str
    actions_dir: Path
    manifest_path: Path
    recordings_dir: Path
    recording_sample_hz: float
    navigation: NavigationSettings
    autonomy: AutonomySettings
    slam: SlamSettings


def load_settings(repo_root: Path) -> NinaSettings:
    actions_dir = repo_root / "nina" / "actions"
    recordings_dir = repo_root / "nina" / "actions" / "recordings"
    manifest_path = actions_dir / "manifest.json"

    recordings_dir.mkdir(parents=True, exist_ok=True)

    navigation = NavigationSettings(
        backend_name=os.environ.get("NINA_NAV_BACKEND", "jetson"),
        pwm_frequency_hz=int(os.environ.get("NINA_NAV_PWM_HZ", "2000")),
        default_speed_percent=int(os.environ.get("NINA_NAV_SPEED", "15")),
        turn_duration_sec=float(os.environ.get("NINA_NAV_TURN_SEC", "2.3")),
        # Defaults match the proven Pi build (speed=15 at 15% duty, no
        # deadband, no kick-start). The earlier non-zero defaults were a
        # workaround for the broken direction pin - now that direction is
        # correctly wired, the motor starts cleanly at low duty just like
        # on the Pi. Set the env vars below if a particular driver/motor
        # combo still needs help starting.
        min_duty_percent=float(os.environ.get("NINA_NAV_MIN_DUTY", "0")),
        max_duty_percent=float(os.environ.get("NINA_NAV_MAX_DUTY", "100")),
        kick_start_duty_percent=float(os.environ.get("NINA_NAV_KICK_DUTY", "100")),
        kick_start_duration_sec=float(os.environ.get("NINA_NAV_KICK_SEC", "0")),
        invert_left_dir=_env_bool("NINA_NAV_INVERT_LEFT", False),
        invert_right_dir=_env_bool("NINA_NAV_INVERT_RIGHT", False),
    )

    autonomy = AutonomySettings(
        tick_hz=float(os.environ.get("NINA_AUTO_TICK_HZ", "5")),
        cruise_speed_pct=int(os.environ.get("NINA_AUTO_CRUISE_PCT", "18")),
        turn_speed_pct=int(os.environ.get("NINA_AUTO_TURN_PCT", "16")),
        forward_clear_mm=int(os.environ.get("NINA_AUTO_FWD_CLEAR_MM", "700")),
        side_clear_mm=int(os.environ.get("NINA_AUTO_SIDE_CLEAR_MM", "350")),
        emergency_stop_mm=int(os.environ.get("NINA_AUTO_ESTOP_MM", "300")),
        cliff_min_mm=int(os.environ.get("NINA_AUTO_CLIFF_MIN_MM", "60")),
        turn_duration_ms=int(os.environ.get("NINA_AUTO_TURN_MS", "350")),
        backoff_duration_ms=int(os.environ.get("NINA_AUTO_BACKOFF_MS", "500")),
    )

    slam = SlamSettings(
        map_size_pixels=int(os.environ.get("NINA_SLAM_PIXELS", "800")),
        map_size_meters=float(os.environ.get("NINA_SLAM_METERS", "20")),
        update_hz=float(os.environ.get("NINA_SLAM_HZ", "5")),
        hole_width_mm=int(os.environ.get("NINA_SLAM_HOLE_MM", "600")),
        random_seed=int(os.environ.get("NINA_SLAM_SEED", "0xdeadbeef"), 0),
    )

    return NinaSettings(
        serial_port=os.environ.get("NINA_DXL_PORT", "/dev/ttyUSB0"),
        baudrate=int(os.environ.get("NINA_DXL_BAUD", "222222")),
        neutral_action_name=os.environ.get("NINA_NEUTRAL_ACTION", "neutral"),
        actions_dir=actions_dir,
        manifest_path=manifest_path,
        recordings_dir=recordings_dir,
        recording_sample_hz=float(os.environ.get("NINA_RECORD_HZ", "20")),
        navigation=navigation,
        autonomy=autonomy,
        slam=slam,
    )
