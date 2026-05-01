"""Parse ``manifest.json`` for HTTP listing — no Dynamixel access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def list_recordings_on_disk(manifest_path: Path) -> List[Dict[str, Any]]:
    """``recordings/*.json`` next to ``manifest.json`` — no Dynamixel access."""
    rd = manifest_path.parent / "recordings"
    if not rd.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(rd.glob("*.json")):
        try:
            st = p.stat()
            out.append(
                {
                    "file": f"recordings/{p.name}",
                    "name": p.stem,
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        except OSError:
            continue
    return out


def load_manifest_actions(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("actions") or {}
    out: List[Dict[str, Any]] = []
    for name, entry in raw.items():
        if isinstance(entry, str):
            out.append(
                {
                    "name": name,
                    "file": entry,
                    "audio": None,
                    "audio_offset": None,
                }
            )
        elif isinstance(entry, dict):
            out.append(
                {
                    "name": name,
                    "file": entry.get("file"),
                    "audio": entry.get("audio"),
                    "audio_offset": entry.get("audio_offset"),
                }
            )
    out.sort(key=lambda x: str(x.get("name", "")).lower())
    return out
