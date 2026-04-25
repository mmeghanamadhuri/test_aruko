# Sirena Control Center

Desktop app (PyQt5) that fronts the Sirena robots. Today it ships one
robot screen — Nina — with two tabs:

- **Playback** lists every action registered in
  `nina/actions/manifest.json` and plays them with the smooth
  interpolated pipeline (`DynamixelManager.play_smooth`). If the
  manifest entry includes an `audio` clip, it is played alongside the
  motion; an optional `audio_offset` (seconds) delays the clip so it
  fires in sync with the gesture (e.g. when the hands meet during
  Namaste).
- **Record** releases torque, counts down, samples the arm at the
  requested rate, and saves the JSON under
  `nina/actions/recordings/`. A **Stop Recording** button cuts the
  capture early and saves whatever was already collected.

## Action audio

Action JSON files live in `nina/actions/recordings/` (the `Record`
tab writes there too). Manifest entries can be either a string
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

### From the GUI

Each row in the **Playback** tab shows the action's current audio
("Audio: namaste.mp3 - +2.00s" or "Audio: none") and exposes an
**Audio** button. The audio editor lets you:

- Type the words to speak (defaults to the action name).
- Pick a voice preset (Indian English female, US, UK, Hindi, etc.).
- Set the **audio offset** (seconds the runtime waits after motion
  starts before firing the clip).
- **Preview** the existing clip, **Generate & Save** a new one,
  **Save offset only** without re-generating, or **Remove** the audio.

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

The launcher screen is intentionally future-proofed (greyed-out
"Carbot" and "+ Add robot" tiles) so additional robots can be plugged
in by adding a new screen and a launcher tile.

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

## File layout

```
sirena_ui/
  __main__.py           # entry point
  main_window.py        # header bar / footer bar / screen stack
  styles.py             # Qt stylesheet (Sirena red + white)
  assets/               # logo, Nina render, generated app icon
  screens/
    launcher_screen.py  # "Choose a robot"
    nina_screen.py      # Playback + Record tabs
  widgets/
    robot_tile.py
    nina_image_panel.py
    playback_panel.py
    record_panel.py
  workers/
    nina_service.py     # DynamixelManager + ActionRunner facade
    playback_worker.py
    record_worker.py
    audio_gen_worker.py # gTTS rendering off the UI thread
  widgets/
    audio_editor_dialog.py  # in-app audio author / tuner
```
