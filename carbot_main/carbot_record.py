"""
carbot_record.py
----------------
Record and play back servo positions for 7 MX-28 servos (IDs 1-7).

Servo IDs 1-5  →  absolute goal-position recorded and replayed directly.
Servo IDs 6-7  →  difference magnitude recorded; user assigns +/- sign at
                  record time; playback reads the live present position and
                  applies  present ± diff  as the new goal — identical to the
                  'p' (relative move) command in mx28_rw.py.

Per-frame settings
------------------
  delay    — seconds to wait before sending commands for this frame
  duration — seconds to hold the position before advancing to the next frame
  speed    — servo moving-speed (0-1023;  0 = maximum speed)

Menu
----
  r  — record a new frame  (snapshot current positions)
  e  — edit a frame
  p  — play back all frames (once)
  o  — loop all frames continuously (Ctrl+C to stop)
  l  — list all frames
  s  — save recording to a JSON file
  d  — load recording from a JSON file
  c  — clear all frames
  q  — quit

Usage
-----
  python carbot_record.py                         # auto-detect port (Windows & Pi)
  python carbot_record.py --port COM3             # Windows
  python carbot_record.py --port /dev/ttyUSB0     # Raspberry Pi
"""

import serial
import serial.tools.list_ports
import time
import argparse
import sys
import json
import platform
import threading
from typing import Optional, List, Dict, Any
from pathlib import Path

from rich.console import Console
from rich.table   import Table
from rich.live    import Live
from rich.text    import Text
from rich.padding import Padding

console = Console()

try:
    from actuator import LinearActuator
    global_arm = LinearActuator(in3_pin=35, in4_pin=37)
except ImportError:
    global_arm = None
except Exception as e:
    console.print(f"[red]Failed to initialize Linear Actuator: {e}[/red]")
    global_arm = None

# ── Platform detection ────────────────────────────────────────────────────────
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"   # covers Raspberry Pi

# ── Servo configuration ───────────────────────────────────────────────────────
SERVO_IDS = [1, 2, 3, 4, 5, 6, 7]
ABS_IDS   = [1, 2, 3, 4, 5]   # absolute goal-position playback
REL_IDS   = [6, 7]            # relative-offset playback
BAUDRATE  = 222222

# ── Protocol constants ────────────────────────────────────────────────────────
HEADER     = 0xFF
PING       = 0x01
READ_DATA  = 0x02
WRITE_DATA = 0x03

# ── Register (address, size) pairs ───────────────────────────────────────────
REG_TORQUE_ENABLE = (24, 1)
REG_GOAL_POSITION = (30, 2)
REG_MOVING_SPEED  = (32, 2)
REG_PRESENT_POS   = (36, 2)

# ── Protocol helpers (mirrors mx28_rw.py) ─────────────────────────────────────

def _s16(v: int) -> int:
    """Unsigned 16-bit → signed 16-bit."""
    return v if v < 32768 else v - 65536


def _u16(v: int) -> int:
    """Signed or wrapped int → unsigned 16-bit (0-65535)."""
    return v & 0xFFFF


def _checksum(pkt: List[int]) -> int:
    return (~sum(pkt[2:])) & 0xFF


def _robust_clear(ser: serial.Serial) -> None:
    """Double-drain: lets USB FIFO flush to PC buffer, then purges twice."""
    ser.reset_input_buffer()
    time.sleep(0.005)  # Optimized for vision-guided tracking (was 20ms)
    ser.reset_input_buffer()


def _build(servo_id: int, instr: int, params: List[int] = None) -> bytes:
    params = params or []
    pkt = [HEADER, HEADER, servo_id, 2 + len(params), instr] + params
    pkt.append(_checksum(pkt))
    return bytes(pkt)


def _recv(ser: serial.Serial, servo_id: int, timeout: float = 0.1):
    deadline = time.time() + timeout
    buf = bytearray()
    while time.time() < deadline:
        if ser.in_waiting:
            buf.extend(ser.read(ser.in_waiting))
            if len(buf) >= 6:
                for i in range(len(buf) - 1):
                    if buf[i] == HEADER and buf[i + 1] == HEADER:
                        chunk = buf[i:]
                        if len(chunk) >= 4:
                            length = chunk[3]
                            if len(chunk) >= 4 + length:
                                full = chunk[: 4 + length]
                                if _checksum(full[:-1]) == full[-1] and full[2] == servo_id:
                                    return full[4], list(full[5:-1])
        time.sleep(0.001)
    return None


def ping(ser: serial.Serial, sid: int) -> bool:
    """Send a PING and return True if the servo responds."""
    pkt = _build(sid, PING)
    _robust_clear(ser)
    ser.write(pkt)
    ser.flush()
    time.sleep(max(0.003, len(pkt) * 10.0 / BAUDRATE))
    return _recv(ser, sid, timeout=0.20) is not None


def read_reg(ser, sid: int, addr: int, size: int) -> Optional[int]:
    pkt = _build(sid, READ_DATA, [addr, size])
    
    # Try multiple times to handle transient bus glitches on Jetson
    for attempt in range(2):
        _robust_clear(ser)
        ser.write(pkt)
        ser.flush()
        time.sleep(max(0.002, len(pkt) * 10.0 / BAUDRATE))
        resp = _recv(ser, sid, timeout=0.015)  # Snappy timeout for async polling
        
        if resp is not None and len(resp[1]) >= size:
            d = resp[1]
            return d[0] if size == 1 else (d[0] | (d[1] << 8))
        
        if attempt == 0:
            time.sleep(0.002)  # Faster retry gap
            
    return None


def write_reg(ser, sid: int, addr: int, size: int, value: int) -> bool:
    value  = int(value) & (0xFF if size == 1 else 0xFFFF)
    params = [addr, value & 0xFF] if size == 1 else [addr, value & 0xFF, (value >> 8) & 0xFF]
    pkt    = _build(sid, WRITE_DATA, params)
    ser.reset_input_buffer()
    ser.write(pkt)
    ser.flush()
    time.sleep(max(0.005, len(pkt) * 10.0 / BAUDRATE))
    _recv(ser, sid, timeout=0.02)   # consume status packet — was 0.08; servo responds in <2ms at 222kbaud
    return True

