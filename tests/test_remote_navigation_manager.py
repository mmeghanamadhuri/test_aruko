"""
Hardware-free unit tests for `nina.controllers.remote_navigation_manager`.

These exercise the wire-protocol invariants that have bitten us in the
field (PING handshake, async-event desync, reconnect throttling,
direction inversion, speed clamping) using a fake `pyserial`-shaped
stub instead of a real serial port. Run with::

    PYTHONPATH=. python3 -m pytest tests/test_remote_navigation_manager.py -v

Or just `pytest` from the repo root if a `pytest.ini` is added later.

Why a hand-rolled fake instead of `unittest.mock`?
  We need a single object that survives across multiple `Serial(...)`
  constructor calls (because `_ensure_port` reopens) AND that lets each
  test script the exact sequence of response lines the bridge would
  send. Easier to read as a deterministic queue.
"""
from __future__ import annotations

import sys
import time
import types
from collections import deque
from typing import Deque, List, Optional

import pytest

from nina.controllers.remote_navigation_manager import (
    RemoteNavigationConfig,
    RemoteNavigationManager,
)


# ---------------------------------------------------------------------
# Fake pyserial shim
# ---------------------------------------------------------------------


class _FakeSerial:
    """Minimal `serial.Serial`-shaped object backed by an in-memory
    response queue. Each test pre-loads `responses` with the lines
    the bridge would emit, in order; `readline()` pops one at a time.

    `writes` records every line the manager wrote, so tests can assert
    on the exact wire bytes (`SET F 30 F 30\\n` etc.).
    """

    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout: Optional[float] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout if timeout is not None else 0.0
        self.is_open = True
        self.writes: List[bytes] = []
        # Backed by the registry below so reopen() preserves history.
        registry = _FakeSerialRegistry.current
        assert registry is not None, "Test forgot to install the fake serial module"
        registry.opens.append(self)
        self.responses: Deque[bytes] = registry.responses
        # If the registry told us to fail this open, raise after recording.
        if registry.fail_opens > 0:
            registry.fail_opens -= 1
            self.is_open = False
            raise OSError(f"fake open failure for {port}")

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def readline(self) -> bytes:
        if self.responses:
            return self.responses.popleft()
        # Mimic pyserial's empty-on-timeout behaviour.
        return b""

    def close(self) -> None:
        self.is_open = False


class _FakeSerialRegistry:
    """Per-test container for the queued responses + opened-port log.

    Threaded as a class attribute so the `_FakeSerial` constructor can
    find it without the test having to inject anything into
    `RemoteNavigationManager`. The fixture below sets / clears
    `current` per test.
    """

    current: Optional["_FakeSerialRegistry"] = None

    def __init__(self) -> None:
        self.responses: Deque[bytes] = deque()
        self.opens: List[_FakeSerial] = []
        self.fail_opens: int = 0

    def queue(self, *lines: str) -> None:
        for ln in lines:
            self.responses.append((ln + "\n").encode("utf-8"))


@pytest.fixture
def fake_serial(monkeypatch: pytest.MonkeyPatch) -> _FakeSerialRegistry:
    """Install a fake `serial` module visible to the lazy import in
    `RemoteNavigationManager.initialize()`."""
    fake_module = types.ModuleType("serial")
    fake_module.Serial = _FakeSerial  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", fake_module)

    registry = _FakeSerialRegistry()
    _FakeSerialRegistry.current = registry
    try:
        yield registry
    finally:
        _FakeSerialRegistry.current = None


def _last_open(registry: _FakeSerialRegistry) -> _FakeSerial:
    assert registry.opens, "Manager never opened the serial port"
    return registry.opens[-1]


def _writes_as_strings(port: _FakeSerial) -> List[str]:
    return [w.decode("utf-8").rstrip("\n") for w in port.writes]


# ---------------------------------------------------------------------
# Initialise / PING handshake
# ---------------------------------------------------------------------


def test_initialize_succeeds_on_first_pong(fake_serial: _FakeSerialRegistry) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )

    nav.initialize()

    port = _last_open(fake_serial)
    assert port.port == "/dev/fake0"
    assert _writes_as_strings(port) == ["PING"]
    assert nav._is_initialized is True


def test_initialize_tolerates_boot_ready_before_pong(
    fake_serial: _FakeSerialRegistry,
) -> None:
    """A bridge that just booted may emit READY before answering PING."""
    fake_serial.queue("READY", "PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=1.0,
            response_timeout_sec=0.1,
        )
    )

    nav.initialize()

    assert nav._is_initialized is True


