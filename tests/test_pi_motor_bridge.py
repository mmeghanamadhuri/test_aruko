"""
Hardware-free unit tests for the Pi-side motor bridge.

These cover the JYQD startup-kick logic that we added after observing
that the JYQD_V7.3E2 chips need a specific EL/PWM edge sequence before
they'll commutate from a stopped rotor. The tests run on any host
because we stub out `pigpio` (so `navigation_bldc` can import) and
sleep (so the kick dwell doesn't make the suite slow).

Run with::

    PYTHONPATH=. python3 -m pytest tests/test_pi_motor_bridge.py -v
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Any, List, Tuple

import pytest


# ---------------------------------------------------------------------
# Fake pigpio shim
# ---------------------------------------------------------------------


class _FakePi:
    """Records every set_mode/write/hardware_PWM call in order so tests
    can assert on the exact GPIO sequence the bridge produced."""

    def __init__(self) -> None:
        self.connected = True
        self.calls: List[Tuple[str, Any, ...]] = []

    def set_mode(self, pin: int, mode: int) -> None:
        self.calls.append(("set_mode", pin, mode))

    def write(self, pin: int, level: int) -> None:
        self.calls.append(("write", pin, level))

    def hardware_PWM(self, pin: int, freq: int, duty: int) -> None:
        self.calls.append(("hardware_PWM", pin, freq, duty))

    def stop(self) -> None:
        self.calls.append(("stop",))
        self.connected = False


def _install_fake_pigpio(monkeypatch: pytest.MonkeyPatch) -> _FakePi:
    """Insert a `pigpio` module into sys.modules whose `pi()` constructor
    returns the same `_FakePi` instance every time. Returns that instance
    so tests can inspect `instance.calls`."""
    fake_pi_instance = _FakePi()

    fake_module = types.ModuleType("pigpio")
    fake_module.OUTPUT = 1  # type: ignore[attr-defined]
    fake_module.INPUT = 0  # type: ignore[attr-defined]
    fake_module.pi = lambda: fake_pi_instance  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pigpio", fake_module)

    return fake_pi_instance


# ---------------------------------------------------------------------
# Fixtures: import navigation_bldc against the fake pigpio, then patch
# time.sleep so kick_and_set returns instantly.
# ---------------------------------------------------------------------


_PI_BRIDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pi_motor_bridge",
)


@pytest.fixture
def nav_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Yield a fresh `navigation_bldc` module wired up to the fake pigpio."""
    fake_pi = _install_fake_pigpio(monkeypatch)
    monkeypatch.syspath_prepend(_PI_BRIDGE_DIR)

    if "navigation_bldc" in sys.modules:
        del sys.modules["navigation_bldc"]
    nav = importlib.import_module("navigation_bldc")

    # Make sleep a no-op so the kick dwell doesn't slow the suite.
    monkeypatch.setattr(nav.time, "sleep", lambda _seconds: None)

    nav._test_fake_pi = fake_pi  # stash for tests to read
    yield nav

    # Reset module-level state so tests can't leak the connection.
    nav.object_pi = None


# ---------------------------------------------------------------------
# navigation_bldc.kick_and_set
# ---------------------------------------------------------------------


def _filter(calls: List[Tuple], op: str, pin: int) -> List[Tuple]:
    return [c for c in calls if c[0] == op and c[1] == pin]


def test_setup_gpio_configures_sig_pins_as_input(nav_module: Any) -> None:
    nav = nav_module
    assert nav.setup_gpio() is True

    fake = nav._test_fake_pi
    set_modes = [c for c in fake.calls if c[0] == "set_mode"]
    # OLD prototype mirror: BCM 24 / 27 must be INPUT.
    assert ("set_mode", nav.L_SIG, 0) in set_modes
    assert ("set_mode", nav.R_SIG, 0) in set_modes