def wait_interruptible(duration: float, stop_flag: Optional[threading.Event] = None) -> bool:
    """Sleeps for duration seconds in small chunks. Returns False if interrupted by stop_flag."""
    if duration <= 0:
        return True
    if stop_flag is None:
        time.sleep(duration)
        return True
    
    start = time.time()
    while time.time() - start < duration:
        if stop_flag.is_set():
            return False
        time.sleep(0.005)
    return True

# ── Position snapshot ─────────────────────────────────────────────────────────

def read_positions(ser) -> Dict[int, Dict[str, Optional[int]]]:
    """Read goal position and present position for every servo."""
    result = {}
    for sid in SERVO_IDS:
        goal    = read_reg(ser, sid, *REG_GOAL_POSITION)
        present = read_reg(ser, sid, *REG_PRESENT_POS)
        result[sid] = {"goal": goal, "present": present}
    return result


def print_positions_table(positions: Dict[int, Dict[str, Optional[int]]]) -> None:
    """Display a Rich table showing goal and present positions for all servos."""
    tbl = Table(
        title       = "Current Servo Positions",
        border_style= "bright_black",
        header_style= "bold white",
        show_lines  = True,
    )
    tbl.add_column("ID",            justify="center", style="cyan",   width=4)
    tbl.add_column("Goal (raw)",    justify="right",  style="yellow", width=12)
    tbl.add_column("Goal (°)",      justify="right",                  width=12)
    tbl.add_column("Present (raw)", justify="right",  style="green",  width=14)
    tbl.add_column("Present (°)",   justify="right",                  width=12)
    tbl.add_column("Mode",          justify="center",                 width=10)

    for sid in SERVO_IDS:
        goal    = positions[sid]["goal"]
        present = positions[sid]["present"]

        g_raw = str(goal)    if goal    is not None else "[red]ERR[/red]"
        p_raw = str(present) if present is not None else "[red]ERR[/red]"
        g_deg = f"{_s16(goal)*0.088:+.2f}°"    if goal    is not None else "—"
        p_deg = f"{_s16(present)*0.088:+.2f}°" if present is not None else "—"

        mode = "[magenta]relative[/magenta]" if sid in REL_IDS else "[cyan]absolute[/cyan]"
        tbl.add_row(str(sid), g_raw, g_deg, p_raw, p_deg, mode)

    console.print(tbl)

# ── Frame helpers ─────────────────────────────────────────────────────────────
# Frame structure:
# {
#   "delay":    float,          # seconds before this frame
#   "duration": float,          # seconds to hold after sending
#   "speed":    int,            # 0-1023
#   "servos": {
#       1: {"type": "absolute", "value": int},
#       ...
#       6: {"type": "relative", "diff": int, "sign": "+" | "-"},
#       7: {"type": "relative", "diff": int, "sign": "+" | "-"},
#   }
# }

def _ask_float(prompt: str, default: float) -> float:
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        console.print(f"  [red]Invalid, using default {default}.[/red]")
        return default


def _ask_int(prompt: str, default: int, lo: int = None, hi: int = None) -> int:
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        v = int(raw)
        if lo is not None and hi is not None:
            v = max(lo, min(hi, v))
        return v
    except ValueError:
        console.print(f"  [red]Invalid, using default {default}.[/red]")
        return default


def _deg_to_raw(deg: float) -> int:
    """Convert degrees to unsigned 16-bit raw count (MX-28: 1 count = 0.088°)."""
    counts = round(deg / 0.088)
    counts = max(-32768, min(32767, counts))
    return counts & 0xFFFF


def _ask_abs_value(sid: int, current: Optional[int]) -> Optional[int]:
    """
    Ask the user how to set the absolute goal position for a servo.
    Returns a raw 16-bit int, or None if kept unchanged (or unreadable).

    Input modes
    -----------
      Enter      — keep the current snapshot value
      r <value>  — raw count  (e.g.  r 2048)
      d <value>  — degrees    (e.g.  d +90.5   or   d -180)
    """
    cur_deg = f"{_s16(current)*0.088:+.2f}°" if current is not None else "—"
    cur_str = f"{current} = {cur_deg}" if current is not None else "unreadable"

    console.print(
        f"  Servo [cyan]{sid}[/cyan]  snapshot=[yellow]{cur_str}[/yellow]  "
        f"→  Enter to keep,  [bold]r[/bold] <raw>  or  [bold]d[/bold] <degrees>"
    )
    raw_in = input(f"    S{sid}: ").strip()

    if not raw_in:
        return current                          # keep snapshot

    parts = raw_in.split(None, 1)
    mode  = parts[0].lower()

    if mode == "r":
        try:
            return int(parts[1]) & 0xFFFF
        except (IndexError, ValueError):
            console.print("  [red]  Usage:  r <integer>  e.g.  r 2048[/red]")
            return current

    if mode == "d":
        try:
            raw_val = _deg_to_raw(float(parts[1]))
            deg_back = _s16(raw_val) * 0.088
            console.print(f"  [dim]  {float(parts[1]):+.2f}° → raw {raw_val}  ({deg_back:+.2f}°)[/dim]")
            return raw_val
        except (IndexError, ValueError):
            console.print("  [red]  Usage:  d <degrees>  e.g.  d +90.5[/red]")
            return current

    console.print("  [red]  Unknown mode. Use  r <raw>  or  d <degrees>  or Enter to keep.[/red]")
    return current

# ── Record ────────────────────────────────────────────────────────────────────