def test_initialize_raises_when_bridge_silent(
    fake_serial: _FakeSerialRegistry,
) -> None:
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.2,
            response_timeout_sec=0.05,
        )
    )

    with pytest.raises(RuntimeError, match="did not reply to PING"):
        nav.initialize()
    assert nav._is_initialized is False
    # Must close the port on failure so the next attempt starts clean.
    assert _last_open(fake_serial).is_open is False


# ---------------------------------------------------------------------
# Async event handling (`EVT WATCHDOG`, `READY`)
# ---------------------------------------------------------------------


def test_async_evt_watchdog_does_not_desync_response_stream(
    fake_serial: _FakeSerialRegistry,
) -> None:
    """The pre-fix bug: an unsolicited EVT line consumed the response
    slot of the next command, shifting every subsequent reply by one.
    With the filter, EVT lines are skipped and `OK` is still seen as
    the SET reply."""
    fake_serial.queue("PONG")  # initialize
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.2,
        )
    )
    nav.initialize()

    fake_serial.queue("EVT WATCHDOG", "OK")
    nav.set_wheels(left_dir="forward", left_speed=30, right_dir="forward", right_speed=30)

    port = _last_open(fake_serial)
    assert _writes_as_strings(port) == ["PING", "SET F 30 F 30"]
    assert not fake_serial.responses, "EVT and OK should both have been consumed"


def test_async_ready_after_reboot_is_skipped_mid_session(
    fake_serial: _FakeSerialRegistry,
) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.2,
        )
    )
    nav.initialize()

    fake_serial.queue("READY", "OK")
    nav.stop()

    port = _last_open(fake_serial)
    assert _writes_as_strings(port) == ["PING", "STOP"]


def test_err_response_returns_false_and_logs(
    fake_serial: _FakeSerialRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )
    nav.initialize()

    fake_serial.queue("ERR speed must be int 0..100")
    caplog.clear()
    ok = nav._send_command("SET F bogus F 30")

    assert ok is False
    assert any("ERR speed" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------
# SET command shape: direction letters, polarity inversion, speed clamp
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "ldir,rdir,expected",
    [
        ("forward", "forward", "SET F 30 F 30"),
        ("forward", "backward", "SET F 30 B 30"),
        ("backward", "forward", "SET B 30 F 30"),
        ("backward", "backward", "SET B 30 B 30"),
    ],
)
def test_set_wheels_emits_correct_direction_letters(
    fake_serial: _FakeSerialRegistry, ldir: str, rdir: str, expected: str,
) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )
    nav.initialize()

    fake_serial.queue("OK")
    nav.set_wheels(left_dir=ldir, left_speed=30, right_dir=rdir, right_speed=30)

    port = _last_open(fake_serial)
    assert _writes_as_strings(port)[-1] == expected


def test_invert_left_flips_only_left_letter(fake_serial: _FakeSerialRegistry) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
            invert_left_dir=True,
        )
    )
    nav.initialize()

    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=20, right_dir="forward", right_speed=20)

    port = _last_open(fake_serial)
    # Forward -> B for left only; right side unchanged.
    assert _writes_as_strings(port)[-1] == "SET B 20 F 20"


def test_set_invert_left_runtime_override_flips_next_set(
    fake_serial: _FakeSerialRegistry,
) -> None:
    """The Drive screen's Flip L toggle calls set_invert_left() at
    runtime - the very next SET must reflect the flip even though the
    frozen RemoteNavigationConfig still says invert_left_dir=False."""
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
            invert_left_dir=False,
        )
    )
    nav.initialize()

    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=20, right_dir="forward", right_speed=20)
    port = _last_open(fake_serial)
    assert _writes_as_strings(port)[-1] == "SET F 20 F 20"

    nav.set_invert_left(True)
    assert nav.get_invert_left() is True

    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=20, right_dir="forward", right_speed=20)
    assert _writes_as_strings(port)[-1] == "SET B 20 F 20"

    nav.set_invert_left(False)
    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=20, right_dir="forward", right_speed=20)
    assert _writes_as_strings(port)[-1] == "SET F 20 F 20"


def test_set_invert_right_runtime_override_flips_next_set(
    fake_serial: _FakeSerialRegistry,
) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )
    nav.initialize()

    nav.set_invert_right(True)
    assert nav.get_invert_right() is True

    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=10, right_dir="forward", right_speed=10)
    port = _last_open(fake_serial)
    assert _writes_as_strings(port)[-1] == "SET F 10 B 10"


def test_runtime_invert_override_wins_over_frozen_config(
    fake_serial: _FakeSerialRegistry,
) -> None:
    """A runtime False must override a config-time True. This matters
    because the kiosk service still ships INVERT_LEFT=1, but if the
    operator clicks Flip L OFF on the GUI we have to honour them."""
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
            invert_left_dir=True,  # boot-time / env-var seeded
        )
    )
    nav.initialize()

    nav.set_invert_left(False)  # operator turns it off in the GUI

    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=20, right_dir="forward", right_speed=20)
    port = _last_open(fake_serial)
    # Override wins: no flip even though the config says True.
    assert _writes_as_strings(port)[-1] == "SET F 20 F 20"