def test_kick_and_set_emits_falling_then_rising_edge(nav_module: Any) -> None:
    """The whole point of kick_and_set: EL goes HIGH->LOW->HIGH and PWM
    goes 0->KICK->0->target on each kicked wheel."""
    nav = nav_module
    assert nav.setup_gpio() is True

    fake = nav._test_fake_pi
    fake.calls.clear()  # ignore setup_gpio's parking writes

    nav.kick_and_set(40, "front", 40, "front")

    # Left EL (BCM 18): HIGH (warm), LOW (falling), HIGH (target)
    l_en_writes = [c[2] for c in _filter(fake.calls, "write", nav.L_EN)]
    assert l_en_writes == [1, 0, 1], f"L_EN sequence wrong: {l_en_writes}"

    # Right EL (BCM 10): same pattern
    r_en_writes = [c[2] for c in _filter(fake.calls, "write", nav.R_EN)]
    assert r_en_writes == [1, 0, 1], f"R_EN sequence wrong: {r_en_writes}"

    # Left PWM (BCM 12): KICK_PWM_PERCENT * 10000, 0, 40 * 10000
    l_pwm_writes = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_L)]
    assert l_pwm_writes == [
        nav.KICK_PWM_PERCENT * 10000,
        0,
        40 * 10000,
    ], f"L PWM sequence wrong: {l_pwm_writes}"

    # Right PWM same pattern
    r_pwm_writes = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_R)]
    assert r_pwm_writes == [
        nav.KICK_PWM_PERCENT * 10000,
        0,
        40 * 10000,
    ], f"R PWM sequence wrong: {r_pwm_writes}"


def test_kick_and_set_skips_warmup_for_zero_speed_wheel(nav_module: Any) -> None:
    """If a wheel is being commanded to PWM=0 it should NOT twitch
    during the kick - only the kicked wheel sees the warm-up pulse."""
    nav = nav_module
    assert nav.setup_gpio() is True

    fake = nav._test_fake_pi
    fake.calls.clear()

    # Left: spin at 30%, Right: stay still.
    nav.kick_and_set(30, "front", 0, "front")

    # Left wheel goes through the full kick.
    l_pwm_writes = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_L)]
    assert l_pwm_writes == [nav.KICK_PWM_PERCENT * 10000, 0, 30 * 10000]

    # Right wheel: only the final write (which lands at 0).
    r_pwm_writes = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_R)]
    assert r_pwm_writes == [0], (
        f"Right wheel should not have warm-up/falling pulses, got {r_pwm_writes}"
    )

    # Left EL still cycles HIGH->LOW->HIGH.
    l_en_writes = [c[2] for c in _filter(fake.calls, "write", nav.L_EN)]
    assert l_en_writes == [1, 0, 1]

    # Right EL only gets the final HIGH (control_speed("right","enable",0,...)).
    r_en_writes = [c[2] for c in _filter(fake.calls, "write", nav.R_EN)]
    assert r_en_writes == [1]


def test_kick_and_set_handles_both_wheels_zero(nav_module: Any) -> None:
    """A 0/0 kick should be a no-op kick (no edges) but still write the
    final 0 to both wheels so they end up in a known state."""
    nav = nav_module
    assert nav.setup_gpio() is True

    fake = nav._test_fake_pi
    fake.calls.clear()

    nav.kick_and_set(0, "front", 0, "front")

    l_pwm_writes = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_L)]
    r_pwm_writes = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_R)]
    assert l_pwm_writes == [0]
    assert r_pwm_writes == [0]


# ---------------------------------------------------------------------
# navigation_bldc.warm_reverse_and_set
# ---------------------------------------------------------------------


def test_warm_reverse_pure_forward_is_identical_to_kick(nav_module: Any) -> None:
    """A pure-forward warm_reverse_and_set must produce exactly the same
    GPIO calls as kick_and_set - no puff, no extra cost."""
    nav = nav_module
    assert nav.setup_gpio() is True
    fake = nav._test_fake_pi

    fake.calls.clear()
    nav.kick_and_set(40, "front", 40, "front")
    kick_calls = list(fake.calls)

    fake.calls.clear()
    nav.warm_reverse_and_set(40, "front", 40, "front")
    warm_calls = list(fake.calls)

    assert kick_calls == warm_calls, (
        "warm_reverse_and_set must be a no-op puff for pure-forward "
        f"commands.\nkick:\n{kick_calls}\nwarm:\n{warm_calls}"
    )


