# Android companion vs desktop `sirena_ui`

This document tracks **functional and navigation parity** between the Jetpack Compose companion (`android/app`) and the PyQt desktop app (`sirena_ui`). UI layout differs by design; behavior targets the same Jetson **nina-link** HTTP API where it exists.

## Shared HTTP surface

The companion talks to `nina-link` only (see [`nina/link_daemon/api.py`](../nina/link_daemon/api.py)). Endpoints include Wi‑Fi, pairing, drive, actions, vision (stream, options, detections, enroll, announce), session claim/release, and static media. When **`NINA_LINK_ENABLE_SLAM_BRIDGE`**, **`NINA_LINK_ENABLE_DEPTH_BRIDGE`**, and **`NINA_LINK_ENABLE_AUTONOMY_BRIDGE`** are set on the Jetson (see [`scripts/install-nina-link-jetson.sh`](../scripts/install-nina-link-jetson.sh)), Android uses **`/v1/slam/*`** for occupancy + pose, **`/v1/depth/stream`** for RealSense MJPEG, and **`/v1/autonomy/*`** for autonomous wander — matching the same stacks as desktop `sirena_ui` subject to hardware availability.

## Screen parity matrix

| Desktop (`sirena_ui`) | Android (Nina console) | Notes |
|----------------------|-------------------------|--------|
| Home | `SirenaHomeScreen` | Quick actions, live-ish overview from `/v1/status` + capabilities. |
| Drive | `SirenaDriveScreen` | Momentary drive + E-stop + MJPEG preview via `/v1/vision/stream`. HTTP drive is refused while autonomy is active (`POST /v1/autonomy/enabled`). |
| Vision | `SirenaVisionScreen` | Stream, face/object toggles, confidence + **Apply**, enrollment, announce; polling `/v1/vision/detections` for a short detection list. |
| Perception | `SirenaPerceptionScreen` | Three-pane layout: SLAM occupancy (`/v1/slam/occupancy`), RGB MJPEG, depth MJPEG when bridges are enabled on the Jetson. |
| Map | `SirenaMapScreen` | Occupancy bitmap + pose from `/v1/slam/snapshot` / occupancy bytes; autonomy toggle calls `/v1/autonomy/enabled`. |
| Actions | `SirenaActionsScreen` | Playback / Record / Audio aligned with link daemon. |
| Settings | `SirenaSettingsScreen` | Same categories as desktop; **Network** includes Jetson Wi‑Fi actions (parity with Setup tab). |
| Health | `SirenaHealthScreen` | Daemon `/health` + capabilities; full desktop hardware donut/table remains on-robot Sirena UI until bridged. |

## Launcher shell

- **Home / Networks / Setup** tabs provision the tablet; **Nina** opens the full console rail (same sections as desktop nav).
- Main **Home** shortcuts: **Networks** and **Setup** (replacing placeholder Carbot/System).

## Footer strip

Charcoal strip dots: **Bus** (link reachability), **Wi‑Fi** (daemon session ready), **Battery** (no tablet API yet — off), **Voice** (ON when `vision_bridge_enabled` in capabilities).

## Tests

- JVM unit test: `JsonCleanStringTest` (JSON helpers used across the HTTP client).

Run unit tests in Android Studio (**test** source set) or `./gradlew test` when the Gradle wrapper is present.
