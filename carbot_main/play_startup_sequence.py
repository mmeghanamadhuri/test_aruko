#!/usr/bin/env python3
"""
Play a fixed list of motion JSON files via motion_server — resilient startup.

- Waits until motion_server answers ``status`` (STARTUP_SERVER_WAIT_SEC).
- Before each clip: ``stop`` + short pause so playback starts from a clean idle.
- Retries each file on transient errors (STARTUP_PLAY_RETRIES).
- Waits until ``is_playing`` is false (STARTUP_PLAYBACK_TIMEOUT_SEC per file).
- Sleeps STARTUP_JSON_DELAY_SEC between files (not after the last).

Environment
-----------
MOTION_HOST, MOTION_PORT
STARTUP_JSON_DELAY_SEC
STARTUP_JSON_FILES          — space-separated relative paths under MOTIONS_DIR
STARTUP_SERVER_WAIT_SEC     — default 90
STARTUP_PLAY_RETRIES        — default 6
STARTUP_PLAYBACK_TIMEOUT_SEC — default 180
STARTUP_POST_STOP_DELAY_SEC — pause after stop() before play, default 0.2
"""

from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from vision.motion_client import motion_rpc  # noqa: E402


def _wait_server(host: str, port: int, max_wait: float) -> bool:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        r = motion_rpc(host, port, {"cmd": "status"}, timeout=3.0)
        if r and r.get("status") == "ok":
            return True
        time.sleep(0.4)
    return False


def _wait_playback_done(host: str, port: int, timeout: float) -> bool:
    """
    Wait until motion_server has finished the current clip.

    Do **not** treat the first ``is_playing: false`` as completion — that often
    races the play thread and makes the client fire ``stop`` + the next ``play``
    while the first file is still starting, so only the first motion appears to run.
    We require ``is_playing`` true at least once, then false (normal path), or a
    short grace path for clips that finish faster than our poll interval.
    """
    deadline = time.monotonic() + timeout
    saw_playing = False
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        r = motion_rpc(host, port, {"cmd": "status"}, timeout=3.0)
        if not r or r.get("status") != "ok":
            time.sleep(0.03)
            continue
        playing = bool(r.get("is_playing"))
        if playing:
            saw_playing = True
        elif saw_playing:
            return True
        time.sleep(0.03)

    # Never saw True: either failed start or one-frame clip finished between polls.
    if not saw_playing:
        time.sleep(0.18)
        r = motion_rpc(host, port, {"cmd": "status"}, timeout=3.0)
        if r and r.get("status") == "ok" and not r.get("is_playing", True):
            return True
    return False


def _stop_idle(host: str, port: int, post_delay: float) -> None:
    motion_rpc(host, port, {"cmd": "stop"}, timeout=8.0)
    time.sleep(max(0.0, post_delay))


def _play_once(host: str, port: int, rel: str) -> tuple[bool, str]:
    r = motion_rpc(host, port, {"cmd": "play", "file": rel, "loop": False}, timeout=12.0)
    if not r:
        return False, "no response from motion_server"
    if r.get("status") == "error":
        return False, str(r.get("error", r))
    if r.get("status") != "started" and r.get("status") != "ok":
        return False, f"unexpected play response: {r}"
    return True, ""


def main() -> None:
    host = os.environ.get("MOTION_HOST", "127.0.0.1")
    port = int(os.environ.get("MOTION_PORT", "5000"))
    delay = float(os.environ.get("STARTUP_JSON_DELAY_SEC", "2"))
    server_wait = float(os.environ.get("STARTUP_SERVER_WAIT_SEC", "90"))
    retries = max(1, int(os.environ.get("STARTUP_PLAY_RETRIES", "6")))
    playback_timeout = float(os.environ.get("STARTUP_PLAYBACK_TIMEOUT_SEC", "180"))
    post_stop = float(os.environ.get("STARTUP_POST_STOP_DELAY_SEC", "0.2"))

    files_raw = os.environ.get(
        "STARTUP_JSON_FILES",
        "actions/short.json",
    )
    # Allow commas (single Docker -e token) or spaces; strip CR from Windows line endings.
    norm = files_raw.replace("\r", "").replace(",", " ")
    files = [f.strip() for f in norm.split() if f.strip()]

    if not files:
        print("play_startup_sequence: no files in STARTUP_JSON_FILES — skipping", file=sys.stderr)
        return

    print(f"  Waiting for motion_server at {host}:{port} …")
    if not _wait_server(host, port, server_wait):
        print("  ✖ motion_server not reachable (status RPC failed).", file=sys.stderr)
        sys.exit(1)

    for i, rel in enumerate(files):
        print(f"  ▶ ({i + 1}/{len(files)}) {rel}")
        ok_final = False
        last_err = ""
        for attempt in range(1, retries + 1):
            _stop_idle(host, port, post_stop)
            ok, err = _play_once(host, port, rel)
            if not ok:
                last_err = err
                print(f"     attempt {attempt}/{retries} play failed: {err}", file=sys.stderr)
                time.sleep(0.35 * attempt)
                continue
            if not _wait_playback_done(host, port, playback_timeout):
                last_err = "playback timeout (is_playing never cleared)"
                print(f"     attempt {attempt}/{retries} {last_err}", file=sys.stderr)
                time.sleep(0.35 * attempt)
                continue
            ok_final = True
            break

        if not ok_final:
            print(f"  ✖ Giving up on {rel}: {last_err}", file=sys.stderr)
            sys.exit(1)

        if i < len(files) - 1:
            time.sleep(delay)

    _stop_idle(host, port, post_stop)
    print("  Startup motion sequence OK.")


if __name__ == "__main__":
    main()
