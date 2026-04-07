# Carbot Developer Documentation

This document outlines the software architecture, JSON protocol specifications, network integration, and physical hardware layouts powering the Carbot robotic platform (Jetson NX).

---

## 1. System Architecture

The robot executes logic using a clean separation between **Playback Core** and the **Network Server**. 

*   **`carbot_record.py` (The Engine)**: The primary standalone source of truth. Features a fully-interactive terminal menu for recording motor positions and loading JSON frames. Crucially, it speaks directly to the Dynamixel RS485 bus using raw hexadecimal packet building. 
*   **`motion_server.py` (The TCP Bridge)**: Stripped of direct hardware interpretation, this server safely listens on port `5000` over Ethernet. When instructed, it launches Python daemon threads that securely proxy parameters directly into `carbot_record.loop_frames` and `play_frames`. 
*   **`windows_carbot_cli.ps1` (The Client)**: A Windows PowerShell script pushing JSON-formatted strings via `.NET TcpClient`. Features `Invoke-CarbotMenu`, seamlessly exposing full Jetson control across a wireless local network.

---

## 2. Linear Actuator Implementation

The linear actuator (12V, 188N, 5mm/sec flat-rate, 100mm stroke) operates independently of the RS485 Dynamixels by relying on a secondary **L298N Motor Driver** mapped to the Jetson's raw GPIOs.

### Hardware Wiring Layout (Jetson 40-Pin Header)
Because the mechanism is purely time-distance extrapolated (no PWM requirement), simplistic generic logic ports are used:
*   **L298N `IN3`** ➔ **Jetson Pin 35** (Native logic)
*   **L298N `IN4`** ➔ **Jetson Pin 37** (Native logic)
*   **L298N `GND`** ➔ **Jetson Pin 39** (Shared Ground Plane)
*   **L298N `ENB`** ➔ **Left Jumpered** to high (+5V rail) on the driver itself.

> [!IMPORTANT]
> **Floating Ground Safety Rule**
> The Black wire from the 12V SMPS *and* the GND jumper from Jetson Pin 39 must concurrently share the center `GND` screw terminal on the L298N block. Failure to overlap grounds results in floating base-voltage interference, causing erratic motor trigger behavior simply by touching proximity cables.

### Software Integration
*   The actuator is isolated in **`actuator.py`**. 
*   `test_actuator.py` provides independent physical module validation.
*   **Sequential Execution:** The actuator blocks the execution thread. A frame with a 40mm extension will force `carbot_record.py` to hard-halt for 8.0 seconds while the arm extends. Only afterwards will the Dynamixels process their target angles for that same frame.

---

## 3. JSON Motion Schema

Action files are strictly structured arrays consisting of chronological frame dictionaries. 

### Schema Definition
```json
[
  {
    "delay": 0.5,
    "duration": 1.0,
    "speed": 100,
    "actuator": {
      "action": "extend", 
      "distance_mm": 40
    },
    "servos": {
      "1": { "type": "absolute", "value": 1466 },
      "6": { "type": "relative", "diff": 1890, "sign": "+", "ref_pos": 1249 }
    }
  }
]
```

### Key Behaviors
*   **`delay`**: Time paused (seconds) before the frame sequence starts natively parsing hardware commands.
*   **`duration`**: Time paused (seconds) holding position immediately after the frame finishes transmitting its hardware coordinates entirely.
*   **`speed`**: Master transmission parameter clamping angular rotation velocities on the Dynamixels (0=Max, 1023=Slowest).
*   **`actuator`**: *(Optional)* Placed dynamically. Values for `"action"` support `"extend"` and `"retract"`. The `"distance_mm"` is securely parsed internally multiplying against the 5mm/sec physical limitation, clamping at `100mm`.
*   **`servos`**: 
    *   **Absolute (`1-5`)**: The literal absolute angular unit command transmitted to the motor.
    *   **Relative (`6-7`)**: The engine queries the physical RS485 bus to discover the motor's actual real-life angle mid-execution, and then subsequently maps the `"diff"` value locally using its respective signed multiplier. The `"ref_pos"` acts as auditing history mapped securely during recording.

---

## 4. Ethernet Command Endpoints

The robot processes JSON dictionary commands via port 5000 explicitly padded with explicit line endings (`\n`).

| Command | JSON Payload Example | Expected Behavior |
| --- | --- | --- |
| **Play** | `{"cmd": "play", "file": "actions/test.json", "loop": true}` | Initializes the global `carbot_record` thread natively. |
| **Stop** | `{"cmd": "stop", "mode": "hard"}` | Fires `threading.Event()` kill flags inside playback timers gracefully. |
| **Neutral**| `{"cmd": "neutral"}` | Calls `.stop("hard")` implicitly avoiding Deadlocks before issuing `Torque Enable: False` sequentially across all IDs. |
| **Freeze** | `{"cmd": "freeze"}` | Instigates `Torque Enable: True` forcing absolute rigid physical posture retention. |
| **Record** | `{"cmd": "record", "file": "test", "delay": 0.5}` | Forces the `_s16` parsing interpreter to generate relational/absolute state snapshots directly into the specified file over TCP. |
