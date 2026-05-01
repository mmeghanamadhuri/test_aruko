#!/usr/bin/env bash
# Build release APK for sideload sharing (see android/app/build.gradle.kts — release uses debug signing).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/android"
if [[ -x ./gradlew ]]; then
  ./gradlew assembleRelease --no-daemon
  APK="${ROOT}/android/app/build/outputs/apk/release/app-release.apk"
  if [[ -f "${APK}" ]]; then
    echo ""
    echo "OK: ${APK}"
  fi
else
  echo "No gradlew — open android/ in Android Studio and use Build > Build APK(s), or run: gradle wrapper" >&2
  exit 1
fi