def record_frame(ser, frames: List[Dict]) -> None:
    console.print("\n[bold]Recording frame — reading all servos...[/bold]")
    positions = read_positions(ser)
    print_positions_table(positions)

    frame: Dict[str, Any] = {"delay": 0.5, "duration": 1.0, "speed": 200, "servos": {}}

    # ── Absolute servos (IDs 1-5) ─────────────────────────────────────────────
    console.print("\n  [bold]Servos 1-5[/bold]  (absolute goal position)")
    console.print("  [dim]Enter to keep snapshot · r <raw>  · d <degrees>[/dim]\n")
    for sid in ABS_IDS:
        goal = positions[sid]["goal"]
        value = _ask_abs_value(sid, goal)
        frame["servos"][sid] = {"type": "absolute", "value": value}

    # ── Relative servos (IDs 6-7) ─────────────────────────────────────────────
    console.print("\n  [bold]Servos 6-7[/bold]  (relative offset from previous frame)")

    for sid in REL_IDS:
        present = positions[sid]["present"]

        if present is None:
            console.print(f"  [red]Servo {sid}: present position unreadable — storing 0 offset.[/red]")
            frame["servos"][sid] = {"type": "relative", "diff": 0, "sign": "+", "ref_pos": None}
            continue

        present_s16 = _s16(present)

        # Find the ref_pos from the last recorded frame that has this servo
        prev_ref = None
        for prev_frame in reversed(frames):
            prev_sv = prev_frame["servos"].get(sid)
            if prev_sv is not None and prev_sv.get("ref_pos") is not None:
                prev_ref = prev_sv["ref_pos"]
                break

        if prev_ref is None:
            # ── First frame for this servo: no previous reference ─────────────
            console.print(
                f"\n  [bold cyan]Servo {sid}[/bold cyan] — "
                f"[dim]no previous frame — this is the starting reference[/dim]  "
                f"present=[green]{present_s16}[/green]  diff=[magenta]0[/magenta]"
            )
            console.print(f"  [dim]  Servo {sid} will use this position ({present_s16}) as the base for future frames.[/dim]")
            frame["servos"][sid] = {"type": "relative", "diff": 0, "sign": "+", "ref_pos": present_s16}
        else:
            # ── Subsequent frames: diff = current_present − prev_ref ──────────
            diff_signed = present_s16 - prev_ref
            diff_mag    = abs(diff_signed)

            console.print(
                f"\n  [bold cyan]Servo {sid}[/bold cyan] — "
                f"prev ref=[yellow]{prev_ref}[/yellow]  "
                f"present now=[green]{present_s16}[/green]  "
                f"diff (now − prev)=[magenta]{diff_signed:+d}[/magenta]  "
                f"magnitude=[bold]{diff_mag}[/bold]"
            )
            while True:
                sign = input(f"  Assign playback sign for servo {sid}  [+/-]: ").strip()
                if sign in ("+", "-"):
                    break
                console.print("  [red]Enter  +  or  -[/red]")

            frame["servos"][sid] = {"type": "relative", "diff": diff_mag, "sign": sign, "ref_pos": present_s16}

    # ── Frame-level settings ──────────────────────────────────────────────────
    console.print("\n  [bold]Frame timing & speed[/bold]")
    frame["delay"]    = _ask_float("  Delay before frame  (s) [0.5]: ", 0.5)
    frame["duration"] = _ask_float("  Hold duration       (s) [1.0]: ", 1.0)
    frame["speed"]    = _ask_int("  Moving speed  0-1023  (0=max) [200]: ", 200, 0, 1023)

    frames.append(frame)
    console.print(
        f"\n  [green]Frame {len(frames)} recorded.[/green]  "
        f"delay={frame['delay']}s  duration={frame['duration']}s  speed={frame['speed']}"
    )

# ── List frames ───────────────────────────────────────────────────────────────

def list_frames(frames: List[Dict]) -> None:
    if not frames:
        console.print("  [dim]No frames recorded yet.[/dim]")
        return

    tbl = Table(
        title        = f"Recorded Frames  ({len(frames)} total)",
        border_style = "bright_black",
        header_style = "bold white",
        show_lines   = True,
    )
    tbl.add_column("#",      justify="right",  style="dim",    width=4)
    tbl.add_column("Delay",  justify="right",                  width=8)
    tbl.add_column("Hold",   justify="right",                  width=7)
    tbl.add_column("Speed",  justify="right",                  width=7)

    for sid in SERVO_IDS:
        color = "magenta" if sid in REL_IDS else "cyan"
        tbl.add_column(f"[{color}]S{sid}[/{color}]", justify="right", min_width=11)

    for i, f in enumerate(frames, 1):
        row = [
            str(i),
            f"{f['delay']:.2f}s",
            f"{f['duration']:.2f}s",
            str(f["speed"]),
        ]
        for sid in SERVO_IDS:
            sv = f["servos"].get(sid)
            if sv is None:
                row.append("[dim]—[/dim]")
            elif sv["type"] == "absolute":
                v = sv.get("value")
                if v is None:
                    row.append("[red]ERR[/red]")
                else:
                    deg = _s16(v) * 0.088
                    row.append(f"{v}\n({deg:+.1f}°)")
            else:
                s   = sv["sign"]
                d   = sv["diff"]
                ref = sv.get("ref_pos")
                c   = "green" if s == "+" else "red"
                ref_str = f"\n[dim]ref={ref}[/dim]" if ref is not None else ""
                row.append(f"[{c}]{s}{d}[/{c}]{ref_str}")
        tbl.add_row(*row)

    console.print(tbl)

# ── Edit frame ────────────────────────────────────────────────────────────────

