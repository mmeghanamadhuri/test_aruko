# Desktop `sirena_ui` vs Android companion — inventory and gaps

The Jetpack app is a **remote client** to **`nina-link`**. This document tracks **screen-by-screen** parity: layout regions, controls, and wiring status. Update it when shipping parity fixes.

**Legend:** Present | Partial | Missing | Stub (UI only, no robot HTTP)

## Shell (`main_window` / `HeaderBar` / `Sidebar` / `StatusBar`)

| Region | Desktop | Android | Notes |
|--------|---------|---------|-------|
| Red header + centered title | `HeaderBar` | `TopAppBar` (primary) | Match title strings per section |
| Tray: Wi‑Fi, battery, clock | Unicode glyphs + live clock | **Present** — clock + tray glyphs + link indicator |
| Left nav order + labels | `NAV_ITEMS` (8) | `SIRENA_NAV_ITEMS` | Same keys/order |
| Sidebar brand + version footer | Logo + `v0.4` · host | Nav rail + **version footer** |
| Charcoal footer dots | Bus / Wi‑Fi / Battery / Voice + labels | **Present** — labeled dots + warn/ok colors |
| Right footer hint | Muted text | Daemon host / link |

## Home (`home_screen.py` → `SirenaHomeScreen.kt`)

| Region | Desktop | Android |
|--------|---------|---------|
| Breadcrumb | Nina › Home | **Present** |
| Hero + chips + CTAs | Image, 3 chips, Play / Record | **Present** — chips use live hints where possible |
| Quick actions grid | `QUICK_ACTIONS` 4×2 | **Present** (`SIRENA_QUICK_ACTIONS`) |
| System overview card | Bus, Camera, Lidar, Battery, Wi‑Fi + “Tap Health” | **Present** — aligned labels + health poll |

## Drive (`drive_screen.py` → `SirenaDriveScreen.kt`)

| Region | Desktop | Android |
|--------|---------|---------|
| Top pills | Autonomous + BLDC | **Present** |
| Camera card + preview pill | Front camera + HUD | **Present** — HUD Speed / Heading / Distance / Battery |
| HUD metrics | From `DriveController.state` + slam | **Partial** — speed from slider; pose from SLAM status when enabled; battery from health |
| Manual card | Title row + Auto toggle | **Present** — autonomy toggle on manual card |
| D-pad + speed | D-pad, slider, % pill | **Present** |
| Wheels Flip L / R | `set_invert_*` + persist | **Present** — `POST /v1/robot/drive/invert` + status fields |
| Brake / Reverse / E‑STOP | Same row | **Present** |
| Keyboard hint | WASD line | **Present** (touch-focused copy) |

## Vision (`vision_screen.py` → `SirenaVisionScreen.kt`)

| Region | Desktop | Android |
|--------|---------|---------|
| Breadcrumb + sections | Cards for pipeline / face / object | **Partial** — HTTP-backed controls; layout tightened with section titles |

## Perception (`perception_screen.py` → `SirenaPerceptionScreen.kt`)

| Region | Desktop | Android |
|--------|---------|---------|
| Fusion view + pills | Multi-sensor | **Partial** — bridge-gated cards and pills aligned |

## Map (`map_screen.py` → `SirenaMapScreen.kt`)

| Region | Desktop | Android |
|--------|---------|---------|
| Breadcrumb + sensor pills | Top row | **Partial** |
| Grid + side stack | Occupancy + autonomy | Existing Map screen + save map |

## Actions / Settings / Health

See [`ANDROID_SIRENA_PARITY.md`](ANDROID_SIRENA_PARITY.md) and [`android_sirena_parity.manifest.json`](android_sirena_parity.manifest.json). Health uses **`GET /v1/robot/health`**; dynamixel donut remains desktop-only until a bus HTTP bridge exists.

## Hardware vs layout

Jetson-side errors (**USB**, **pyrealsense2**, **GPIO**, empty SLAM) appear in **daemon JSON/logs**, not fixed by Android-only layout. See [`COMPANION_APP.md`](COMPANION_APP.md).

## Logs

```bash
adb logcat -s NinaCompanion:I NinaCompanion:W NinaCompanion:E OkHttp:I
```
