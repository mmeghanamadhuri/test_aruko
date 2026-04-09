"""JSON-line TCP client for carbot ``motion_server`` (port 5000)."""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Dict, Optional

log = logging.getLogger("carbot.vision.motion_client")


def motion_rpc(
    host: str,
    port: int,
    payload: Dict[str, Any],
    timeout: float = 4.0,
) -> Optional[Dict[str, Any]]:
    """Send one command; return parsed JSON dict or None on failure."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            msg = json.dumps(payload) + "\n"
            sock.sendall(msg.encode("utf-8"))
            sock.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
            if not line:
                return None
            return json.loads(line)
    except socket.timeout:
        log.error("motion_rpc timeout %s:%s", host, port)
    except ConnectionRefusedError:
        log.error("motion_rpc refused %s:%s — is motion_server running?", host, port)
    except Exception as e:
        log.error("motion_rpc error: %s", e)
    return None