def test_warm_reverse_both_wheels_puffs_forward_then_kicks_reverse(
    nav_module: Any,
) -> None:
    """For a reverse SET on both wheels, both wheels see:
        1. forward puff (DIR=front, PWM=PUFF_PWM_PERCENT)
        2. coast      (DIR=front, PWM=0)
        3. kick       (DIR=back, PWM warm/0/target)
    """
    nav = nav_module
    assert nav.setup_gpio() is True
    fake = nav._test_fake_pi
    fake.calls.clear()

    nav.warm_reverse_and_set(40, "back", 40, "back")

    # Left PWM sequence: PUFF, 0 (coast), KICK warm, 0 (kick fall), 40 (target)
    l_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_L)]
    assert l_pwm == [
        nav.PUFF_PWM_PERCENT * 10000,
        0,
        nav.KICK_PWM_PERCENT * 10000,
        0,
        40 * 10000,
    ], f"L PWM sequence wrong: {l_pwm}"

    r_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_R)]
    assert r_pwm == l_pwm, f"R PWM should mirror L: {r_pwm}"

    # Left DIR (BCM 25): front during puff/coast, back during kick.
    l_dir_writes = [c[2] for c in _filter(fake.calls, "write", nav.L_DIR)]
    # 1=front (puff), 1=front (coast), 0=back (kick warm),
    # 0=back (kick fall), 0=back (kick rise)
    assert l_dir_writes == [1, 1, 0, 0, 0], f"L DIR sequence wrong: {l_dir_writes}"

    # Right DIR (BCM 22): mirrored - front=0, back=1.
    r_dir_writes = [c[2] for c in _filter(fake.calls, "write", nav.R_DIR)]
    assert r_dir_writes == [0, 0, 1, 1, 1], f"R DIR sequence wrong: {r_dir_writes}"


def test_warm_reverse_mixed_only_puffs_the_reverse_wheel(nav_module: Any) -> None:
    """left=back, right=front: ONLY left should puff. Right goes
    straight into the standard kick."""
    nav = nav_module
    assert nav.setup_gpio() is True
    fake = nav._test_fake_pi
    fake.calls.clear()

    nav.warm_reverse_and_set(30, "back", 30, "front")

    # Left: PUFF -> 0 -> KICK_warm -> 0 -> 30
    l_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_L)]
    assert l_pwm == [
        nav.PUFF_PWM_PERCENT * 10000,
        0,
        nav.KICK_PWM_PERCENT * 10000,
        0,
        30 * 10000,
    ]

    # Right: NO puff. Just KICK_warm -> 0 -> 30.
    r_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_R)]
    assert r_pwm == [
        nav.KICK_PWM_PERCENT * 10000,
        0,
        30 * 10000,
    ], f"Right wheel should not have puff pulses, got {r_pwm}"


def test_warm_reverse_zero_speed_wheel_skips_puff(nav_module: Any) -> None:
    """A wheel commanded to PWM=0 must NEVER twitch - not during the
    puff and not during the kick warm-up."""
    nav = nav_module
    assert nav.setup_gpio() is True
    fake = nav._test_fake_pi
    fake.calls.clear()

    # Left: reverse 30. Right: hold still.
    nav.warm_reverse_and_set(30, "back", 0, "back")

    # Right wheel: only the final write (lands at 0). No puff, no kick.
    r_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_R)]
    assert r_pwm == [0], (
        f"Right wheel commanded to 0 must not move during the puff: {r_pwm}"
    )

    # Left wheel: full puff + kick.
    l_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav.PWM_L)]
    assert l_pwm == [
        nav.PUFF_PWM_PERCENT * 10000,
        0,
        nav.KICK_PWM_PERCENT * 10000,
        0,
        30 * 10000,
    ]


def test_bridge_set_reverse_uses_warm_reverse(
    nav_module: Any, bridge_module: Any
) -> None:
    """A SET that asks for reverse from a stopped state must route
    through warm_reverse_and_set, not bare kick_and_set - we observe
    the puff PWM write that only the warm-reverse path produces."""
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi
    fake.calls.clear()

    bridge._dispatch("SET B 40 B 40")

    # First PWM write on each wheel must be PUFF_PWM_PERCENT (the puff),
    # NOT KICK_PWM_PERCENT (the bare kick warm-up).
    l_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav_module.PWM_L)]
    assert l_pwm[0] == nav_module.PUFF_PWM_PERCENT * 10000, (
        f"reverse SET should puff first, but L PWM started with {l_pwm[0]}"
    )
    # And the final landing PWM must be the requested 40%.
    assert l_pwm[-1] == 40 * 10000


# ---------------------------------------------------------------------
# motor_bridge.MotorBridge.SET dispatch (transition detection)
# ---------------------------------------------------------------------


@pytest.fixture
def bridge_module(monkeypatch: pytest.MonkeyPatch, nav_module: Any) -> Any:
    """Yield a fresh `motor_bridge` module sharing the fake pigpio
    that `nav_module` already installed."""
    # motor_bridge imports pyserial at top level; stub it.
    fake_serial_module = types.ModuleType("serial")
    fake_serial_module.Serial = lambda *a, **kw: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", fake_serial_module)

    if "motor_bridge" in sys.modules:
        del sys.modules["motor_bridge"]
    bridge_mod = importlib.import_module("motor_bridge")

    # Stub ensure_pigpiod so it doesn't shell out to pgrep on dev hosts.
    monkeypatch.setattr(bridge_mod, "ensure_pigpiod", lambda: True)

    yield bridge_mod


