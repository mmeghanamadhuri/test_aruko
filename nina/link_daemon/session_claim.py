"""Optional Jetson helper script for UI session takeover (e.g. stop Sirena kiosk)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("nina.link_daemon.session_claim")


def invoke_script(script: str, verb: str) -> Dict[str, Any]:
    """Run ``script <verb>`` where verb is ``claim`` or ``release``."""
    path = Path(script).expanduser()
    if not path.is_file():
        return {"ok": False, "error": f"script not found: {path}"}
    try:
        r = subprocess.run(
            [str(path), verb],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        ok = r.returncode == 0
        log.info(
            "session script %s %s -> rc=%s",
            path,
            verb,
            r.returncode,
        )
        return {
            "ok": ok,
            "returncode": r.returncode,
            "stdout": (r.stdout or "")[-2000:],
            "stderr": (r.stderr or "")[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "session script timed out"}
    except Exception as e:
        log.exception("session script")
        return {"ok": False, "error": str(e)}
