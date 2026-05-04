# Nina Companion (Android)

Jetpack Compose app for provisioning the Jetson over the **nina-link** HTTP API (Wi‑Fi AP / home network). Theme aligns with Sirena UI (red `#c8102e`, charcoal / cloud).

## Open in Android Studio

1. **File → Open** and select this folder: `nina-app/android` (the directory that contains `settings.gradle.kts`).
2. Wait for Gradle sync. If Android Studio asks to use the Gradle wrapper or download Gradle **8.7**, accept.
3. Enable **Developer options** and **USB debugging** on your tablet; connect USB (or use wireless debugging).
4. Choose your device in the toolbar and click **Run**.

If Gradle wrapper files (`gradlew`, `gradlew.bat`) are missing, Android Studio creates them on first sync, or install Gradle locally and run:

```bash
gradle wrapper --gradle-version 8.7
```

(from this directory).

## Defaults

- **minSdk 26**, **compileSdk 34**
- Default Jetson URL: `http://10.42.0.1:8787` (NetworkManager hotspot on Jetson; use **Setup** if your gateway differs, e.g. `192.168.4.1` on some tether-style subnets).
- Cleartext HTTP is allowed for local robot communication (`network_security_config`).

## Project layout

- `app/src/main/java/com/sirena/nina/companion/` — UI, `CompanionViewModel`, `LinkClient`
- `app/src/main/java/.../data/Prefs.kt` — DataStore for URL and bearer token

## Shareable APK (sideload)

After **Build → Build APK(s)** (release): outputs go to `app/build/outputs/apk/release/app-release.apk`. Release builds use the **debug signing config** so you can install on any device without a Play Console key (internal use only). Scripts: [`scripts/build-companion-apk.ps1`](../scripts/build-companion-apk.ps1) / [`scripts/build-companion-apk.sh`](../scripts/build-companion-apk.sh) if `gradlew` exists.

See also [`docs/COMPANION_APP.md`](../docs/COMPANION_APP.md) and [`docs/ANDROID_SIRENA_PARITY.md`](../docs/ANDROID_SIRENA_PARITY.md) (desktop vs companion feature matrix). **What the tablet cannot mirror yet** (Health table, full Settings persist, map save, etc.): [`docs/ANDROID_SIRENA_GAPS.md`](../docs/ANDROID_SIRENA_GAPS.md). When `sirena_ui` changes labels, nav, or tiles, follow the **Hook** section in **ANDROID_SIRENA_PARITY** and [`docs/android_sirena_parity.manifest.json`](../docs/android_sirena_parity.manifest.json).

**Debug logs** (HTTP failures log at WARN from `LinkClient`):

`adb logcat -s NinaCompanion:I NinaCompanion:W NinaCompanion:E`

## Jetson dependencies (not built into the APK)

Map / SLAM / autonomy / vision require **`nina-link`** on the robot with **`sirena_ui/requirements-headless.txt`** installed into **`.venv-link`** (plus systemd bridge env vars). Minimal pip (`requirements-link.txt`) is enough for Wi‑Fi + pairing only.

Quick path on the Jetson from repo root:  
`./scripts/install-sirena-companion-jetson.sh --with-sirena-headless`
(or `./scripts/update-nina-link-jetson.sh --sirena-headless --restart` after a normal install). On the robot, **`./scripts/verify-nina-link-companion.sh`** checks `/health`, capabilities, USB nodes, and logs. Details: **COMPANION_APP.md**, **ANDROID_SIRENA_PARITY.md** § Jetson `.venv-link`.
