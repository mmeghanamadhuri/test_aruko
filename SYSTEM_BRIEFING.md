# Carbot Vision System — Third-Party Briefing
## For: External Reviewer / Consultant
## Date: 2026-04-12 | Branch: `feature-carbot`

---

## 1. System Goal

This robot must autonomously:
1. **SEARCH** — pan/tilt its camera (Motors 6, 7) to scan for a button on a car window panel
2. **ALIGN** — center the camera directly on the button (Motors 6, 7 proportional control)
3. **APPROACH** — reach the arm toward the button (Motors 1–5 multi-joint lunge)
4. **DONE** — stop when close enough to physically press the button

---

## 2. Robot Anatomy: Motor Map

| Servo ID | Joint Name | Type | Approach Direction | Notes |
|---|---|---|---|---|
| **1** | Base Rotate | Absolute | **+** (increases) | Rotates arm left/right |
| **2** | Shoulder | Absolute | **−** (decreases) | Lifts/lowers arm |
| **3** | Elbow | Absolute | **+** (increases) | Extends arm forward |
| **4** | Wrist 1 | Absolute | **+** (increases) | Rotates wrist |
| **5** | Wrist 2 | Absolute | **+** (increases) | Rotates end-effector |
| **6** | Pan (Eye) | Relative | N/A — used for ALIGN | Camera left/right |
| **7** | Tilt (Eye) | Relative | N/A — used for ALIGN | Camera up/down |

> **Key finding**: The "reach forward" direction was derived from
> `carbot_main/actions/neutral.json` vs `carbot_main/actions/with_act.json`.
> S2 is the only joint that **decreases** to extend forward. All others increase.

---

## 3. Responsible Files

### A. Vision Brain (runs inside Docker on Jetson)

| File | Role |
|---|---|
| `vision/window_servo.py` | **Main state machine.** SEARCH → ALIGN → APPROACH → DONE logic. |
| `vision/config.py` | Reads all `VISION_*` environment variables. Defines `VisionConfig`. |
| `vision/detector.py` | YOLO wrapper. Runs inference, returns `ButtonDetection` objects. |
| `vision/motion_client.py` | Sends JSON RPC commands over a TCP socket to the motion server. |
| `vision/types.py` | Data classes: `ButtonDetection`, `BBox`, etc. |
| `vision/camera.py` | Opens the camera and reads frames. |
| `vision/mjpeg_server.py` | Serves a live MJPEG preview on port 8080 for human monitoring. |

### B. Motion Server (runs on Jetson host, outside Docker)

| File | Role |
|---|---|
| `carbot_main/motion_server.py` | **TCP server.** Handles RPC commands and sends Dynamixel serial packets. |
| `carbot_main/carbot_record.py` | Low-level Dynamixel helpers: `read_reg`, `write_reg`, `_s16`, `_u16`, `play_frames`. |

### C. Configuration

| File | Role |
|---|---|
| `carbot.sh` | **Master config.** All tuning knobs as environment variables. Run this to start. |
| `carbot_main/actions/neutral.json` | The robot's home/resting pose (servo positions). |
| `carbot_main/actions/with_act.json` | Example of the arm in an extended/reaching pose. |

---

## 4. How ALIGN Works (Motors 6, 7)

```
Camera sees the button at pixel (cx, cy)
  Error = (cx - half_width_px, cy - half_height_px)

  If error > 120px  →  use KP_FAR = 0.85 (aggressive correction)
  If error > 40px   →  use KP_MID = 0.85
  Otherwise         →  use KP_NEAR = 0.85 (gentle fine-tuning)

  Command = KP × error × invert_flag
  Applied to Motor 6 (pan) and Motor 7 (tilt)

  Stability: Need 4 consecutive frames with error < 50px (DEADZONE)
  Once stable → transition to APPROACH phase
```

**Current tuning:** `KP_FAR=0.85 | KP_MID=0.85 | KP_NEAR=0.85 | DEADZONE=50px | STABLE_FRAMES=4`

---

## 5. How APPROACH Works (Motors 1–5)

```
APPROACH is triggered after ALIGN is stable (4 frames within 50px deadzone)

Per step:
  1. Compute area = (bbox_w × bbox_h) / (frame_w × frame_h)
  2. If area >= APPROACH_AREA (0.25) → DONE (button is close enough)
  1. Arm joints (1, 2, 3, 4) take a limited forward lunge based on `dist_factor`.
  2. The arm **PAUSES** and waits.
  3. Triple-Axis Centering: Motors 5, 6, and 7 work together to re-center the target perfectly.
  4. Stability Check: Once 5, 6, and 7 are centered (stable for 4 frames), the arm takes the NEXT step.

Active Recovery:
  - If target lost during approach → Wrist (Motor 5) sweeps `+60, -120, +180...` until found.

**Current base deltas**: `S1:+15 | S2:-20 | S3:+50 | S4:+40 | S5:+15`
*(Smaller deltas prevent FOV loss while Active Stabilization tracks the movement)*

---

## 6. Current State / Known Issues

| Issue | Status |
|---|---|
| Heap crash on startup | ✅ Fixed — CUDA context initialized before OpenCV |
| Motors 6,7 not moving | ✅ Fixed — `_u16` helper now imported correctly |
| Approach arm fighting itself | ✅ Fixed — direction signs corrected per anatomy |
| Approach "stalling" | ✅ Fixed — switched from slow Playback Engine to direct `multi_servo_move` |
| Only 2 motors used | ✅ Just fixed — now uses all 5 arm joints |
| Arm not reaching far enough | 🔧 In Progress — increase deltas or add actuator |

---

## 7. Questions for External Reviewer

1. **Is the multi-joint vector physically correct?** Given the neutral vs. extended pose, are directions `[+25, -35, +90, +80, +30]` for joints `[1, 2, 3, 4, 5]` anatomically sound?

2. **Should we use Inverse Kinematics?** Instead of hard-coded deltas, we could compute joint angles from camera-estimated 3D distance. Is that worth implementing?

3. **Stability condition**: Currently 4 frames within 50px. Is this too strict (preventing reach) or too loose (reaching while still moving)?

4. **Done condition**: We stop when the button occupies 25% of the frame area. Is this the right stopping condition for button pressing?

---

## 8. How to Run

```bash
# Terminal 1 — on the Jetson (host system, NOT Docker)
cd /home/jnx/Prajwal/feature-carbot/carbot_main
python3 motion_server.py

# Terminal 2 — start the Docker container
# (this runs window_servo.py inside Docker with the env from carbot.sh)
cd /home/jnx/Prajwal/feature-carbot
./carbot.sh

# Monitor the live camera feed at:
# http://<jetson-ip>:8080/
```

Expected log sequence:
```
[INFO] Vision runtime=yolo (ultralytics local weights: best.pt)
[INFO] Warming up detector...
[INFO] Detector warm-up complete. Initializing vision stack...
[INFO] MJPEG preview http://0.0.0.0:8080/
[INFO] Detected front_right_window -> ALIGN
[INFO] Stable Check: 1/4 (err=38.3px)
[INFO] Stable Check: 2/4 (err=30.1px)
[INFO] Stable Check: 3/4 (err=22.5px)
[INFO] Stable Check: 4/4 (err=18.2px)
[INFO] Stability Achieved -> APPROACH
[INFO] Approach Step: Dist:2.3x | S1:23->81 | S2:2616->2493 | S3:1360->1567 | S4:1384->1568 | S5:1125->1194
[INFO] Approach Step: Dist:1.8x | S1:81->126 | ...
[INFO] area=0.253 >= target=0.250 -> DONE
```