def edit_frame(frames: List[Dict]) -> None:
    if not frames:
        console.print("  [dim]No frames to edit.[/dim]")
        return

    list_frames(frames)

    idx_str = input(f"\n  Frame number to edit  (1-{len(frames)}): ").strip()
    try:
        idx = int(idx_str) - 1
        if not (0 <= idx < len(frames)):
            raise ValueError
    except ValueError:
        console.print("  [red]Invalid frame number.[/red]")
        return

    f = frames[idx]
    console.print(f"\n  [bold]Editing frame {idx + 1}[/bold]  (press Enter to keep current value)\n")

    # Frame-level settings
    console.print("  [bold]Timing & speed[/bold]")
    new_delay = input(f"  Delay    [{f['delay']}s]: ").strip()
    if new_delay:
        try:
            f["delay"] = float(new_delay)
        except ValueError:
            console.print("  [red]Invalid, kept original.[/red]")

    new_dur = input(f"  Duration [{f['duration']}s]: ").strip()
    if new_dur:
        try:
            f["duration"] = float(new_dur)
        except ValueError:
            console.print("  [red]Invalid, kept original.[/red]")

    new_spd = input(f"  Speed    [{f['speed']}]: ").strip()
    if new_spd:
        try:
            f["speed"] = max(0, min(1023, int(new_spd)))
        except ValueError:
            console.print("  [red]Invalid, kept original.[/red]")

    # Per-servo values
    console.print("\n  [bold]Servo values[/bold]")
    console.print("  [dim]Servos 1-5: Enter to keep · r <raw> · d <degrees>[/dim]")

    for sid in SERVO_IDS:
        sv = f["servos"].get(sid)
        if sv is None:
            console.print(f"  Servo {sid}: [dim]not recorded in this frame[/dim]")
            continue

        if sv["type"] == "absolute":
            new_val = _ask_abs_value(sid, sv.get("value"))
            sv["value"] = new_val

        elif sv["type"] == "relative":
            cur_diff = sv.get("diff", 0)
            cur_sign = sv.get("sign", "+")
            cur_ref  = sv.get("ref_pos")

            console.print(
                f"  Servo {sid} [magenta]relative[/magenta]  "
                f"ref_pos=[yellow]{cur_ref}[/yellow]  "
                f"diff=[bold]{cur_sign}{cur_diff}[/bold]"
            )
            raw_diff = input(f"    New magnitude [{cur_diff}]: ").strip()
            if raw_diff:
                try:
                    sv["diff"] = abs(int(raw_diff))
                except ValueError:
                    console.print("  [red]Invalid, kept original.[/red]")

            raw_sign = input(f"  Servo {sid} sign [{cur_sign}]  (+/-): ").strip()
            if raw_sign in ("+", "-"):
                sv["sign"] = raw_sign

    console.print(f"  [green]Frame {idx + 1} updated.[/green]")

# ── Playback ──────────────────────────────────────────────────────────────────

def play_frames(ser, frames: List[Dict], stop_flag: Optional[threading.Event] = None) -> None:
    if not frames:
        console.print("  [dim]No frames to play.[/dim]")
        return

    console.print(f"\n[bold green]Playing {len(frames)} frame(s)...[/bold green]\n")

    for i, f in enumerate(frames, 1):
        delay    = f.get("delay",    0.5)
        duration = f.get("duration", 1.0)
        speed    = f.get("speed",    200)

        console.print(
            f"[bold]► Frame {i}/{len(frames)}[/bold]  "
            f"delay=[cyan]{delay}s[/cyan]  "
            f"hold=[cyan]{duration}s[/cyan]  "
            f"speed=[cyan]{speed}[/cyan]"
        )

        if delay > 0:
            console.print(f"  [dim]Waiting {delay}s before sending...[/dim]")
            if not wait_interruptible(delay, stop_flag):
                return

        # ── Step 0: Actuator ──────────────────────────────────────────────────
        act = f.get("actuator")
        if act and global_arm:
            action = act.get("action")
            dist = act.get("distance_mm")
            dur_val = act.get("duration")
            console.print(f"  [bold magenta]Actuator:[/bold magenta] {action} (dist={dist}, duration={dur_val})")
            if action == "extend":
                global_arm.extend(distance_mm=dist, duration=dur_val, wait=True)
            elif action == "retract":
                global_arm.retract(distance_mm=dist, duration=dur_val, wait=True)
            if stop_flag and stop_flag.is_set():
                return

        # ── Step 1: read S6 & S7 FIRST while bus is idle ─────────────────────
        # Must happen before any writes — identical to loop_frames approach.
        rel_present: Dict[int, Optional[int]] = {}
        for sid in REL_IDS:
            sv = f["servos"].get(sid)
            if sv is None:
                continue
            present = read_reg(ser, sid, *REG_PRESENT_POS)
            if present is None:
                console.print(f"  Servo {sid}: [red]cannot read present position — skipped[/red]")
            rel_present[sid] = present

        # ── Step 2: apply speed to all servos ────────────────────────────────
        for sid in SERVO_IDS:
            write_reg(ser, sid, *REG_MOVING_SPEED, speed)

        # ── Step 3: absolute goal positions (servos 1-5) ─────────────────────
        for sid in ABS_IDS:
            sv = f["servos"].get(sid)
            if sv is None:
                continue
            val = sv.get("value")
            if val is None:
                console.print(f"  Servo {sid}: [red]no value stored — skipped[/red]")
                continue
            write_reg(ser, sid, *REG_GOAL_POSITION, val)
            deg = _s16(val) * 0.088
            console.print(f"  Servo {sid} [cyan]abs[/cyan] → [yellow]{val}[/yellow]  ({deg:+.2f}°)")

        # ── Step 4: relative goal positions (servos 6-7) ─────────────────────
        for sid in REL_IDS:
            sv = f["servos"].get(sid)
            if sv is None:
                continue
            present = rel_present.get(sid)
            if present is None:
                continue
            diff        = sv.get("diff", 0)
            sign        = sv.get("sign", "+")
            present_s16 = _s16(present)
            offset      = diff if sign == "+" else -diff
            target      = max(-32768, min(32767, present_s16 + offset))
            raw_val     = target & 0xFFFF
            deg         = _s16(raw_val) * 0.088
            write_reg(ser, sid, *REG_GOAL_POSITION, raw_val)
            sign_color = "green" if sign == "+" else "red"
            console.print(
                f"  Servo {sid} [magenta]rel[/magenta] → "
                f"[dim]{present_s16}[/dim] "
                f"[{sign_color}]{sign}{diff}[/{sign_color}] "
                f"= [yellow]{target}[/yellow]  ({deg:+.2f}°)"
            )

        if duration > 0:
            console.print(f"  [dim]Holding {duration}s...[/dim]")
            if not wait_interruptible(duration, stop_flag):
                return

        console.print()

    console.print("[bold green]Playback complete.[/bold green]")

