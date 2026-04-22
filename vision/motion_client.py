
# """JSON-line TCP client for carbot ``motion_server`` (port 5000).


# Uses a persistent connection per (host, port) pair so the vision loop does
# not open/close a new socket on every RPC call.  The connection is recreated
# automatically on any error, so it is safe to restart the server without
# restarting the vision process.
# """


# from __future__ import annotations


# import json
# import logging
# import socket
# import threading
# from typing import Any, Dict, Optional


# log = logging.getLogger("carbot.vision.motion_client")


# # ── Persistent connection registry ────────────────────────────────────────────
# # One entry per (host, port) — shared across all callers in the same process.
# _conns: Dict[tuple, "_PersistentConn"] = {}
# _conns_lock = threading.Lock()




# class _PersistentConn:
#     """Thread-safe persistent TCP connection to the motion server."""


#     def __init__(self, host: str, port: int, timeout: float = 4.0):
#         self.host = host
#         self.port = port
#         self.timeout = timeout
#         self._sock: Optional[socket.socket] = None
#         self._lock = threading.Lock()


#     def _connect(self) -> None:
#         if self._sock is not None:
#             try:
#                 self._sock.close()
#             except Exception:
#                 pass
#             self._sock = None
#         s = socket.create_connection((self.host, self.port), timeout=self.timeout)
#         s.settimeout(self.timeout)
#         # Disable Nagle — we send short JSON lines, want them flushed immediately.
#         s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
#         self._sock = s
#         log.debug("motion_client: connected to %s:%s", self.host, self.port)


#     def call(
#         self,
#         payload: Dict[str, Any],
#         *,
#         request_timeout: Optional[float] = None,
#     ) -> Optional[Dict[str, Any]]:
#         """Send one JSON-line RPC. ``request_timeout`` overrides the default for this call only."""
#         with self._lock:
#             for attempt in range(2):
#                 try:
#                     if self._sock is None:
#                         self._connect()
#                     t = self.timeout if request_timeout is None else float(request_timeout)
#                     if self._sock is not None:
#                         self._sock.settimeout(t)
#                     msg = json.dumps(payload) + "\n"
#                     self._sock.sendall(msg.encode("utf-8"))
#                     buf = b""
#                     while b"\n" not in buf:
#                         chunk = self._sock.recv(8192)
#                         if not chunk:
#                             raise ConnectionResetError("server closed connection")
#                         buf += chunk
#                     line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
#                     if not line:
#                         return None
#                     return json.loads(line)
#                 except (ConnectionResetError, BrokenPipeError, OSError) as e:
#                     log.warning("motion_client: connection lost (%s) — reconnecting", e)
#                     self._sock = None
#                     if attempt == 1:
#                         log.error("motion_client: reconnect failed for %s:%s", self.host, self.port)
#                         return None
#                 except socket.timeout:
#                     log.error("motion_rpc timeout %s:%s", self.host, self.port)
#                     # Don't reconnect on timeout — server may just be busy.
#                     return None
#                 except Exception as e:
#                     log.error("motion_rpc error: %s", e)
#                     self._sock = None
#                     return None
#         return None


#     def close(self) -> None:
#         with self._lock:
#             if self._sock is not None:
#                 try:
#                     self._sock.close()
#                 except Exception:
#                     pass
#                 self._sock = None




# def _get_conn(host: str, port: int, timeout: float) -> _PersistentConn:
#     key = (host, port)
#     with _conns_lock:
#         if key not in _conns:
#             _conns[key] = _PersistentConn(host, port, timeout)
#         return _conns[key]




# def motion_rpc(
#     host: str,
#     port: int,
#     payload: Dict[str, Any],
#     timeout: float = 4.0,
# ) -> Optional[Dict[str, Any]]:
#     """Send one command over a persistent connection; return parsed JSON or None."""
#     return _get_conn(host, port, timeout).call(payload, request_timeout=timeout)




# def close_motion_rpc_connection(host: str, port: int) -> None:
#     """Close the persistent socket for ``(host, port)`` (call when a vision run ends)."""
#     key = (host, port)
#     with _conns_lock:
#         conn = _conns.pop(key, None)
#     if conn is not None:
#         conn.close()

# """JSON-line TCP client for carbot ``motion_server`` (port 5000)."""


# from __future__ import annotations