def _make_bridge(bridge_module: Any) -> Any:
    return bridge_module.MotorBridge(
        port="/dev/fake0",
        baud=115200,
        watchdog_timeout_sec=10.0,
    )


def _kick_count(nav_module: Any, fake_calls: List[Tuple]) -> int:
    """Count the number of full kicks issued: each kick produces a
    HIGH->LOW->HIGH on L_EN, so we count low writes to L_EN."""
    return sum(
        1
        for c in fake_calls
        if c[0] == "write" and c[1] == nav_module.L_EN and c[2] == 0
    )


def test_first_set_after_init_triggers_kick(
    nav_module: Any, bridge_module: Any
) -> None:
    """A SET while the bridge thinks the wheels are stopped must kick."""
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi
    fake.calls.clear()

    response = bridge._dispatch("SET F 30 F 30")

    assert response == "OK"
    assert _kick_count(nav_module, fake.calls) == 1
    assert bridge._wheels_active is True
    assert bridge._last_ldir == "front"
    assert bridge._last_rdir == "front"


def test_steady_state_set_does_not_kick(
    nav_module: Any, bridge_module: Any
) -> None:
    """Once moving, a SET in the same direction should stream straight
    through to set_wheels with no kick."""
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi

    bridge._dispatch("SET F 30 F 30")  # kicks
    fake.calls.clear()

    bridge._dispatch("SET F 50 F 50")  # should be a steady-state nudge

    assert _kick_count(nav_module, fake.calls) == 0
    # PWM should land on 50%.
    l_pwm = [c[3] for c in _filter(fake.calls, "hardware_PWM", nav_module.PWM_L)]
    assert l_pwm == [50 * 10000]


def test_direction_change_triggers_kick(
    nav_module: Any, bridge_module: Any
) -> None:
    """Reversing either wheel must kick - the JYQD has to re-arm in the
    new commutation order."""
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi

    bridge._dispatch("SET F 30 F 30")
    fake.calls.clear()

    bridge._dispatch("SET B 30 F 30")  # left flips forward -> back

    assert _kick_count(nav_module, fake.calls) == 1


def test_set_after_stop_triggers_kick(
    nav_module: Any, bridge_module: Any
) -> None:
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi

    bridge._dispatch("SET F 30 F 30")  # kicks
    bridge._dispatch("STOP")
    fake.calls.clear()

    bridge._dispatch("SET F 30 F 30")  # rotor stopped, must kick again

    assert _kick_count(nav_module, fake.calls) == 1


def test_set_after_estop_triggers_kick(
    nav_module: Any, bridge_module: Any
) -> None:
    """ESTOP drops EL LOW; the next SET must kick to bring it back up
    even if the requested direction matches the previous SET."""
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi

    bridge._dispatch("SET F 30 F 30")
    bridge._dispatch("ESTOP")
    fake.calls.clear()

    bridge._dispatch("SET F 30 F 30")

    assert _kick_count(nav_module, fake.calls) == 1
    # ESTOP also clears the cached direction.
    # (After the SET, _last_ldir is repopulated; check during the SET.)
    assert bridge._last_ldir == "front"


def test_set_zero_zero_does_not_kick(
    nav_module: Any, bridge_module: Any
) -> None:
    """A SET that asks for both wheels at PWM 0 is essentially a STOP -
    no need to fire the kick path."""
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi
    fake.calls.clear()

    bridge._dispatch("SET F 0 F 0")

    assert _kick_count(nav_module, fake.calls) == 0
    assert bridge._wheels_active is False


def test_stop_command_resets_active_flag(
    nav_module: Any, bridge_module: Any
) -> None:
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)

    bridge._dispatch("SET F 30 F 30")
    assert bridge._wheels_active is True

    bridge._dispatch("STOP")
    assert bridge._wheels_active is False


def test_estop_command_clears_direction_cache(
    nav_module: Any, bridge_module: Any
) -> None:
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)

    bridge._dispatch("SET F 30 F 30")
    assert bridge._last_ldir == "front"
    assert bridge._last_rdir == "front"

    bridge._dispatch("ESTOP")
    assert bridge._wheels_active is False
    assert bridge._last_ldir is None
    assert bridge._last_rdir is None


def test_invalid_set_arity_returns_error(
    nav_module: Any, bridge_module: Any
) -> None:
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)

    response = bridge._dispatch("SET F 30 F")
    assert response.startswith("ERR usage")