# ── Loop playback ────────────────────────────────────────────────────────────

def _make_loop_status(loop: int, frame_idx: int, total: int,
                      frame: dict, log_lines: List[str]) -> Table:
    """Build the Rich Live status table for loop mode."""
    tbl = Table(
        title=(
            f"[bold cyan]Carbot Loop[/bold cyan]  [dim]|[/dim]  "
            f"Loop [bold yellow]{loop}[/bold yellow]  [dim]|[/dim]  "
            f"Frame [bold green]{frame_idx}/{total}[/bold green]  [dim]|[/dim]  "
            f"delay=[cyan]{frame['delay']}s[/cyan]  "
            f"hold=[cyan]{frame['duration']}s[/cyan]  "
            f"speed=[cyan]{frame['speed']}[/cyan]  "
            f"[dim]   Ctrl+C to stop[/dim]"
        ),
        border_style="bright_black",
        header_style="bold white",
        show_lines=True,
        expand=True,
    )
    tbl.add_column("ID",      justify="center", style="cyan",   width=4)
    tbl.add_column("Mode",    justify="center",                 width=10)
    tbl.add_column("Target",  justify="right",  style="yellow", width=10)
    tbl.add_column("Target°", justify="right",                  width=10)
    tbl.add_column("Details", justify="left",                   min_width=24)

    for sid in SERVO_IDS:
        sv = frame["servos"].get(sid)
        if sv is None:
            tbl.add_row(str(sid), "[dim]—[/dim]", "—", "—", "[dim]skipped[/dim]")
            continue
        if sv["type"] == "absolute":
            val = sv.get("value")
            if val is None:
                tbl.add_row(str(sid), "[cyan]abs[/cyan]", "[red]ERR[/red]", "—", "")
            else:
                tbl.add_row(str(sid), "[cyan]abs[/cyan]",
                            str(val), f"{_s16(val)*0.088:+.1f}°", "")
        else:
            diff = sv.get("diff", 0)
            sign = sv.get("sign", "+")
            c    = "green" if sign == "+" else "red"
            tbl.add_row(str(sid), "[magenta]rel[/magenta]",
                        f"[{c}]{sign}{diff}[/{c}]", "—",
                        "[dim]from present pos[/dim]")

    log_text = "\n".join(log_lines[-6:]) if log_lines else "[dim]  ...[/dim]"
    outer = Table.grid(expand=True)
    outer.add_row(tbl)
    outer.add_row(Padding(Text.from_markup(log_text), (0, 2)))
    return outer


def loop_frames(ser, frames: List[Dict], stop_flag: Optional[threading.Event] = None) -> None:
    """Run all recorded frames in an infinite loop until Ctrl+C."""
    if not frames:
        console.print("  [dim]No frames to loop. Record or load some first.[/dim]")
        return

    console.print(f"\n[bold green]Loop mode — {len(frames)} frame(s). Ctrl+C to stop.[/bold green]")

    # ── Startup ping: stabilises bus + flushes USB FIFO (critical for S6 & S7) ─
    console.print("[dim]Pinging servos...[/dim]")
    for sid in SERVO_IDS:
        ok = ping(ser, sid)
        status = "[green]OK[/green]" if ok else "[red]NO RESPONSE[/red]"
        console.print(f"  Servo {sid}: {status}")
    console.print()

    loop_count = 0
    log_lines: List[str] = []

    try:
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            while True:
                if stop_flag and stop_flag.is_set():
                    break
                loop_count += 1
                log_lines.append(f"\n[bold cyan]--- Loop {loop_count} ---[/bold cyan]")

                for idx, f in enumerate(frames, 1):
                    delay    = f.get("delay",    0.5)
                    duration = f.get("duration", 1.0)
                    speed    = f.get("speed",    200)

                    log_lines.append(f"[bold]> Frame {idx}/{len(frames)}[/bold]")
                    live.update(_make_loop_status(loop_count, idx, len(frames), f, log_lines))

                    if delay > 0:
                        log_lines.append(f"  [dim]>> waiting {delay}s...[/dim]")
                        if not wait_interruptible(delay, stop_flag):
                            raise KeyboardInterrupt

                    # ── Step 0: Actuator ──────────────────────────────────────
                    act = f.get("actuator")
                    if act and global_arm:
                        action = act.get("action")
                        dist = act.get("distance_mm")
                        dur_val = act.get("duration")
                        log_lines.append(f"  [magenta]>> actuator {action}[/magenta]")
                        if action == "extend":
                            global_arm.extend(distance_mm=dist, duration=dur_val, wait=True)
                        elif action == "retract":
                            global_arm.retract(distance_mm=dist, duration=dur_val, wait=True)
                        if stop_flag and stop_flag.is_set():
                            raise KeyboardInterrupt

                    # ── Step 1: read S6 & S7 FIRST while bus is idle ──────────
                    rel_present: Dict[int, Optional[int]] = {}
                    for sid in REL_IDS:
                        sv = f["servos"].get(sid)
                        if sv is None:
                            continue
                        pkt = _build(sid, READ_DATA, list(REG_PRESENT_POS))
                        _robust_clear(ser)
                        ser.write(pkt); ser.flush()
                        time.sleep(max(0.003, len(pkt) * 10.0 / BAUDRATE))
                        resp = _recv(ser, sid, timeout=0.12)
                        if resp is None or len(resp[1]) < 2:
                            log_lines.append(f"  [red]S{sid}: can't read present — skipped[/red]")
                            rel_present[sid] = None
                        else:
                            rel_present[sid] = resp[1][0] | (resp[1][1] << 8)

                    # ── Step 2: set speed on all servos ──────────────────────
                    for sid in SERVO_IDS:
                        write_reg(ser, sid, *REG_MOVING_SPEED, speed)

                    # ── Step 3: absolute goal positions (servos 1-5) ─────────
                    for sid in ABS_IDS:
                        sv = f["servos"].get(sid)
                        if sv is None:
                            continue
                        val = sv.get("value")
                        if val is None:
                            log_lines.append(f"  [red]S{sid}: no value — skipped[/red]")
                            continue
                        write_reg(ser, sid, *REG_GOAL_POSITION, val)
                        log_lines.append(
                            f"  S{sid} [cyan]abs[/cyan] → "
                            f"[yellow]{val}[/yellow] ({_s16(val)*0.088:+.1f}°)"
                        )

                    # ── Step 4: relative goal positions (servos 6-7) ─────────
                    for sid in REL_IDS:
                        sv = f["servos"].get(sid)
                        if sv is None:
                            continue
                        present = rel_present.get(sid)
                        if present is None:
                            continue
                        diff        = sv.get("diff", 0)
                        sign        = sv.get("sign", "+")
                        present_s16 = _s16(present)
                        offset      = diff if sign == "+" else -diff
                        target      = max(-32768, min(32767, present_s16 + offset))
                        raw_val     = target & 0xFFFF
                        write_reg(ser, sid, *REG_GOAL_POSITION, raw_val)
                        c = "green" if sign == "+" else "red"
                        log_lines.append(
                            f"  S{sid} [magenta]rel[/magenta] "
                            f"[dim]{present_s16}[/dim] [{c}]{sign}{diff}[/{c}]"
                            f" → [yellow]{target}[/yellow] ({_s16(raw_val)*0.088:+.1f}°)"
                        )

                    if duration > 0:
                        log_lines.append(f"  [dim].. holding {duration}s...[/dim]")
                        if not wait_interruptible(duration, stop_flag):
                            raise KeyboardInterrupt

                    live.update(_make_loop_status(loop_count, idx, len(frames), f, log_lines))

    except KeyboardInterrupt:
        pass

    console.print(f"\n[green]Loop stopped after {loop_count} loop(s).[/green]")


