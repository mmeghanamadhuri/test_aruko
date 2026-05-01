"""Read-only file serving for files under ``nina/actions/`` (audio clips, etc.)."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import HTTPException


def resolve_safe_media_path(actions_root: Path, relative: str) -> Path:
    """Resolve ``relative`` (e.g. ``audio/namaste.mp3``) under ``actions_root``."""
    rel = (relative or "").strip().lstrip("/").replace("..", "")
    if not rel:
        raise HTTPException(status_code=400, detail="empty path")
    root = actions_root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=403, detail="path escapes actions root") from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return target


def guess_content_type(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    return mt or "application/octet-stream"