# import json
# import logging
# import socket
# from typing import Any, Dict, Optional


# log = logging.getLogger("carbot.vision.motion_client")




# def motion_rpc(
#     host: str,
#     port: int,
#     payload: Dict[str, Any],
#     timeout: float = 4.0,
# ) -> Optional[Dict[str, Any]]:
#     """Send one command; return parsed JSON dict or None on failure."""
#     try:
#         with socket.create_connection((host, port), timeout=timeout) as sock:
#             msg = json.dumps(payload) + "\n"
#             sock.sendall(msg.encode("utf-8"))
#             sock.settimeout(timeout)
#             buf = b""
#             while b"\n" not in buf:
#                 chunk = sock.recv(8192)
#                 if not chunk:
#                     break
#                 buf += chunk
#             line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
#             if not line:
#                 return None
#             return json.loads(line)
#     except socket.timeout:
#         log.error("motion_rpc timeout %s:%s", host, port)
#     except ConnectionRefusedError:
#         log.error("motion_rpc refused %s:%s — is motion_server running?", host, port)
#     except Exception as e:
#         log.error("motion_rpc error: %s", e)
#     return None
"""JSON-line TCP client for carbot ``motion_server`` (port 5000).


Uses a persistent connection per (host, port) pair so the vision loop does
not open/close a new socket on every RPC call.  The connection is recreated
automatically on any error, so it is safe to restart the server without
restarting the vision process.
"""


from __future__ import annotations


import json
import logging
import socket
import threading
from typing import Any, Dict, Optional


log = logging.getLogger("carbot.vision.motion_client")


# ── Persistent connection registry ────────────────────────────────────────────
# One entry per (host, port) — shared across all callers in the same process.
_conns: Dict[tuple, "_PersistentConn"] = {}
_conns_lock = threading.Lock()




class _PersistentConn:
    """Thread-safe persistent TCP connection to the motion server."""


    def __init__(self, host: str, port: int, timeout: float = 4.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()


    def _connect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        # Disable Nagle — we send short JSON lines, want them flushed immediately.
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s
        log.debug("motion_client: connected to %s:%s", self.host, self.port)


    def call(
        self,
        payload: Dict[str, Any],
        *,
        request_timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send one JSON-line RPC. ``request_timeout`` overrides the default for this call only."""
        with self._lock:
            for attempt in range(2):
                try:
                    if self._sock is None:
                        self._connect()
                    t = self.timeout if request_timeout is None else float(request_timeout)
                    if self._sock is not None:
                        self._sock.settimeout(t)
                    msg = json.dumps(payload) + "\n"
                    self._sock.sendall(msg.encode("utf-8"))
                    buf = b""
                    while b"\n" not in buf:
                        chunk = self._sock.recv(8192)
                        if not chunk:
                            raise ConnectionResetError("server closed connection")
                        buf += chunk
                    line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
                    if not line:
                        return None
                    return json.loads(line)
                except (ConnectionResetError, BrokenPipeError, OSError) as e:
                    log.warning("motion_client: connection lost (%s) — reconnecting", e)
                    self._sock = None
                    if attempt == 1:
                        log.error("motion_client: reconnect failed for %s:%s", self.host, self.port)
                        return None
                except socket.timeout:
                    log.error("motion_rpc timeout %s:%s", self.host, self.port)
                    # Don't reconnect on timeout — server may just be busy.
                    return None
                except Exception as e:
                    log.error("motion_rpc error: %s", e)
                    self._sock = None
                    return None
        return None


    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None




def _get_conn(host: str, port: int, timeout: float) -> _PersistentConn:
    key = (host, port)
    with _conns_lock:
        if key not in _conns:
            _conns[key] = _PersistentConn(host, port, timeout)
        return _conns[key]




def motion_rpc(
    host: str,
    port: int,
    payload: Dict[str, Any],
    timeout: float = 4.0,
) -> Optional[Dict[str, Any]]:
    """Send one command over a persistent connection; return parsed JSON or None."""
    return _get_conn(host, port, timeout).call(payload, request_timeout=timeout)




def close_motion_rpc_connection(host: str, port: int) -> None:
    """Close the persistent socket for ``(host, port)`` (call when a vision run ends)."""
    key = (host, port)
    with _conns_lock:
        conn = _conns.pop(key, None)
    if conn is not None:
        conn.close()