# ── Direct move ──────────────────────────────────────────────────────────────

def menu_move(ser) -> None:
    """
    Send a goal position to one or more servos immediately.

    Servos 1-5  →  absolute position  (raw count  or  degrees)
    Servos 6-7  →  relative offset    (+N / -N counts from present position)
    """
    read_current(ser)

    console.print(
        "\n[bold]Direct Move[/bold]  —  which servo?  "
        f"Enter ID {SERVO_IDS} or [bold]a[/bold] for all: ",
        end="",
    )
    choice = input().strip().lower()

    if choice == "a":
        targets = SERVO_IDS
    else:
        try:
            sid = int(choice)
            if sid not in SERVO_IDS:
                raise ValueError
            targets = [sid]
        except ValueError:
            console.print(f"  [red]Enter one of {SERVO_IDS} or  a  for all.[/red]")
            return

    # Speed — optional, applied before any move
    spd_str = input("  Moving speed  0-1023  (0=max, Enter=skip): ").strip()
    if spd_str:
        try:
            spd = max(0, min(1023, int(spd_str)))
            for sid in targets:
                write_reg(ser, sid, *REG_MOVING_SPEED, spd)
            console.print(f"  [dim]Speed set to {spd} on {targets}.[/dim]")
        except ValueError:
            console.print("  [red]Invalid speed, skipped.[/red]")

    console.print()

    for sid in targets:
        if sid in ABS_IDS:
            # ── Absolute: raw or degrees ──────────────────────────────────────
            current = read_reg(ser, sid, *REG_PRESENT_POS)
            cur_str = (
                f"{current} = {_s16(current)*0.088:+.2f}°"
                if current is not None else "unreadable"
            )
            console.print(
                f"  Servo [cyan]{sid}[/cyan]  present=[green]{cur_str}[/green]  "
                f"→  [bold]r[/bold] <raw>  or  [bold]d[/bold] <degrees>"
            )
            raw_in = input(f"    S{sid}: ").strip()
            if not raw_in:
                console.print(f"  [dim]Servo {sid} skipped.[/dim]")
                continue

            parts = raw_in.split(None, 1)
            mode  = parts[0].lower()
            try:
                if mode == "r":
                    raw_val = int(parts[1]) & 0xFFFF
                elif mode == "d":
                    raw_val = _deg_to_raw(float(parts[1]))
                else:
                    console.print("  [red]Use  r <raw>  or  d <degrees>.[/red]")
                    continue
            except (IndexError, ValueError):
                console.print("  [red]Invalid input.[/red]")
                continue

            write_reg(ser, sid, *REG_GOAL_POSITION, raw_val)
            deg = _s16(raw_val) * 0.088
            console.print(f"  Servo {sid} [cyan]→[/cyan] [yellow]{raw_val}[/yellow]  ({deg:+.2f}°)  [green]sent[/green]")

        else:
            # ── Relative: offset from present position (IDs 6-7) ─────────────
            present = read_reg(ser, sid, *REG_PRESENT_POS)
            if present is None:
                console.print(f"  Servo {sid}: [red]cannot read present position — skipped.[/red]")
                continue

            present_s16 = _s16(present)
            console.print(
                f"  Servo [magenta]{sid}[/magenta]  present=[green]{present_s16}[/green]  "
                f"({present_s16*0.088:+.2f}°)  →  enter offset  e.g.  +100  -50"
            )
            off_str = input(f"    S{sid}: ").strip()
            if not off_str:
                console.print(f"  [dim]Servo {sid} skipped.[/dim]")
                continue

            try:
                offset = int(off_str)
            except ValueError:
                console.print("  [red]Invalid offset.[/red]")
                continue

            target  = max(-32768, min(32767, present_s16 + offset))
            raw_val = target & 0xFFFF
            deg     = _s16(raw_val) * 0.088

            write_reg(ser, sid, *REG_GOAL_POSITION, raw_val)
            sign_color = "green" if offset >= 0 else "red"
            console.print(
                f"  Servo {sid} [magenta]→[/magenta] "
                f"[dim]{present_s16}[/dim] "
                f"[{sign_color}]{offset:+d}[/{sign_color}] "
                f"= [yellow]{target}[/yellow]  ({deg:+.2f}°)  [green]sent[/green]"
            )

# ── Torque toggle ─────────────────────────────────────────────────────────────

