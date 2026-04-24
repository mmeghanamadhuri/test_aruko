"""
nina_record.py
--------------
Record and play back servo positions for Nina's 11 Dynamixel motors.


Motor Layout
------------
 Right hand (bottom to top): IDs 1, 3, 5, 7, 9
   - Pitch motors (MX-28):  1, 3
   - Pitch motor  (MX-106): 7
   - Roll  motors (MX-28):  5
   - Roll  motor  (MX-106): 9


 Left hand (bottom to top): IDs 2, 4, 6, 8, 10
   - Pitch motors (MX-28):  2, 4
   - Pitch motor  (MX-106): 8
   - Roll  motor  (MX-28):  6
   - Roll  motor  (MX-106): 10


 Neck motor (MX-28): ID 11


Motor Types
-----------
 MX-28  motors → IDs 1, 2, 3, 4, 5, 6, 11
 MX-106 motors → IDs 7, 8, 9, 10


All 11 motors operate in Joint Mode (position control).
 Position range : 0 – 4095  (maps to 0° – 300°)
 Resolution     : 300 / 4096 ≈ 0.0732° per count
 All positions are unsigned; no negative values.


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
 python nina_record.py                         # auto-detect port (Windows & Pi)
 python nina_record.py --port COM3             # Windows
 python nina_record.py --port /dev/ttyUSB0     # Raspberry Pi / Jetson
 python nina_record.py --file pose_1.json      # load recording at startup
 python nina_record.py --file pose_1.json --loop   # headless: load & loop
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


# ── Platform detection ────────────────────────────────────────────────────────
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"   # covers Raspberry Pi / Jetson


# ── Motor configuration ───────────────────────────────────────────────────────
#
#   Right hand (bottom → top): 1, 3, 5, 7, 9
#     Pitch: 1 (MX-28), 3 (MX-28), 7 (MX-106)
#     Roll:  5 (MX-28), 9 (MX-106)
#
#   Left hand (bottom → top): 2, 4, 6, 8, 10
#     Pitch: 2 (MX-28), 4 (MX-28), 8 (MX-106)
#     Roll:  6 (MX-28), 10 (MX-106)
#
#   Neck: 11 (MX-28)
#
SERVO_IDS  = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]   # all Nina motors
ABS_IDS    = SERVO_IDS                               # all motors use absolute playback


MX28_IDS   = [1, 2, 3, 4, 5, 6, 11]   # MX-28  motors
MX106_IDS  = [7, 8, 9, 10]            # MX-106 motors


BAUDRATE   = 222222
BUS_SETTLE_SEC = 0.25
INTER_PACKET_SEC = 0.004
PING_RETRIES = 3
READ_RETRIES = 4
PING_TIMEOUT_SEC = 0.08
READ_TIMEOUT_SEC = 0.05
WRITE_STATUS_TIMEOUT_SEC = 0.03


# ── Joint-mode position constants ─────────────────────────────────────────────
# All MX-28 and MX-106 motors are in Joint Mode (position control).
# Position register: 0–4095  →  0°–300°  (unsigned, never negative)
POS_MIN        = 0
POS_MAX        = 4095
DEG_PER_COUNT  = 300.0 / 4096   # ≈ 0.07324° per count
COUNTS_PER_DEG = 4096.0 / 300.0 # ≈ 13.653 counts per degree


# ── Motor role labels (used in tables) ───────────────────────────────────────
# Right hand: bottom to top = 1, 3, 5, 7, 9  |  1,3,7 pitch  |  5,9 roll
# Left  hand: bottom to top = 2, 4, 6, 8, 10 |  2,4,8 pitch  |  6,10 roll
# Neck: 11
MOTOR_LABELS: Dict[int, str] = {
   1:  "R-Pitch1",   # right hand, bottom pitch (MX-28)
   3:  "R-Pitch2",   # right hand, mid    pitch (MX-28)
   5:  "R-Roll1",    # right hand, bottom roll  (MX-28)
   7:  "R-Pitch3",   # right hand, top    pitch (MX-106)
   9:  "R-Roll2",    # right hand, top    roll  (MX-106)
   2:  "L-Pitch1",   # left  hand, bottom pitch (MX-28)
   4:  "L-Pitch2",   # left  hand, mid    pitch (MX-28)
   6:  "L-Roll1",    # left  hand, bottom roll  (MX-28)
   8:  "L-Pitch3",   # left  hand, top    pitch (MX-106)
   10: "L-Roll2",    # left  hand, top    roll  (MX-106)
   11: "Neck",       # neck                      (MX-28)
}


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


# ── Protocol helpers ──────────────────────────────────────────────────────────


def _raw_to_deg(v: int) -> float:
   """Joint mode: raw count (0–4095) → degrees (0.0–300.0°)."""
   return v * DEG_PER_COUNT




def _deg_to_raw(deg: float) -> int:
   """Joint mode: degrees (0–300°) → clamped raw count (0–4095)."""
   counts = round(deg * COUNTS_PER_DEG)
   return max(POS_MIN, min(POS_MAX, counts))




def _clamp_pos(v: int) -> int:
   """Clamp a raw position value to the valid joint-mode range 0–4095."""
   return max(POS_MIN, min(POS_MAX, int(v)))




def _checksum(pkt: List[int]) -> int:
   return (~sum(pkt[2:])) & 0xFF




def _robust_clear(ser: serial.Serial) -> None:
   """Double-drain: lets USB FIFO flush to PC buffer, then purges twice."""
   ser.reset_input_buffer()
   time.sleep(0.005)
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
   for attempt in range(PING_RETRIES):
       _robust_clear(ser)
       ser.write(pkt)
       ser.flush()
       time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / BAUDRATE))
       if _recv(ser, sid, timeout=PING_TIMEOUT_SEC) is not None:
           return True
       if attempt < PING_RETRIES - 1:
           time.sleep(0.015)
   return False




def read_reg(ser, sid: int, addr: int, size: int) -> Optional[int]:
   pkt = _build(sid, READ_DATA, [addr, size])


   for attempt in range(READ_RETRIES):
       _robust_clear(ser)
       ser.write(pkt)
       ser.flush()
       time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / BAUDRATE))
       resp = _recv(ser, sid, timeout=READ_TIMEOUT_SEC)


       if resp is not None and len(resp[1]) >= size:
           d = resp[1]
           return d[0] if size == 1 else (d[0] | (d[1] << 8))


       if attempt < READ_RETRIES - 1:
           time.sleep(0.01)


   return None




def write_reg(ser, sid: int, addr: int, size: int, value: int) -> bool:
   value  = int(value) & (0xFF if size == 1 else 0xFFFF)
   params = [addr, value & 0xFF] if size == 1 else [addr, value & 0xFF, (value >> 8) & 0xFF]
   pkt    = _build(sid, WRITE_DATA, params)
   ser.reset_input_buffer()
   ser.write(pkt)
   ser.flush()
   time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / BAUDRATE))
   _recv(ser, sid, timeout=WRITE_STATUS_TIMEOUT_SEC)
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
   """Read goal position and present position for every motor."""
   result = {}
   for sid in SERVO_IDS:
       goal    = read_reg(ser, sid, *REG_GOAL_POSITION)
       present = read_reg(ser, sid, *REG_PRESENT_POS)
       result[sid] = {"goal": goal, "present": present}
   return result




def _motor_type_str(sid: int) -> str:
   """Return a coloured motor-type label for a given servo ID."""
   if sid in MX106_IDS:
       return "[bold magenta]MX-106[/bold magenta]"
   return "[cyan]MX-28 [/cyan]"




def print_positions_table(positions: Dict[int, Dict[str, Optional[int]]]) -> None:
   """Display a Rich table showing goal and present positions for all motors."""
   tbl = Table(
       title       = "Nina — Current Motor Positions",
       border_style= "bright_black",
       header_style= "bold white",
       show_lines  = True,
   )
   tbl.add_column("ID",            justify="center", style="cyan",   width=4)
   tbl.add_column("Role",          justify="center",                 width=12)
   tbl.add_column("Type",          justify="center",                 width=9)
   tbl.add_column("Goal (raw)",    justify="right",  style="yellow", width=12)
   tbl.add_column("Goal (°)",      justify="right",                  width=12)
   tbl.add_column("Present (raw)", justify="right",  style="green",  width=14)
   tbl.add_column("Present (°)",   justify="right",                  width=12)


   for sid in SERVO_IDS:
       goal    = positions[sid]["goal"]
       present = positions[sid]["present"]


       g_raw = str(goal)    if goal    is not None else "[red]ERR[/red]"
       p_raw = str(present) if present is not None else "[red]ERR[/red]"
       g_deg = f"{_raw_to_deg(goal):.2f}°"    if goal    is not None else "—"
       p_deg = f"{_raw_to_deg(present):.2f}°" if present is not None else "—"


       role  = MOTOR_LABELS.get(sid, f"S{sid}")
       mtype = _motor_type_str(sid)
       tbl.add_row(str(sid), role, mtype, g_raw, g_deg, p_raw, p_deg)


   console.print(tbl)


# ── Frame helpers ─────────────────────────────────────────────────────────────
# Frame structure:
# {
#   "delay":    float,          # seconds before this frame
#   "duration": float,          # seconds to hold after sending
#   "speed":    int,            # 0-1023
#   "servos": {
#       1:  {"type": "absolute", "value": int},
#       2:  {"type": "absolute", "value": int},
#       ...
#       11: {"type": "absolute", "value": int},
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




def _ask_abs_value(sid: int, current: Optional[int]) -> Optional[int]:
   """
   Ask the user how to set the absolute goal position for a motor.
   Returns a raw 16-bit int, or the current value if kept.


   Input modes
   -----------
     Enter      — keep the current snapshot value
     r <value>  — raw count  (e.g.  r 2048,  range 0–4095)
     d <value>  — degrees    (e.g.  d 150.0,  range 0–300°)
   """
   cur_deg = f"{_raw_to_deg(current):.2f}°" if current is not None else "—"
   cur_str = f"{current} = {cur_deg}" if current is not None else "unreadable"
   role    = MOTOR_LABELS.get(sid, f"S{sid}")
   mtype   = "MX-106" if sid in MX106_IDS else "MX-28"


   console.print(
       f"  Motor [cyan]{sid:>2}[/cyan] [dim]({role} / {mtype})[/dim]  "
       f"snapshot=[yellow]{cur_str}[/yellow]  "
       f"→  Enter to keep,  [bold]r[/bold] <0-4095>  or  [bold]d[/bold] <0-300°>"
   )
   raw_in = input(f"    S{sid}: ").strip()


   if not raw_in:
       return current                          # keep snapshot


   parts = raw_in.split(None, 1)
   mode  = parts[0].lower()


   if mode == "r":
       try:
           return _clamp_pos(int(parts[1]))
       except (IndexError, ValueError):
           console.print("  [red]  Usage:  r <integer 0-4095>  e.g.  r 2048[/red]")
           return current


   if mode == "d":
       try:
           raw_val  = _deg_to_raw(float(parts[1]))
           deg_back = _raw_to_deg(raw_val)
           console.print(f"  [dim]  {float(parts[1]):.2f}° → raw {raw_val}  ({deg_back:.2f}°)[/dim]")
           return raw_val
       except (IndexError, ValueError):
           console.print("  [red]  Usage:  d <degrees 0-300>  e.g.  d 150.0[/red]")
           return current


   console.print("  [red]  Unknown mode. Use  r <raw>  or  d <degrees>  or Enter to keep.[/red]")
   return current


# ── Record ────────────────────────────────────────────────────────────────────


def record_frame(ser, frames: List[Dict]) -> None:
   console.print("\n[bold]Recording frame — reading all 11 motors...[/bold]")
   positions = read_positions(ser)
   print_positions_table(positions)


   frame: Dict[str, Any] = {"delay": 0.5, "duration": 1.0, "speed": 200, "servos": {}}


   # ── Right hand: IDs 1, 3, 5, 7, 9 (bottom to top) ────────────────────────
   console.print("\n  [bold yellow]Right hand[/bold yellow]  (bottom → top: 1, 3, 5, 7, 9)")
   console.print("  [dim]1=R-Pitch1(MX28)  3=R-Pitch2(MX28)  5=R-Roll1(MX28)  "
                 "7=R-Pitch3(MX106)  9=R-Roll2(MX106)[/dim]")
   console.print("  [dim]Enter to keep snapshot · r <raw> · d <degrees>[/dim]\n")
   for sid in [1, 3, 5, 7, 9]:
       goal  = positions[sid]["goal"]
       value = _ask_abs_value(sid, goal)
       frame["servos"][sid] = {"type": "absolute", "value": value}


   # ── Left hand: IDs 2, 4, 6, 8, 10 (bottom to top) ────────────────────────
   console.print("\n  [bold yellow]Left hand[/bold yellow]  (bottom → top: 2, 4, 6, 8, 10)")
   console.print("  [dim]2=L-Pitch1(MX28)  4=L-Pitch2(MX28)  6=L-Roll1(MX28)  "
                 "8=L-Pitch3(MX106)  10=L-Roll2(MX106)[/dim]")
   console.print("  [dim]Enter to keep snapshot · r <raw> · d <degrees>[/dim]\n")
   for sid in [2, 4, 6, 8, 10]:
       goal  = positions[sid]["goal"]
       value = _ask_abs_value(sid, goal)
       frame["servos"][sid] = {"type": "absolute", "value": value}


   # ── Neck: ID 11 ───────────────────────────────────────────────────────────
   console.print("\n  [bold yellow]Neck[/bold yellow]  (ID 11 / MX-28)")
   console.print("  [dim]Enter to keep snapshot · r <raw> · d <degrees>[/dim]\n")
   goal  = positions[11]["goal"]
   value = _ask_abs_value(11, goal)
   frame["servos"][11] = {"type": "absolute", "value": value}


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
       title        = f"Nina — Recorded Frames  ({len(frames)} total)",
       border_style = "bright_black",
       header_style = "bold white",
       show_lines   = True,
   )
   tbl.add_column("#",      justify="right",  style="dim",    width=4)
   tbl.add_column("Delay",  justify="right",                  width=8)
   tbl.add_column("Hold",   justify="right",                  width=7)
   tbl.add_column("Speed",  justify="right",                  width=7)


   for sid in SERVO_IDS:
       color = "magenta" if sid in MX106_IDS else "cyan"
       label = MOTOR_LABELS.get(sid, f"S{sid}")
       tbl.add_column(f"[{color}]S{sid}\n{label}[/{color}]", justify="right", min_width=11)


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
           else:
               v = sv.get("value")
               if v is None:
                   row.append("[red]ERR[/red]")
               else:
                   deg = _raw_to_deg(v)
                   row.append(f"{v}\n({deg:.1f}°)")
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


   # Per-motor values
   console.print("\n  [bold]Motor values[/bold]")
   console.print("  [dim]All motors: Enter to keep · r <raw> · d <degrees>[/dim]")


   for sid in SERVO_IDS:
       sv = f["servos"].get(sid)
       if sv is None:
           console.print(f"  Motor {sid}: [dim]not recorded in this frame[/dim]")
           continue
       new_val = _ask_abs_value(sid, sv.get("value"))
       sv["value"] = new_val


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


       # ── Step 1: apply speed to all motors ────────────────────────────────
       for sid in SERVO_IDS:
           write_reg(ser, sid, *REG_MOVING_SPEED, speed)


       # ── Step 2: send absolute goal positions to all 11 motors ────────────
       for sid in SERVO_IDS:
           sv = f["servos"].get(sid)
           if sv is None:
               continue
           val = sv.get("value")
           if val is None:
               console.print(f"  Motor {sid}: [red]no value stored — skipped[/red]")
               continue
           write_reg(ser, sid, *REG_GOAL_POSITION, val)
           deg  = _raw_to_deg(val)
           role = MOTOR_LABELS.get(sid, f"S{sid}")
           console.print(
               f"  S{sid:>2} [dim]({role})[/dim] → "
               f"[yellow]{val}[/yellow]  ({deg:.2f}°)  [green]sent[/green]"
           )


       if stop_flag and stop_flag.is_set():
           return


       if duration > 0:
           console.print(f"  [dim]Holding {duration}s...[/dim]")
           if not wait_interruptible(duration, stop_flag):
               return


   console.print("\n[green]Playback complete.[/green]")


# ── Loop playback ─────────────────────────────────────────────────────────────


def _make_loop_status(loop: int, frame_idx: int, total: int,
                     frame: dict, log_lines: List[str]) -> Table:
   """Build the Rich Live status table for loop mode."""
   tbl = Table(
       title=(
           f"[bold cyan]Nina Loop[/bold cyan]  [dim]|[/dim]  "
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
   tbl.add_column("Role",    justify="center",                 width=12)
   tbl.add_column("Type",    justify="center",                 width=9)
   tbl.add_column("Target",  justify="right",  style="yellow", width=10)
   tbl.add_column("Target°", justify="right",                  width=10)


   for sid in SERVO_IDS:
       sv    = frame["servos"].get(sid)
       role  = MOTOR_LABELS.get(sid, f"S{sid}")
       mtype = _motor_type_str(sid)
       if sv is None:
           tbl.add_row(str(sid), role, mtype, "[dim]—[/dim]", "[dim]skipped[/dim]")
           continue
       val = sv.get("value")
       if val is None:
           tbl.add_row(str(sid), role, mtype, "[red]ERR[/red]", "—")
       else:
           tbl.add_row(str(sid), role, mtype, str(val), f"{_raw_to_deg(val):.1f}°")


   log_text = "\n".join(log_lines[-8:]) if log_lines else "[dim]  ...[/dim]"
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


   console.print("[dim]Pinging motors...[/dim]")
   for sid in SERVO_IDS:
       ok = ping(ser, sid)
       status = "[green]OK[/green]" if ok else "[red]NO RESPONSE[/red]"
       role = MOTOR_LABELS.get(sid, f"S{sid}")
       console.print(f"  Motor {sid:>2} ({role}): {status}")
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


                   # ── Step 1: set speed on all motors ──────────────────────
                   for sid in SERVO_IDS:
                       write_reg(ser, sid, *REG_MOVING_SPEED, speed)


                   # ── Step 2: absolute goal positions — all 11 motors ───────
                   for sid in SERVO_IDS:
                       sv = f["servos"].get(sid)
                       if sv is None:
                           continue
                       val = sv.get("value")
                       if val is None:
                           log_lines.append(f"  [red]S{sid}: no value — skipped[/red]")
                           continue
                       write_reg(ser, sid, *REG_GOAL_POSITION, val)
                       role = MOTOR_LABELS.get(sid, f"S{sid}")
                       log_lines.append(
                           f"  S{sid:>2} [dim]({role})[/dim] [cyan]abs[/cyan] → "
                           f"[yellow]{val}[/yellow] ({_raw_to_deg(val):.1f}°)"
                       )


                   if duration > 0:
                       log_lines.append(f"  [dim].. holding {duration}s...[/dim]")
                       if not wait_interruptible(duration, stop_flag):
                           raise KeyboardInterrupt


                   live.update(_make_loop_status(loop_count, idx, len(frames), f, log_lines))


   except KeyboardInterrupt:
       pass


   console.print(f"\n[green]Loop stopped after {loop_count} loop(s).[/green]")




# ── Direct move ───────────────────────────────────────────────────────────────


def menu_move(ser) -> None:
   """Send a goal position to one or more motors immediately (absolute)."""
   read_current(ser)


   console.print(
       f"\n[bold]Direct Move[/bold]  —  which motor?  "
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
       # All motors: absolute position (raw or degrees)
       current = read_reg(ser, sid, *REG_PRESENT_POS)
       cur_str = (
           f"{current} = {_raw_to_deg(current):.2f}°"
           if current is not None else "unreadable"
       )
       role  = MOTOR_LABELS.get(sid, f"S{sid}")
       mtype = "MX-106" if sid in MX106_IDS else "MX-28"
       console.print(
           f"  Motor [cyan]{sid:>2}[/cyan] [dim]({role} / {mtype})[/dim]  "
           f"present=[green]{cur_str}[/green]  "
           f"→  [bold]r[/bold] <0-4095>  or  [bold]d[/bold] <0-300°>"
       )
       raw_in = input(f"    S{sid}: ").strip()
       if not raw_in:
           console.print(f"  [dim]Motor {sid} skipped.[/dim]")
           continue


       parts = raw_in.split(None, 1)
       mode  = parts[0].lower()
       try:
           if mode == "r":
               raw_val = _clamp_pos(int(parts[1]))
           elif mode == "d":
               raw_val = _deg_to_raw(float(parts[1]))
           else:
               console.print("  [red]Use  r <raw>  or  d <degrees>.[/red]")
               continue
       except (IndexError, ValueError):
           console.print("  [red]Invalid input.[/red]")
           continue


       write_reg(ser, sid, *REG_GOAL_POSITION, raw_val)
       deg = _raw_to_deg(raw_val)
       console.print(f"  Motor {sid:>2} [cyan]→[/cyan] [yellow]{raw_val}[/yellow]  ({deg:.2f}°)  [green]sent[/green]")


# ── Torque toggle ─────────────────────────────────────────────────────────────


def menu_torque(ser) -> None:
   """Enable or disable torque on one or all motors."""
   console.print(f"\n[bold]Torque Toggle[/bold]  —  motor ID {SERVO_IDS} or [bold]a[/bold] for all: ", end="")
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
       state = "[green]ON[/green]"  if new_val  else "[dim]OFF[/dim]"
       prev  = "[green]ON[/green]"  if current  else "[dim]OFF[/dim]"
       role  = MOTOR_LABELS.get(sid, f"S{sid}")
       console.print(f"  Motor {sid:>2} ({role})  torque  {prev} → {state}")




def set_torque_all(ser, enable: bool) -> None:
   val = 1 if enable else 0
   for sid in SERVO_IDS:
       write_reg(ser, sid, *REG_TORQUE_ENABLE, val)


# ── Read current motor values ──────────────────────────────────────────────────


def read_current(ser) -> None:
   """Read and display live positions for all 11 motors."""
   console.print("\n[bold]Reading all 11 motors...[/bold]")


   tbl = Table(
       title        = "Nina — Live Motor Positions",
       border_style = "bright_black",
       header_style = "bold white",
       show_lines   = True,
   )
   tbl.add_column("ID",            justify="center", style="cyan",   width=4)
   tbl.add_column("Role",          justify="center",                 width=12)
   tbl.add_column("Type",          justify="center",                 width=9)
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


       g_raw   = str(goal)    if goal    is not None else "[red]ERR[/red]"
       p_raw   = str(present) if present is not None else "[red]ERR[/red]"
       g_deg   = f"{_raw_to_deg(goal):.2f}°"    if goal    is not None else "—"
       p_deg   = f"{_raw_to_deg(present):.2f}°" if present is not None else "—"
       spd_str = str(speed & 0x3FF) if speed is not None else "[red]ERR[/red]"


       if moving is None:
           mov_str = "[red]ERR[/red]"
       else:
           mov_str = "[yellow]YES[/yellow]" if moving else "[dim]no[/dim]"


       role  = MOTOR_LABELS.get(sid, f"S{sid}")
       mtype = _motor_type_str(sid)
       tbl.add_row(str(sid), role, mtype, g_raw, g_deg, p_raw, p_deg, spd_str, mov_str)


   console.print(tbl)


# ── Save / Load ───────────────────────────────────────────────────────────────


def save_frames(frames: List[Dict]) -> None:
   if not frames:
       console.print("  [dim]No frames to save.[/dim]")
       return
   path_str = input("  Save to file  (e.g. nina_pose.json): ").strip()
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
           serializable.append(frame_data)
       payload = {
           "robot":       "nina",
           "motor_ids":   SERVO_IDS,
           "mx28_ids":    MX28_IDS,
           "mx106_ids":   MX106_IDS,
           "frame_count": len(serializable),
           "frames":      serializable,
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


# ── Port auto-detect  (Windows + Linux / Raspberry Pi / Jetson) ───────────────


def find_port() -> Optional[str]:
   # Pass 1 — match known USB-serial chip names in HWID/description (both OS)
   for p in serial.tools.list_ports.comports():
       combined = (p.hwid + p.description).lower()
       if any(k in combined for k in
              ("ft232", "ftdi", "usb serial", "usb2dynamixel", "ch340", "cp210", "pl2303")):
           return p.device


   # Pass 2 — Linux / Pi / Jetson fallback: take the first available ttyUSB or ttyACM
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
       description="Nina servo recorder — 11 motors (MX-28 + MX-106)",
       formatter_class=argparse.RawDescriptionHelpFormatter,
       epilog=(
           "Examples:\n"
           "  python nina_record.py                                 # interactive, auto-detect port\n"
           "  python nina_record.py --port /dev/ttyUSB0             # Linux explicit port\n"
           "  python nina_record.py --port COM3                     # Windows explicit port\n"
           "  python nina_record.py --file nina_pose.json           # load recording at startup\n"
           "  python nina_record.py --file nina_pose.json --loop    # headless: load & loop\n"
       ),
   )
   parser.add_argument("--port", "-p", type=str, default=None,
                       help="Serial port (e.g. COM3 or /dev/ttyUSB0). Auto-detected if omitted.")
   parser.add_argument("--file", "-f", type=str, default=None,
                       help="JSON recording to load at startup.")
   parser.add_argument("--loop", action="store_true",
                       help="Jump straight into loop mode after loading --file (useful for headless Pi).")
   parser.add_argument("--play", action="store_true",
                       help="Play the loaded --file once and exit.")
   parser.add_argument("--torque-off", action="store_true",
                       help="Disable torque on all motors and exit.")
   args = parser.parse_args()


   # ── --loop requires --file ────────────────────────────────────────────────
   if args.loop and not args.file:
       console.print("[red]ERROR:[/] --loop requires --file  (e.g. --file nina_pose.json --loop)")
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
               "  Or run once with:  [bold]sudo python nina_record.py[/bold]"
           )
       sys.exit(1)


   console.print(f"[green]Port open:[/green] {port}  @  {BAUDRATE} baud")
   time.sleep(BUS_SETTLE_SEC)
   _robust_clear(ser)


   # ── Startup ping — stabilises bus + flushes USB FIFO ─────────────────────
   console.print("[dim]Pinging all 11 motors...[/dim]")
   for sid in SERVO_IDS:
       ok     = ping(ser, sid)
       status = "[green]OK[/green]" if ok else "[red]NO RESPONSE[/red]"
       role   = MOTOR_LABELS.get(sid, f"S{sid}")
       mtype  = "MX-106" if sid in MX106_IDS else "MX-28 "
       console.print(f"  Motor {sid:>2} ({role} / {mtype}): {status}")
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
       console.print("\n[green]Torque disabled on all motors. Port closed. Bye.[/green]")
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
       "\n[bold cyan]─── Nina Recorder ───────────────────────────────[/bold cyan]\n"
       "  [bold]v[/bold]  — view current motor positions\n"
       "  [bold]m[/bold]  — move motor(s) now  (direct command)\n"
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
       "[dim]  Motors: MX-28 → 1,2,3,4,5,6,11 | MX-106 → 7,8,9,10[/dim]\n"
       "[dim]  Right hand (↑): 1,3,5,7,9 | Left hand (↑): 2,4,6,8,10 | Neck: 11[/dim]\n"
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



