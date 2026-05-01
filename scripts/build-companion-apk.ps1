# Build a sideloadable Sirena UI companion APK (release build signed with debug keystore).
# Requires JDK 17 + Android SDK. Easiest path: open `android/` in Android Studio → Build → Build Bundle(s) / APK(s) → Build APK(s).
#
# From repo root, if Gradle wrapper exists:
#   .\scripts\build-companion-apk.ps1
#
# Output: android\app\build\outputs\apk\release\app-release.apk

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Android = Join-Path $Root "android"
$Gradlew = Join-Path $Android "gradlew.bat"

if (-not (Test-Path $Android)) {
    Write-Error "Missing android folder: $Android"
}

Push-Location $Android
try {
    if (Test-Path $Gradlew) {
        Write-Host "Building release APK..."
        & .\gradlew.bat assembleRelease --no-daemon
        $apk = Join-Path $Android "app\build\outputs\apk\release\app-release.apk"
        if (Test-Path $apk) {
            Write-Host ""
            Write-Host "OK: $apk"
            Write-Host "Share this file for sideload install (Settings → allow unknown sources)."
        }
    }
    else {
        Write-Host "No Gradle wrapper in android/. Generate it once:"
        Write-Host "  - Open the project in Android Studio (opens android folder), or"
        Write-Host "  - Install Gradle and run: cd android; gradle wrapper"
        Write-Host "Then re-run this script."
        exit 1
    }
}
finally {
    Pop-Location
}