def test_speed_is_clamped_to_0_100(fake_serial: _FakeSerialRegistry) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )
    nav.initialize()

    fake_serial.queue("OK")
    nav.set_wheels(left_dir="forward", left_speed=999, right_dir="backward", right_speed=-50)

    port = _last_open(fake_serial)
    assert _writes_as_strings(port)[-1] == "SET F 100 B 0"


def test_set_wheels_start_kick_from_rest_issues_boost_then_target(
    fake_serial: _FakeSerialRegistry,
) -> None:
    """Breakaway kick: two SET lines when configured and both sides were at rest."""
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
            start_kick_percent=40,
            start_kick_sec=0.01,
        )
    )
    nav.initialize()
    fake_serial.queue("OK", "OK")
    nav.set_wheels(
        left_dir="forward", left_speed=15, right_dir="forward", right_speed=15,
    )
    port = _last_open(fake_serial)
    lines = [ln for ln in _writes_as_strings(port) if ln.startswith("SET ")]
    assert lines == ["SET F 40 F 40", "SET F 15 F 15"]

    fake_serial.queue("OK")
    nav.set_wheels(
        left_dir="forward", left_speed=15, right_dir="forward", right_speed=15,
    )
    lines2 = [ln for ln in _writes_as_strings(port) if ln.startswith("SET ")]
    assert lines2[-1] == "SET F 15 F 15"
    assert lines2.count("SET F 15 F 15") >= 2
    # Second call from motion: no boost SET before the final line.
    assert "SET F 40 F 40" not in lines2[-2:]


def test_invalid_direction_raises(fake_serial: _FakeSerialRegistry) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )
    nav.initialize()

    with pytest.raises(ValueError):
        nav.set_wheels(left_dir="sideways", left_speed=10, right_dir="forward", right_speed=10)


# ---------------------------------------------------------------------
# Stop / ESTOP / LED
# ---------------------------------------------------------------------


def test_stop_emergency_stop_and_led_emit_correct_lines(
    fake_serial: _FakeSerialRegistry,
) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.1,
        )
    )
    nav.initialize()

    fake_serial.queue("OK", "OK", "OK", "OK")
    nav.stop()
    nav.emergency_stop()
    nav.set_status("CONNECTED")
    nav.set_status("bogus")  # falls back to OFF, still one line on the wire

    port = _last_open(fake_serial)
    assert _writes_as_strings(port)[1:] == [
        "STOP",
        "ESTOP",
        "LED CONNECTED",
        "LED OFF",
    ]


# ---------------------------------------------------------------------
# Reconnect throttling
# ---------------------------------------------------------------------


def test_reconnect_is_throttled_when_pi_is_dead(
    fake_serial: _FakeSerialRegistry,
) -> None:
    """After a serial I/O failure closes the port, `_ensure_port`
    should reopen at most once per `reconnect_min_interval_sec`. Burst
    callers must NOT re-open on every command, which previously made
    the GUI's drive_continuous tick busy-loop on a dead Pi."""
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.05,
            reconnect_min_interval_sec=10.0,  # huge: definitely throttled
        )
    )
    nav.initialize()
    initial_open_count = len(fake_serial.opens)

    nav._close_port()
    fake_serial.fail_opens = 99  # every reopen attempt errors out

    # Burst of would-be commands: should attempt to open exactly once,
    # then short-circuit on the throttle for every subsequent call.
    for _ in range(10):
        ok = nav._send_command("STOP")
        assert ok is False

    new_opens = len(fake_serial.opens) - initial_open_count
    assert new_opens == 1, (
        f"expected exactly one reopen attempt under throttle, got {new_opens}"
    )


def test_reconnect_succeeds_after_cooldown_elapses(
    fake_serial: _FakeSerialRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_serial.queue("PONG")
    nav = RemoteNavigationManager(
        RemoteNavigationConfig(
            serial_port="/dev/fake0",
            connect_timeout_sec=0.5,
            response_timeout_sec=0.05,
            reconnect_min_interval_sec=0.5,
        )
    )
    nav.initialize()

    # Force a disconnect, fail one reopen attempt to arm the throttle.
    nav._close_port()
    fake_serial.fail_opens = 1
    assert nav._send_command("STOP") is False

    # Pretend the cooldown has passed; next attempt should succeed.
    monkeypatch.setattr(
        nav,
        "_last_reconnect_failure_ts",
        time.monotonic() - 1.0,
    )
    fake_serial.queue("OK")
    assert nav._send_command("STOP") is True
