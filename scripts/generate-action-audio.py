#!/usr/bin/env python3
"""
Generate an audio clip for a Nina action using Google Text-to-Speech
(gTTS). Output is an MP3 saved at `nina/actions/audio/<name>.mp3` and
the manifest entry for that action is updated to point at it.

Examples:
    python3 scripts/generate-action-audio.py namaste
    python3 scripts/generate-action-audio.py namaste --text "Namaste, welcome"
    python3 scripts/generate-action-audio.py wave --lang en --tld co.in
    # Set/clear the per-action audio offset (seconds the runtime waits
    # after motion starts before firing the clip; tune to match the gesture).
    python3 scripts/generate-action-audio.py namaste --offset 2.0
    python3 scripts/generate-action-audio.py namaste --offset 0 --skip-tts

Requirements (one-time):
    pip install --user gTTS
    sudo apt install -y mpg123       # so the GUI / CLI can play .mp3

Run from the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Nina action audio with gTTS.")
    parser.add_argument("action", help="Action name to generate audio for (must exist in manifest).")
    parser.add_argument("--text", default=None, help="Text to speak (default: capitalized action name).")
    parser.add_argument("--lang", default="en", help="gTTS language code (default: en).")
    parser.add_argument(
        "--tld",
        default="co.in",
        help="Google TLD for accent selection (default: co.in for Indian-English female voice).",
    )
    parser.add_argument(
        "--no-register",
        action="store_true",
        help="Generate the file but skip updating the manifest entry.",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=None,
        help=(
            "Per-action audio offset in seconds (delay between motion start "
            "and audio playback). Use 0 to clear an existing offset."
        ),
    )
    parser.add_argument(
        "--skip-tts",
        action="store_true",
        help="Skip generating the MP3 (useful when you only want to update --offset).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    actions_dir = repo_root / "nina" / "actions"
    audio_dir = actions_dir / "audio"
    manifest_path = actions_dir / "manifest.json"

    if not args.skip_tts:
        try:
            from gtts import gTTS
        except ImportError:
            print("gTTS is not installed. Run: pip install --user gTTS", file=sys.stderr)
            return 1

        audio_dir.mkdir(parents=True, exist_ok=True)
        out_path = audio_dir / f"{args.action}.mp3"
        text = args.text or args.action.replace("_", " ").title()
        print(
            f"Generating audio for action '{args.action}': '{text}' "
            f"(lang={args.lang}, tld={args.tld})"
        )
        gTTS(text=text, lang=args.lang, tld=args.tld, slow=False).save(str(out_path))
        print(f"Saved {out_path} ({out_path.stat().st_size} bytes)")

    if args.no_register:
        return 0

    if not manifest_path.exists():
        print(f"manifest not found at {manifest_path}; skipping registration.", file=sys.stderr)
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actions = manifest.setdefault("actions", {})
    rel_audio = f"audio/{args.action}.mp3"

    existing = actions.get(args.action)
    if existing is None:
        print(
            f"Action '{args.action}' is not in the manifest. "
            "Add the action first (record-action --register) or pass --no-register."
        )
        return 0

    if isinstance(existing, dict):
        entry = dict(existing)
    else:
        entry = {"file": existing}

    if not args.skip_tts:
        entry["audio"] = rel_audio
    elif "audio" not in entry:
        print(
            "--skip-tts was set but the manifest entry has no existing audio mapping; "
            "nothing to update.",
            file=sys.stderr,
        )

    if args.offset is not None:
        if args.offset > 0:
            entry["audio_offset"] = float(args.offset)
        else:
            entry.pop("audio_offset", None)

    actions[args.action] = entry
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    audio_field = entry.get("audio", "<none>")
    offset_field = entry.get("audio_offset", 0.0)
    print(
        f"Updated manifest entry for '{args.action}' -> audio={audio_field}, "
        f"audio_offset={offset_field}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
