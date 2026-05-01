"""Remove manifest entries and optional recording/audio files (no Dynamixel bus)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def delete_manifest_action(
    manifest_path: Path,
    actions_dir: Path,
    action_name: str,
    *,
    delete_recording: bool = True,
    delete_audio: bool = False,
) -> Dict[str, Any]:
    """Mirror ``ActionRunner.delete_action`` without hardware initialization."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actions = manifest.setdefault("actions", {})
    if action_name not in actions:
        raise ValueError(f"Action '{action_name}' is not in the manifest.")

    entry = actions[action_name]
    rel_file = _extract_file(entry)
    rel_audio: Optional[str] = None
    if isinstance(entry, dict):
        audio_val = entry.get("audio")
        if isinstance(audio_val, str) and audio_val.strip():
            rel_audio = audio_val.strip()

    del actions[action_name]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    deleted_recording: Optional[str] = None
    if delete_recording and rel_file:
        recording_path = actions_dir / rel_file
        if recording_path.exists() and recording_path.is_file():
            try:
                recording_path.unlink()
                deleted_recording = str(recording_path)
            except OSError:
                deleted_recording = None

    deleted_audio: Optional[str] = None
    skipped_audio_shared_with: List[str] = []
    if delete_audio and rel_audio:
        others = [
            other_name
            for other_name, other_entry in actions.items()
            if isinstance(other_entry, dict)
            and isinstance(other_entry.get("audio"), str)
            and other_entry["audio"].strip() == rel_audio
        ]
        if others:
            skipped_audio_shared_with = others
        else:
            audio_path = actions_dir / rel_audio
            if audio_path.exists() and audio_path.is_file():
                try:
                    audio_path.unlink()
                    deleted_audio = str(audio_path)
                except OSError:
                    deleted_audio = None

    return {
        "removed_from_manifest": True,
        "deleted_recording": deleted_recording,
        "deleted_audio": deleted_audio,
        "skipped_audio_shared_with": skipped_audio_shared_with,
    }


def _extract_file(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("file", "") or "")
    return str(entry)
