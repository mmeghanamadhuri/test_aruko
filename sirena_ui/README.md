# Sirena Control Center

Desktop app (PyQt5) that fronts the Nina robot on the Jetson's 10.1"
touchscreen. It uses a persistent left sidebar, a centred red header,
and a charcoal status bar so the experience feels like a polished
consumer cockpit.

## Information architecture

```
+-- Home        Quick-action dashboard with Nina photo, status strip
+-- Drive       Manual BLDC control: virtual D-pad, speed slider, brake
+-- Vision      USB camera feed + face / object recognition controls
+-- Map         SLAM occupancy grid, sensor health, auto-dock
+-- Actions     Existing record / play / audio - now in one screen
|     +-- Playback   list registered actions, smooth replay (+ optional audio)
|     +-- Record     release torque, capture frames, save into manifest
|     +-- Audio      gTTS author / tune / remove per-action audio clips
+-- Settings    Sub-sidebar: General / Network / Display / Audio / Privacy
|                Autodock / Voice (ESP) / Power / OTA Update
+-- Health      Donut + 13-row subsystem table, Run-all-checks
```

The first release ships fully working **Home**, **Actions** and
**Health** flows. **Drive**, **Vision**, **Map**, **Settings** and
the non-Dynamixel rows on **Health** are polished UI scaffolds with
in-process stubs (`workers/drive_stub.py`, etc.) so the firmware
team can swap each stub for a real driver without touching the UI.

## Action audio

Action JSON files live in `nina/actions/recordings/` (the `Record`
sub-tab writes there too). Manifest entries can be either a string
(`"recordings/namaste.json"`) or a dict:

```json
{
  "namaste": {
    "file": "recordings/namaste.json",
    "audio": "audio/namaste.mp3",
    "audio_offset": 2.0
  }
}
```

### From the GUI (Actions -> Audio)

Pick an action from the dropdown and:

- Type the words to speak (defaults to the action name).
- Pick a voice preset (US English by default, plus UK, Australian, Indian, Hindi, etc.).
- Set the **audio offset** (seconds the runtime waits after motion
  starts before firing the clip).
- **Preview** the existing clip, **Generate & Save** a new one,
  **Save offset** without re-generating, or **Remove** the audio.

The Playback sub-tab still shows the audio summary on each row and
exposes an **Audio** shortcut that jumps straight to the editor with
the right action pre-selected.

The MP3 is rendered with gTTS (needs internet on the Jetson the first
time you click *Generate*), saved to
`nina/actions/audio/<action>.mp3`, and the manifest is updated
atomically. Install gTTS with:

```bash
pip install --user gTTS
sudo apt install -y mpg123    # so the generated MP3 can be played
```

### From the CLI

```bash
# Generate the MP3 with gTTS and register it in the manifest
python3 scripts/generate-action-audio.py namaste

# Tune the offset later without re-generating audio
python3 scripts/generate-action-audio.py namaste --offset 2.5 --skip-tts
```

## Install dependencies on Jetson Nano

```bash
sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg
# Or, inside a venv:
pip install -r sirena_ui/requirements.txt
```

## One-time permissions (no `sudo` at runtime)

The app deliberately avoids `sudo` - running a Qt GUI as root breaks
your X11/Wayland session, leaves root-owned files in your home, and
defeats the security model. Instead, do these once:

```bash
# Serial port (Dynamixel bus on /dev/ttyUSB0)
sudo usermod -aG dialout $USER

# Optional: drops FTDI latency_timer to 1ms so reads are reliable
sudo bash scripts/install-ftdi-udev.sh

# Make sure the repo is owned by your user (not root from a sudo-clone)
sudo chown -R $USER:$USER ~/Nvidia-jetson-platform
```

Then **log out and log back in** (a reboot is the simplest test) so
the new `dialout` group membership applies to your desktop session.
Verify with:

```bash
groups | grep dialout
ls -l /dev/ttyUSB0       # should show "crw-rw---- root dialout"
```

If recording or playback ever shows "Permission denied", the
in-app error message now tells you exactly which fix to apply.

## Add the icon to the Jetson home screen

```bash
./scripts/install-sirena-desktop.sh
```

This drops a `Sirena.desktop` launcher into both the application menu
and the user's Desktop folder, pointing the Exec line at this repo and
this venv. Re-run the script after moving the repo.

## Launch from a terminal

```bash
PYTHONPATH=. python3 -m sirena_ui
```

## Threading model

- `NinaService` owns the single `DynamixelManager` and exposes a
  `bus_lock` (`threading.RLock`).
- `PlaybackWorker` and `RecordWorker` are `QThread`s that acquire the
  bus lock for their duration, so they can never race on the serial
  port.
- The UI never touches the bus directly; it only signals workers to
  start/stop and reads progress over Qt signals.
- Lazy screen construction: each screen is built the first time a
  user navigates to it, so launch is fast.

## File layout

```
sirena_ui/
  __main__.py             # entry point
  main_window.py          # red header / charcoal sidebar / charcoal footer
  styles.py               # v2 theme tokens + Qt stylesheet
  assets/                 # logo, Nina photo, app icon
  screens/
    home_screen.py        # dashboard
    actions_screen.py     # Playback / Record / Audio sub-tabs
    drive_screen.py       # BLDC manual control (stub)
    vision_screen.py      # camera + recognition (stub)
    map_screen.py         # SLAM (stub)
    settings_screen.py    # sub-sidebar with 9 categories
    health_screen.py      # donut + 13-row subsystem table
  widgets/
    sidebar.py            # persistent dark nav
    header_bar.py         # red top bar with clock / wifi / battery
    status_bar.py         # charcoal footer with status dots
    common.py             # Card, CardTitle, Pill, Breadcrumb, ...
    nina_image_panel.py   # left rail of the Actions screen
    playback_panel.py
    record_panel.py
    audio_panel.py        # action picker + audio editor
    audio_editor_dialog.py# voice presets shared with the panel
    dpad.py               # virtual D-pad on the Drive screen
    donut_gauge.py        # health donut with Nina photo in the hole
  workers/
    nina_service.py       # DynamixelManager + ActionRunner facade
    playback_worker.py
    record_worker.py
    audio_gen_worker.py   # gTTS rendering off the UI thread
    drive_stub.py         # in-process state machine until BLDC lands
    health_collector.py   # subsystem statuses for the Health screen
    error_hints.py        # turn raw errors into actionable Jetson tips
```