def menu_torque(ser) -> None:
    """Enable or disable torque on one or all servos."""
    console.print(f"\n[bold]Torque Toggle[/bold]  —  servo ID {SERVO_IDS} or [bold]a[/bold] for all: ", end="")
    choice = input().strip().lower()

    if choice == "a":
        targets = SERVO_IDS
    else:
        try:
            sid = int(choice)
            if sid not in SERVO_IDS:
                raise ValueError
            targets = [sid]
        except ValueError:
            console.print(f"  [red]Enter one of {SERVO_IDS} or  a.[/red]")
            return

    for sid in targets:
        current = read_reg(ser, sid, *REG_TORQUE_ENABLE)
        new_val = 0 if current else 1
        write_reg(ser, sid, *REG_TORQUE_ENABLE, new_val)
        state = "[green]ON[/green]" if new_val else "[dim]OFF[/dim]"
        prev  = "[green]ON[/green]" if current else "[dim]OFF[/dim]"
        console.print(f"  Servo {sid}  torque  {prev} → {state}")

def set_torque_all(ser, enable: bool) -> None:
    val = 1 if enable else 0
    for sid in SERVO_IDS:
        write_reg(ser, sid, *REG_TORQUE_ENABLE, val)

# ── Read current servo values ─────────────────────────────────────────────────

def read_current(ser) -> None:
    """Read and display movement-relevant values for all 7 servos."""
    console.print("\n[bold]Reading servos...[/bold]")

    tbl = Table(
        title        = "Live Servo Positions",
        border_style = "bright_black",
        header_style = "bold white",
        show_lines   = True,
    )
    tbl.add_column("ID",            justify="center", style="cyan",   width=4)
    tbl.add_column("Goal (raw)",    justify="right",  style="yellow", width=12)
    tbl.add_column("Goal (°)",      justify="right",                  width=12)
    tbl.add_column("Present (raw)", justify="right",  style="green",  width=14)
    tbl.add_column("Present (°)",   justify="right",                  width=12)
    tbl.add_column("Speed (raw)",   justify="right",                  width=11)
    tbl.add_column("Moving?",       justify="center",                 width=8)

    for sid in SERVO_IDS:
        goal    = read_reg(ser, sid, *REG_GOAL_POSITION)
        present = read_reg(ser, sid, *REG_PRESENT_POS)
        speed   = read_reg(ser, sid, *REG_MOVING_SPEED)
        moving  = read_reg(ser, sid, 46, 1)

        g_raw = str(goal)    if goal    is not None else "[red]ERR[/red]"
        p_raw = str(present) if present is not None else "[red]ERR[/red]"
        g_deg = f"{_s16(goal)*0.088:+.2f}°"    if goal    is not None else "—"
        p_deg = f"{_s16(present)*0.088:+.2f}°" if present is not None else "—"
        spd_str = str(speed & 0x3FF) if speed is not None else "[red]ERR[/red]"

        if moving is None:
            mov_str = "[red]ERR[/red]"
        else:
            mov_str = "[yellow]YES[/yellow]" if moving else "[dim]no[/dim]"

        tbl.add_row(str(sid), g_raw, g_deg, p_raw, p_deg, spd_str, mov_str)

    console.print(tbl)

# ── Save / Load ───────────────────────────────────────────────────────────────

def save_frames(frames: List[Dict]) -> None:
    if not frames:
        console.print("  [dim]No frames to save.[/dim]")
        return
    path_str = input("  Save to file  (e.g. recording.json): ").strip()
    if not path_str:
        console.print("  [red]No path given.[/red]")
        return
    try:
        serializable = []
        for f in frames:
            frame_data = {
                "delay":    f["delay"],
                "duration": f["duration"],
                "speed":    f["speed"],
                "servos":   {str(k): v for k, v in f["servos"].items()},
            }
            if "actuator" in f:
                frame_data["actuator"] = f["actuator"]
            serializable.append(frame_data)
        payload = {
            "frame_count": len(serializable),
            "frames": serializable,
        }
        Path(path_str).write_text(json.dumps(payload, indent=2))
        console.print(f"  [green]Saved {len(frames)} frame(s) → {path_str}[/green]")
    except Exception as e:
        console.print(f"  [red]Save failed: {e}[/red]")


def _load_from_path(path_str: str) -> Optional[List[Dict]]:
    """Parse a JSON recording file and return a list of frames, or None on error."""
    try:
        raw = json.loads(Path(path_str).read_text())
        if isinstance(raw, list):
            data = raw
        elif isinstance(raw, dict) and isinstance(raw.get("frames"), list):
            data = raw["frames"]
        else:
            raise ValueError("JSON must be an array of frames or an object with a 'frames' array")
        loaded = []
        for f in data:
            frame_data = {
                "delay":    float(f["delay"]),
                "duration": float(f["duration"]),
                "speed":    int(f["speed"]),
                "servos":   {int(k): v for k, v in f["servos"].items()},
            }
            if "actuator" in f:
                frame_data["actuator"] = f["actuator"]
            loaded.append(frame_data)
        return loaded
    except Exception as e:
        console.print(f"  [red]Load failed: {e}[/red]")
        return None


def load_frames(frames: List[Dict]) -> None:
    path_str = input("  Load from file: ").strip()
    if not path_str:
        console.print("  [red]No path given.[/red]")
        return
    loaded = _load_from_path(path_str)
    if loaded is not None:
        frames.clear()
        frames.extend(loaded)
        console.print(f"  [green]Loaded {len(frames)} frame(s) from {path_str}[/green]")

# ── Port auto-detect  (Windows + Linux / Raspberry Pi) ───────────────────────

def find_port() -> Optional[str]:
    # Pass 1 — match known USB-serial chip names in HWID/description (both OS)
    for p in serial.tools.list_ports.comports():
        combined = (p.hwid + p.description).lower()
        if any(k in combined for k in
               ("ft232", "ftdi", "usb serial", "usb2dynamixel", "ch340", "cp210", "pl2303")):
            return p.device

    # Pass 2 — Linux / Pi fallback: take the first available ttyUSB or ttyACM
    if IS_LINUX:
        for pattern in ("ttyUSB*", "ttyACM*"):
            matches = sorted(Path("/dev").glob(pattern))
            if matches:
                return str(matches[0])

    return None


