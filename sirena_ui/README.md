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

Manifest entries can be either a string (`"namaste.json"`) or a dict:

```json
{
  "namaste": {
    "file": "namaste.json",
    "audio": "audio/namaste.mp3",
    "audio_offset": 2.0
  }
}
```

Generate or tune audio with the helper script:

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
```
