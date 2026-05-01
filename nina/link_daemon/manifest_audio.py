"""Manifest-only audio helpers for nina-link (no Dynamixel bus)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from nina.services.audio_generator import AudioGenerator, AudioGeneratorError


def _load_manifest(manifest_path: Path) -> Dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _save_manifest(manifest_path: Path, data: Dict[str, Any]) -> None:
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_action_audio_rel(manifest_path: Path, action_name: str) -> Optional[str]:
    manifest = _load_manifest(manifest_path)
    raw = (manifest.get("actions") or {}).get(action_name)
    if raw is None:
        raise ValueError(f"Action '{action_name}' is not in the manifest.")
    if isinstance(raw, dict):
        audio = raw.get("audio")
        if isinstance(audio, str) and audio.strip():
            return audio.strip()
    return None


def get_action_audio_info(
    manifest_path: Path, actions_dir: Path, action_name: str
) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    actions = manifest.get("actions") or {}
    if action_name not in actions:
        raise ValueError(f"Action '{action_name}' is not in the manifest.")
    entry = actions[action_name]
    rel: Optional[str] = None
    offset = 0.0
    if isinstance(entry, dict):
        audio = entry.get("audio")
        if isinstance(audio, str) and audio.strip():
            rel = audio.strip()
        off = entry.get("audio_offset")
        if isinstance(off, (int, float)):
            offset = max(0.0, float(off))
    clip_path = (actions_dir / rel) if rel else None
    clip_exists = bool(clip_path and clip_path.is_file())
    gerr = AudioGenerator.is_available()
    return {
        "action": action_name,
        "audio_rel": rel,
        "audio_offset": offset,
        "clip_file_exists": clip_exists,
        "gtts_error": gerr,
    }


def set_action_audio(
    manifest_path: Path,
    action_name: str,
    audio_name: Optional[str],
    audio_offset: Optional[float] = None,
) -> None:
    manifest = _load_manifest(manifest_path)
    actions = manifest.setdefault("actions", {})
    existing = actions.get(action_name)
    if existing is None:
        raise ValueError(f"Action '{action_name}' is not in the manifest.")

    if isinstance(existing, dict):
        file_name = str(existing.get("file", ""))
        existing_offset = existing.get("audio_offset")
    else:
        file_name = str(existing)
        existing_offset = None

    if not audio_name or not str(audio_name).strip():
        actions[action_name] = file_name
    else:
        entry: Dict[str, Any] = {"file": file_name, "audio": str(audio_name)}
        if audio_offset is not None:
            if audio_offset > 0:
                entry["audio_offset"] = float(audio_offset)
        elif isinstance(existing_offset, (int, float)) and float(existing_offset) > 0:
            entry["audio_offset"] = float(existing_offset)
        actions[action_name] = entry

    _save_manifest(manifest_path, manifest)


def set_action_audio_offset_only(
    manifest_path: Path, action_name: str, audio_offset: float
) -> None:
    rel = get_action_audio_rel(manifest_path, action_name)
    if not rel:
        raise ValueError(
            f"Action '{action_name}' has no audio clip; generate one first."
        )
    set_action_audio(manifest_path, action_name, rel, audio_offset=audio_offset)


def clear_action_audio_mapping(manifest_path: Path, action_name: str) -> None:
    set_action_audio(manifest_path, action_name, None)


def generate_action_audio_clip(
    manifest_path: Path,
    actions_dir: Path,
    action_name: str,
    text: str,
    *,
    lang: str = "en",
    tld: str = "com",
    offset: float = 0.0,
) -> Path:
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot generate audio: text is empty.")
    manifest = _load_manifest(manifest_path)
    if action_name not in (manifest.get("actions") or {}):
        raise ValueError(f"Action '{action_name}' is not in the manifest.")
    err = AudioGenerator.is_available()
    if err:
        raise AudioGeneratorError(err)
    rel = f"audio/{action_name}.mp3"
    out_path = actions_dir / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    AudioGenerator.generate(text, out_path, lang=lang, tld=tld)
    off_arg: Optional[float] = offset
    set_action_audio(manifest_path, action_name, rel, audio_offset=off_arg)
    return out_path