def _port_hint() -> str:
    """Return an OS-appropriate port example for error messages."""
    return "COM3" if IS_WINDOWS else "/dev/ttyUSB0"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Carbot servo recorder — MX-28 IDs 1-7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python carbot_record.py                                 # interactive, auto-detect port\n"
            "  python carbot_record.py --port /dev/ttyUSB0             # Linux explicit port\n"
            "  python carbot_record.py --port COM3                     # Windows explicit port\n"
            "  python carbot_record.py --file trail_2.json             # load recording at startup\n"
            "  python carbot_record.py --file trail_2.json --loop      # headless Pi: load & loop\n"
        ),
    )
    parser.add_argument("--port", "-p", type=str, default=None,
                        help="Serial port (e.g. COM3 or /dev/ttyUSB0). Auto-detected if omitted.")
    parser.add_argument("--file", "-f", type=str, default=None,
                        help="JSON recording to load at startup.")
    parser.add_argument("--loop", action="store_true",
                        help="Jump straight into loop mode after loading --file (useful for headless Pi).")
    parser.add_argument("--play", action="store_true",
                        help="Play the loaded --file once and exit (useful for startup).")
    parser.add_argument("--torque-off", action="store_true",
                        help="Disable torque on all servos and exit.")
    args = parser.parse_args()

    # ── --loop requires --file ────────────────────────────────────────────────
    if args.loop and not args.file:
        console.print("[red]ERROR:[/] --loop requires --file  (e.g. --file trail_2.json --loop)")
        sys.exit(1)

    port = args.port or find_port()
    if not port:
        console.print(f"[red]ERROR:[/] No port found. Use --port {_port_hint()}")
        sys.exit(1)

    try:
        ser = serial.Serial(port=port, baudrate=BAUDRATE, timeout=0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception as e:
        console.print(f"[red]ERROR:[/] Cannot open {port}: {e}")
        if IS_LINUX and "Permission denied" in str(e):
            console.print(
                "[yellow]Tip:[/yellow] Your user may not have permission to access the serial port.\n"
                "  Run:  [bold]sudo usermod -aG dialout $USER[/bold]  then log out and back in.\n"
                "  Or run once with:  [bold]sudo python carbot_record.py[/bold]"
            )
        sys.exit(1)

    console.print(f"[green]Port open:[/green] {port}  @  {BAUDRATE} baud")

    # ── Startup ping — stabilises bus + flushes USB FIFO ─────────────────────
    console.print("[dim]Pinging servos...[/dim]")
    for sid in SERVO_IDS:
        ok = ping(ser, sid)
        status = "[green]OK[/green]" if ok else "[red]NO RESPONSE[/red]"
        console.print(f"  Servo {sid}: {status}")
    console.print()

    frames: List[Dict] = []

    # ── Load file if --file given ─────────────────────────────────────────────
    if args.file:
        loaded = _load_from_path(args.file)
        if loaded is None:
            ser.close()
            sys.exit(1)
        frames.extend(loaded)
        console.print(f"[green]Loaded[/green] {len(frames)} frame(s) from [cyan]{args.file}[/cyan]\n")

    # ── --torque-off: turn off torque and exit ───────────────────────────────
    if args.torque_off:
        set_torque_all(ser, False)
        ser.close()
        console.print("\n[green]Torque disabled. Port closed. Bye.[/green]")
        return

    # ── --loop: skip the menu and go straight into loop mode ─────────────────
    if args.loop:
        loop_frames(ser, frames)
        ser.close()
        console.print("\n[green]Port closed. Bye.[/green]")
        return

    # ── --play: skip the menu, play once and exit ────────────────────────────
    if args.play:
        play_frames(ser, frames)
        ser.close()
        console.print("\n[green]Port closed. Bye.[/green]")
        return

    MENU = (
        "\n[bold cyan]─── Carbot Recorder ─────────────────────────────[/bold cyan]\n"
        "  [bold]v[/bold]  — view current servo positions\n"
        "  [bold]m[/bold]  — move servo(s) now  (direct command)\n"
        "  [bold]t[/bold]  — toggle torque  ON / OFF\n"
        "  ────────────────────────────────────────────\n"
        "  [bold]r[/bold]  — record a new frame\n"
        "  [bold]e[/bold]  — edit a frame\n"
        "  [bold]p[/bold]  — play back all frames  (once)\n"
        "  [bold]o[/bold]  — [bold yellow]loop[/bold yellow] all frames continuously  (Ctrl+C to stop)\n"
        "  [bold]l[/bold]  — list all frames\n"
        "  [bold]s[/bold]  — save recording to JSON\n"
        "  [bold]d[/bold]  — load recording from JSON\n"
        "  [bold]c[/bold]  — clear all frames\n"
        "  [bold]q[/bold]  — quit\n"
        "[dim]  Each frame has delay (pause before) + duration (hold after).[/dim]\n"
        "[bold cyan]────────────────────────────────────────────────[/bold cyan]"
    )

    while True:
        console.print(MENU)
        try:
            cmd = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if   cmd == "v": read_current(ser)
        elif cmd == "m": menu_move(ser)
        elif cmd == "t": menu_torque(ser)
        elif cmd == "r": record_frame(ser, frames)
        elif cmd == "e": edit_frame(frames)
        elif cmd == "p": play_frames(ser, frames)
        elif cmd == "o": loop_frames(ser, frames)
        elif cmd == "l": list_frames(frames)
        elif cmd == "s": save_frames(frames)
        elif cmd == "d": load_frames(frames)
        elif cmd == "c":
            if frames and input("  Clear all frames? [y/N]: ").strip().lower() == "y":
                frames.clear()
                console.print("  [green]All frames cleared.[/green]")
        elif cmd == "q":
            break
        else:
            console.print("[dim]  Unknown command.[/dim]")

    ser.close()
    console.print("\n[green]Port closed. Bye.[/green]")


if __name__ == "__main__":
    main()