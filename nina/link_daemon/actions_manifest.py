"""Parse ``manifest.json`` for HTTP listing — no Dynamixel access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


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