def test_invalid_direction_letter_returns_error(
    nav_module: Any, bridge_module: Any
) -> None:
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)

    response = bridge._dispatch("SET X 30 F 30")
    assert "direction" in response


def test_speed_clamped_to_0_100(
    nav_module: Any, bridge_module: Any
) -> None:
    nav_module.setup_gpio()
    bridge = _make_bridge(bridge_module)
    fake = nav_module._test_fake_pi
    fake.calls.clear()

    bridge._dispatch("SET F 9999 F -50")

    # Final PWM writes should be 100% on left, 0% on right.
    l_pwm_writes = [
        c[3] for c in _filter(fake.calls, "hardware_PWM", nav_module.PWM_L)
    ]
    r_pwm_writes = [
        c[3] for c in _filter(fake.calls, "hardware_PWM", nav_module.PWM_R)
    ]
    # Last write for each is the steady-state target.
    assert l_pwm_writes[-1] == 100 * 10000
    assert r_pwm_writes[-1] == 0


# ---------------------------------------------------------------------
# motor_bridge watchdog enable/disable
# ---------------------------------------------------------------------


def test_watchdog_thread_started_when_timeout_positive(
    nav_module: Any, bridge_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A positive watchdog_timeout_sec must spawn the watchdog thread."""
    spawned: list = []

    class _RecordingThread:
        def __init__(self, *, target=None, name=None, daemon=None) -> None:
            spawned.append((name, target, daemon))
            self._target = target

        def start(self) -> None:
            spawned.append(("started", self._target.__name__))

    monkeypatch.setattr(bridge_module.threading, "Thread", _RecordingThread)

    bridge = bridge_module.MotorBridge(
        port="/dev/fake0", baud=115200, watchdog_timeout_sec=1.5
    )
    assert bridge._start_watchdog_thread() is True
    assert ("watchdog", bridge._watchdog_loop, True) in spawned
    assert ("started", "_watchdog_loop") in spawned


def test_watchdog_thread_skipped_when_timeout_zero(
    nav_module: Any, bridge_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watchdog_timeout_sec=0 must DISABLE the watchdog: no thread, no
    timer, no auto-stop. The wheels only stop on explicit STOP / ESTOP."""
    spawned: list = []
    monkeypatch.setattr(
        bridge_module.threading,
        "Thread",
        lambda **kw: spawned.append(kw) or pytest.fail("watchdog thread should not start"),
    )

    bridge = bridge_module.MotorBridge(
        port="/dev/fake0", baud=115200, watchdog_timeout_sec=0
    )
    assert bridge._start_watchdog_thread() is False
    assert spawned == []


def test_watchdog_thread_skipped_when_timeout_negative(
    nav_module: Any, bridge_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: a negative timeout (e.g. someone fat-fingered an env
    var) is treated identically to 0 - watchdog DISABLED, not 'fires
    instantly'."""
    monkeypatch.setattr(
        bridge_module.threading,
        "Thread",
        lambda **kw: pytest.fail("watchdog thread should not start"),
    )

    bridge = bridge_module.MotorBridge(
        port="/dev/fake0", baud=115200, watchdog_timeout_sec=-3.2
    )
    assert bridge._start_watchdog_thread() is False


def test_watchdog_disabled_set_and_long_silence_keeps_motors_running(
    nav_module: Any, bridge_module: Any
) -> None:
    """End-to-end: with the watchdog disabled, dispatching a SET and
    then sitting silent for a 'long' time (we fast-forward the clock)
    must NOT issue any soft_stop or EVT WATCHDOG. The bridge stays in
    'wheels_active' until told otherwise."""
    nav_module.setup_gpio()
    bridge = bridge_module.MotorBridge(
        port="/dev/fake0", baud=115200, watchdog_timeout_sec=0
    )
    bridge._send_line = lambda line: None  # no serial in tests

    bridge._dispatch("SET F 30 F 30")
    assert bridge._wheels_active is True

    fake = nav_module._test_fake_pi
    fake.calls.clear()

    # Simulate "Jetson went silent for 60 seconds" - in real life with
    # the watchdog enabled this would fire ~1.5s in. With it disabled,
    # no GPIO writes whatsoever should happen.
    bridge._last_cmd_time = bridge._last_cmd_time - 60.0
    # Even if someone called the loop body manually, the disabled
    # bridge has no thread. The wheels-active flag stays True until
    # an explicit STOP / ESTOP / SET 0 0 arrives.
    assert bridge._wheels_active is True
    assert fake.calls == []  # no soft_stop, no nothing
