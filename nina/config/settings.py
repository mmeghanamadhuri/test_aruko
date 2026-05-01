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
    """Tunables for the BLDC navigation manager.

    Two backends are supported and chosen via `mode`:

      mode='local'  - drive the JYQDs directly from the Jetson Orin
                      Nano's GPIOs. Uses `backend_name` and
                      `pwm_frequency_hz`. This is the historical path.

      mode='remote' - send ASCII commands over a serial port to a
                      Raspberry Pi running
                      `pi_motor_bridge/motor_bridge.py`. The Pi owns
                      the JYQDs. Uses `remote_serial_port`,
                      `remote_baudrate`, `remote_response_timeout_sec`.
                      `backend_name` / `pwm_frequency_hz` are ignored
                      in this mode.

    `default_speed_percent`, `turn_duration_sec`, `invert_left_dir`,
    and `invert_right_dir` apply to both modes - they live in the
    Jetson side regardless of who actually toggles GPIOs.
    """
    backend_name: str
    pwm_frequency_hz: int
    default_speed_percent: int
    turn_duration_sec: float
    invert_left_dir: bool
    invert_right_dir: bool
    # Remote-mode (Pi serial bridge) settings; ignored when mode='local'.
    mode: str = "local"
    remote_serial_port: str = "/dev/ttyUSB0"
    remote_baudrate: int = 115200
    remote_response_timeout_sec: float = 0.4


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
        # 15% matches the Sirena RPi reference build's `forward_forever`
        # default. Bump via NINA_NAV_SPEED for harder cruises.
        default_speed_percent=int(os.environ.get("NINA_NAV_SPEED", "15")),
        turn_duration_sec=float(os.environ.get("NINA_NAV_TURN_SEC", "2.3")),
        # Flip if a wheel spins opposite of what the GUI expects (the
        # JYQD ZF level for "forward" depends on motor wiring polarity).
        invert_left_dir=_env_bool("NINA_NAV_INVERT_LEFT", False),
        invert_right_dir=_env_bool("NINA_NAV_INVERT_RIGHT", False),
        # 'local'  -> Jetson GPIOs drive the JYQDs directly.
        # 'remote' -> commands are sent over serial to a Raspberry Pi
        #             running pi_motor_bridge/motor_bridge.py.
        mode=os.environ.get("NINA_NAV_MODE", "local").strip().lower(),
        remote_serial_port=os.environ.get("NINA_NAV_REMOTE_PORT", "/dev/ttyUSB0"),
        remote_baudrate=int(os.environ.get("NINA_NAV_REMOTE_BAUD", "115200")),
        remote_response_timeout_sec=float(
            os.environ.get("NINA_NAV_REMOTE_TIMEOUT_SEC", "0.4")
        ),
    )

    autonomy = AutonomySettings(
        tick_hz=float(os.environ.get("NINA_AUTO_TICK_HZ", "5")),
        # 15% matches the GUI manual-mode floor (MIN_SPEED_PCT) so an
        # operator dropping out of autonomy doesn't see the wheels
        # change pace mid-handoff. Bump via NINA_AUTO_CRUISE_PCT for
        # tests that want a faster wander.
        cruise_speed_pct=int(os.environ.get("NINA_AUTO_CRUISE_PCT", "15")),
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
