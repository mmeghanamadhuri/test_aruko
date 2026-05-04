# Android companion vs desktop `sirena_ui`

This document tracks **functional and navigation parity** between the Jetpack Compose companion (`android/app`) and the PyQt desktop app (`sirena_ui`). UI layout differs by design; behavior targets the same Jetson **nina-link** HTTP API where it exists.

## Shared HTTP surface

The companion talks to `nina-link` only (see [`nina/link_daemon/api.py`](../nina/link_daemon/api.py)). Endpoints include Wi‑Fi, pairing, drive, actions, vision (stream, options, detections, enroll, announce), session claim/release, and static media. **SLAM occupancy, pose streaming, depth overlays, and autonomy on/off are not yet exposed as REST** from nina-link; Android mirrors desktop **intent** (toggles, controls) and shows clear placeholders until backend routes exist.

## Screen parity matrix

| Desktop (`sirena_ui`) | Android (Nina console) | Notes |
|----------------------|-------------------------|--------|
| Home | `SirenaHomeScreen` | Quick actions, live-ish overview from `/v1/status` + capabilities. |
| Drive | `SirenaDriveScreen` | Momentary drive + E-stop + MJPEG preview via `/v1/vision/stream`. Autonomy toggle is **local UI** until an autonomy API ships. |
| Vision | `SirenaVisionScreen` | Stream, face/object toggles, confidence + **Apply**, enrollment, announce; polling `/v1/vision/detections` for a short detection list. |
| Perception | `SirenaPerceptionScreen` | Three-pane layout; RGB via MJPEG when enabled. LiDAR/depth panels wait on daemon endpoints. |
| Map | `SirenaMapScreen` | Occupancy/pose **pending API**; autonomy/mapping controls reflect operator intent; status text polled from vision status as a placeholder for richer telemetry. |
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
