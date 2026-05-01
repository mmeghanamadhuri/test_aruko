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

See also [`docs/COMPANION_APP.md`](../docs/COMPANION_APP.md).
