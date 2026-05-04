from dataclasses import dataclass
from pathlib import Path
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


# Upper bound for breakaway timing (seconds). Longer holds behave like
# sustained drive at kick duty, not a start pulse. Env/clamped values
# cannot exceed this.
NAV_START_KICK_SEC_MAX = 1.0


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

    `start_kick_percent` / `start_kick_sec` apply when both wheels were
    at PWM 0 and a new command requests motion: each non-zero side
    briefly runs at at least the kick duty to overcome static friction,
    then drops to the commanded speed. Set either to 0 to disable
    (NINA_NAV_START_KICK_PCT / NINA_NAV_START_KICK_SEC). SEC is clamped
    to at most NAV_START_KICK_SEC_MAX (default when unset = that max).

    `straight_opposite_nudge_sec` (+ `NUDGE_PCT`, `OPPOSITE_ZERO_SETTLE_SEC`)
    apply only to symmetric straight crawls (same dir and speed on
    both sides). The opposite jog runs from rest, when reversing
    straight F<->B while moving, or when transitioning from a turn /
    curve (non-symmetric motion) to symmetric straight while PWM is
    still non-zero. Set NUDGE_SEC to 0 to disable.
    """
    backend_name: str
    pwm_frequency_hz: int
    default_speed_percent: int
    turn_duration_sec: float
    invert_left_dir: bool
    invert_right_dir: bool
    start_kick_percent: int = 35
    start_kick_sec: float = NAV_START_KICK_SEC_MAX
    # Local + remote: delay after DIR+EL before torque (local GPIO). Remote
    # uses the same value as a sleep between protocol steps when mirroring.
    dir_pwm_gap_sec: float = 0.03
    pwm_reassert_sec: float = 0.02
    # Straight-line only: brief opposite jog before crawling (0 sec = off).
    straight_opposite_nudge_sec: float = 0.5
    straight_opposite_nudge_pct: int = 20
    opposite_zero_settle_sec: float = 0.04
    # Pause after soft stop / between stop and fresh motion (`stop()`,
    # `drive_continuous`). Matches `NavigationConfig.settle_delay_sec`.
    settle_delay_sec: float = 0.1
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
    fwd_blocked_backup_sec: float        # reverse when forward blocked this long (0=off)


@dataclass(frozen=True)
class LidarSettings:
    """Lidar transport + model configuration.

    The factory in `nina.sensors.lidar_factory.build_lidar` reads
    these to decide which physical lidar driver to instantiate. Two
    are supported:

      ``model='s2e'``  - SLAMTEC RPLIDAR S2E (Ethernet / UDP). Uses
                          `host` + `udp_port`. ~30 m range, ~10 Hz scan
                          rate, ~32 kHz sample rate. CURRENT default.
      ``model='a1'``   - SLAMTEC RPLIDAR A1M8 (USB-serial). Uses
                          `serial_port` + `baudrate`. ~12 m range,
                          ~5.5 Hz scan rate. Legacy bring-up.
      ``model='auto'`` - try S2E first, fall back to A1.

    `host`/`serial_port` only matter for the matching transport; the
    other one is ignored.
    """
    model: str
    host: str
    udp_port: int
    serial_port: str
    baudrate: int


@dataclass(frozen=True)
class SlamSettings:
    """BreezySLAM map / pose configuration."""
    map_size_pixels: int                 # square map; default 1000
    map_size_meters: float               # physical extent of the map
    update_hz: float                     # SLAM update rate (cap)
    hole_width_mm: int                   # smallest passable opening
    random_seed: int
    laser_max_range_mm: int              # detection envelope of the lidar (drives BreezySLAM laser model)
    laser_scan_size: int                 # samples per sweep (laser model)
    laser_scan_rate_hz: float            # rev/sec (laser model)


@dataclass(frozen=True)
class GotoSettings:
    """Tunables for the goto-point pilot.

    Goto navigation is goal-directed (vs the wander pilot's
    obstacle-only reactive behaviour). The pilot plans an A* path on
    the live BreezySLAM occupancy grid, follows it with pure-pursuit,
    and re-runs the planner whenever the reactive obstacle layer
    vetoes a path step. All distances are millimetres, speeds are
    percent (0..100).

    The planner inflates walls by an effective radius of:

        max(footprint_radius_mm, ceil(min_passage_width_mm / 2))

    so paths leave a Nina-shaped buffer to walls AND any corridor
    the planner routes through is at least `min_passage_width_mm`
    wide between walls. The two knobs are intentionally separate:
    `footprint_radius_mm` is "this is how big my body is" (geometry)
    while `min_passage_width_mm` is "this is the tightest gap I'm
    willing to send the bot through" (safety policy). Default
    passage width is 2 ft / 610 mm.
    """
    arrival_radius_mm: int                 # within this -> 'arrived'
    footprint_radius_mm: int               # bot body half-width (geometry)
    min_passage_width_mm: int              # smallest corridor width the planner is allowed to use
    cruise_speed_pct: int                  # straight-line speed
    turn_speed_pct: int                    # in-place spin speed
    heading_deadband_deg: float            # |heading_err| <= this -> drive forward
    lookahead_mm: int                      # pure-pursuit lookahead distance
    replan_period_sec: float               # automatic replans, even if path looks fine
    stuck_window_sec: float                # window we look back over for "is the bot moving?"
    stuck_motion_mm: int                   # if pose moved < this in window -> 'stuck'
    tick_hz: float                         # control loop rate
    unknown_pixel_cost: float              # A* extra cost per unknown grey pixel (>=1.0)


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
    lidar: LidarSettings
    goto: GotoSettings


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
        start_kick_percent=int(os.environ.get("NINA_NAV_START_KICK_PCT", "35")),
        start_kick_sec=min(
            NAV_START_KICK_SEC_MAX,
            max(
                0.0,
                float(
                    os.environ.get(
                        "NINA_NAV_START_KICK_SEC", str(NAV_START_KICK_SEC_MAX)
                    )
                ),
            ),
        ),
        dir_pwm_gap_sec=float(os.environ.get("NINA_NAV_DIR_SETTLE_SEC", "0.03")),
        pwm_reassert_sec=float(os.environ.get("NINA_NAV_PWM_REASSERT_SEC", "0.02")),
        straight_opposite_nudge_sec=float(
            os.environ.get("NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC", "0.5")
        ),
        straight_opposite_nudge_pct=int(
            os.environ.get("NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_PCT", "20")
        ),
        opposite_zero_settle_sec=float(
            os.environ.get("NINA_NAV_OPPOSITE_ZERO_SETTLE_SEC", "0.04")
        ),
        settle_delay_sec=float(os.environ.get("NINA_NAV_SETTLE_SEC", "0.1")),
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
        # 8 Hz (was 5 Hz) so the pilot reacts every 125 ms instead of
        # every 200 ms. At 15% PWM (~0.3-0.5 m/s) the bot still
        # coasts a few cm during one tick, but the extra ticks per
        # second cut the worst-case "saw obstacle / decided to turn /
        # actually started turning" latency by ~75 ms.
        tick_hz=float(os.environ.get("NINA_AUTO_TICK_HZ", "8")),
        # 15% matches the GUI manual-mode floor (MIN_SPEED_PCT) so an
        # operator dropping out of autonomy doesn't see the wheels
        # change pace mid-handoff. Bump via NINA_AUTO_CRUISE_PCT for
        # tests that want a faster wander.
        cruise_speed_pct=int(os.environ.get("NINA_AUTO_CRUISE_PCT", "15")),
        turn_speed_pct=int(os.environ.get("NINA_AUTO_TURN_PCT", "16")),
        # 1200 mm (was 700 mm) is the new commit-to-forward
        # threshold. The previous 700 mm gave the BLDCs no room to
        # decelerate before reaching the obstacle: at ~0.4 m/s with
        # ~200 ms tick + ~200 ms wheel coast the bot would stop
        # 50-60 cm from a person -> "almost hitting" was the user
        # report. 1200 mm leaves the bot a clean ~1 m of buffer at
        # the moment it commits to a turn, so the actual stopping
        # distance lands closer to personal-space (~1 m) than
        # arm's-length.
        forward_clear_mm=int(os.environ.get("NINA_AUTO_FWD_CLEAR_MM", "1200")),
        side_clear_mm=int(os.environ.get("NINA_AUTO_SIDE_CLEAR_MM", "450")),
        # 850 mm. Between this and forward_clear_mm the pilot turns;
        # at or inside this radius it reverses immediately (layer 2).
        # 600 mm still let the bot coast into bump range before backoff;
        # 850 matches ~1 m "personal space" when combined with
        # forward_clear=1200 and the timed / dead-end backoff below.
        emergency_stop_mm=int(os.environ.get("NINA_AUTO_ESTOP_MM", "850")),
        cliff_min_mm=int(os.environ.get("NINA_AUTO_CLIFF_MIN_MM", "60")),
        turn_duration_ms=int(os.environ.get("NINA_AUTO_TURN_MS", "350")),
        backoff_duration_ms=int(os.environ.get("NINA_AUTO_BACKOFF_MS", "500")),
        fwd_blocked_backup_sec=float(
            os.environ.get("NINA_AUTO_FWD_BLOCKED_BACKUP_SEC", "2.5")
        ),
    )

    lidar = LidarSettings(
        # 's2e' is the current shipping default (Slamtec S2E,
        # Ethernet / UDP). Set NINA_LIDAR_MODEL=a1 for the legacy
        # USB-serial RPLIDAR A1M8 bring-up, or =auto to probe S2E
        # first and fall through to A1 on failure.
        model=os.environ.get("NINA_LIDAR_MODEL", "s2e").strip().lower(),
        host=os.environ.get("NINA_LIDAR_HOST", "192.168.11.2"),
        udp_port=int(os.environ.get("NINA_LIDAR_UDP_PORT", "8089")),
        # A1-only fields. Ignored when model='s2e'.
        serial_port=os.environ.get("NINA_LIDAR_PORT", "/dev/ttyUSB0"),
        baudrate=int(os.environ.get("NINA_LIDAR_BAUD", "115200")),
    )

    # Default world size is now sized for the S2E (effective ~25 m
    # indoors). With a 12 m square world at 1000 px we get
    # 12 mm/px - still above the wall-resolution floor (15 mm/px,
    # see test_slam_resolution_fine_enough_for_walls) and gives a
    # meaningful map of an 8 m hallway without flooding RAM. The
    # earlier A1-era default of 8 m / 800 px was chosen because the
    # A1's 6 m range left a 20 m world looking empty; the S2E sees
    # walls at 25 m so we let the world grow.
    if lidar.model == "a1":
        # Legacy A1 path: keep the proven 8 m / 800 px sizing - the
        # A1 won't fill a bigger world anyway.
        slam_pixels_default = "800"
        slam_meters_default = "8"
        slam_max_range_mm_default = "12000"
        slam_scan_size_default = "360"
        slam_scan_rate_default = "5.5"
    else:
        # S2E (or auto - same defaults). 12 mm/px keeps walls > 1
        # pixel after letterboxing into the Perception card.
        slam_pixels_default = "1000"
        slam_meters_default = "12"
        # The S2E's published max is 30 m but multipath inside small
        # rooms makes returns past ~28 m unreliable; clip there.
        slam_max_range_mm_default = "28000"
        # 400 samples/rev is the S2E's typical compressed-mode output
        # at 10 Hz. BreezySLAM resamples internally, so this is
        # effectively a hint about angular resolution.
        slam_scan_size_default = "400"
        slam_scan_rate_default = "10"

    slam = SlamSettings(
        map_size_pixels=int(os.environ.get("NINA_SLAM_PIXELS", slam_pixels_default)),
        map_size_meters=float(os.environ.get("NINA_SLAM_METERS", slam_meters_default)),
        update_hz=float(os.environ.get("NINA_SLAM_HZ", "5")),
        hole_width_mm=int(os.environ.get("NINA_SLAM_HOLE_MM", "600")),
        random_seed=int(os.environ.get("NINA_SLAM_SEED", "0xdeadbeef"), 0),
        laser_max_range_mm=int(
            os.environ.get("NINA_SLAM_LASER_MAX_MM", slam_max_range_mm_default)
        ),
        laser_scan_size=int(
            os.environ.get("NINA_SLAM_LASER_SCAN_SIZE", slam_scan_size_default)
        ),
        laser_scan_rate_hz=float(
            os.environ.get("NINA_SLAM_LASER_SCAN_RATE_HZ", slam_scan_rate_default)
        ),
    )

    goto = GotoSettings(
        # 250 mm -> roughly Nina's chassis half-width. The pilot
        # transitions to 'arrived' once the bot is inside this radius
        # of the goal, which keeps it from oscillating on the spot
        # trying to land on the exact pixel the operator tapped.
        arrival_radius_mm=int(os.environ.get("NINA_GOTO_ARRIVAL_MM", "250")),
        # Footprint inflation: A* treats every wall pixel as
        # "wall + this many mm of buffer" so the planner never
        # picks a route the bot physically can't take. Default
        # 250 mm = ~half-body width. Bump for wider bots; tighter
        # safety margins are better expressed via
        # `min_passage_width_mm` below.
        footprint_radius_mm=int(os.environ.get("NINA_GOTO_INFLATE_MM", "250")),
        # Minimum corridor width (between facing walls) the planner
        # is allowed to route through. Default 610 mm = 24 in / 2 ft,
        # which is the smallest gap Nina is supposed to fit through
        # in the lab + corridor environments she's used in. Drive
        # this from operator policy ("I want 3 ft of buffer in the
        # showroom") rather than from the bot's geometry.
        min_passage_width_mm=int(
            os.environ.get("NINA_GOTO_MIN_PASSAGE_MM", "610")
        ),
        # Match the wander pilot's 15 % cruise so a goto handoff
        # doesn't change the bot's perceived "speed" mid-run.
        cruise_speed_pct=int(os.environ.get("NINA_GOTO_CRUISE_PCT", "15")),
        turn_speed_pct=int(os.environ.get("NINA_GOTO_TURN_PCT", "16")),
        # 18 deg deadband: inside this we drive forward (still with
        # a small heading correction); outside, we turn in place.
        # Wider than the wander pilot's implicit binary so we don't
        # flip into in-place spin during normal forward driving on a
        # noisy SLAM heading estimate.
        heading_deadband_deg=float(os.environ.get("NINA_GOTO_HEAD_DEG", "18.0")),
        # 600 mm pure-pursuit lookahead. Longer = smoother arc,
        # shorter = tighter follow but more wobble.
        lookahead_mm=int(os.environ.get("NINA_GOTO_LOOKAHEAD_MM", "600")),
        # Periodic replan even if everything looks fine - the SLAM
        # map keeps growing and the optimal path may shorten as
        # walls fill in.
        replan_period_sec=float(os.environ.get("NINA_GOTO_REPLAN_SEC", "3.0")),
        # 5 s window x 50 mm of motion = "the bot is genuinely stuck".
        # The wander pilot's reactive veto would otherwise mask a
        # locked-rotor / dead-driver scenario.
        stuck_window_sec=float(os.environ.get("NINA_GOTO_STUCK_SEC", "5.0")),
        stuck_motion_mm=int(os.environ.get("NINA_GOTO_STUCK_MM", "50")),
        # Same 8 Hz as the wander pilot's tick.
        tick_hz=float(os.environ.get("NINA_GOTO_TICK_HZ", "8")),
        # Unknown-grey pixels cost slightly more than known-free
        # ones in the planner, so A* will prefer mapped corridors
        # but still happily route into unmapped rooms when needed.
        unknown_pixel_cost=float(os.environ.get("NINA_GOTO_UNKNOWN_COST", "1.5")),
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
        lidar=lidar,
        goto=goto,
    )
