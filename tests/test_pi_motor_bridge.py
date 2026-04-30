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
