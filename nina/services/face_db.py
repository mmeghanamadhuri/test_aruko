"""
JSON-backed face-embedding database for Nina.

Each enrolled face is stored as:

    {
      "name": "hari",
      "embedding": [<128 floats>],     # L2-normalized SFace feature
      "samples":   12,                  # frames averaged into the embedding
      "created_at": 1714200000.0,
      "updated_at": 1714200120.0
    }

Persistence is plain JSON so the file is human-readable and version-control
friendly. Matching uses cosine similarity, which is what SFace was trained
for; OpenCV recommends a threshold of ~0.363 for "same person" -- we keep a
slightly stricter default of 0.48 so we err on the side of "Unknown".

The class deliberately has no dependency on cv2 / numpy: callers pass plain
Python lists / iterables of floats, and we do the dot product by hand. That
keeps the module importable on dev hosts without OpenCV, which is useful
for unit tests and tooling.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


DEFAULT_MATCH_THRESHOLD = 0.48


def _l2_normalize(values: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in values))
    if norm <= 1e-12:
        return [0.0] * len(values)
    inv = 1.0 / norm
    return [float(v) * inv for v in values]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(float(x) * float(y) for x, y in zip(a, b))


class FaceDB:
    """A tiny JSON-backed registry of named face embeddings.

    Thread-safe: every public method takes an internal RLock so the
    Vision worker thread (which writes during enrollment and reads
    during recognition) can't race with the GUI thread.
    """

    def __init__(self, path: Path, *, match_threshold: float = DEFAULT_MATCH_THRESHOLD) -> None:
        self._path = Path(path)
        self._threshold = float(match_threshold)
        self._lock = threading.RLock()
        self._entries: List[dict] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            self._entries = []
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(raw, list):
                return
            cleaned: List[dict] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                emb = item.get("embedding")
                if not name or not isinstance(emb, list) or not emb:
                    continue
                try:
                    embedding = [float(v) for v in emb]
                except (TypeError, ValueError):
                    continue
                cleaned.append(
                    {
                        "name": name,
                        "embedding": embedding,
                        "samples": int(item.get("samples", 1) or 1),
                        "created_at": float(item.get("created_at", time.time())),
                        "updated_at": float(item.get("updated_at", time.time())),
                    }
                )
            self._entries = cleaned

    def _save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")
            tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def upsert(
        self,
        name: str,
        embedding: Iterable[float],
        *,
        samples: int = 1,
    ) -> None:
        """Insert or replace the entry for `name`.

        The embedding is L2-normalized on the way in so cosine matching
        in `find_best_match` is just a dot product.
        """
        name = str(name).strip()
        if not name:
            raise ValueError("Face name cannot be empty")
        normalized = _l2_normalize(list(embedding))
        if not any(normalized):
            raise ValueError("Face embedding is degenerate (all zeros)")
        now = time.time()
        with self._lock:
            existing = self._index_of(name)
            if existing is None:
                self._entries.append(
                    {
                        "name": name,
                        "embedding": normalized,
                        "samples": int(samples),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            else:
                self._entries[existing].update(
                    {
                        "embedding": normalized,
                        "samples": int(samples),
                        "updated_at": now,
                    }
                )
            self._save()

    def remove(self, name: str) -> bool:
        with self._lock:
            idx = self._index_of(name)
            if idx is None:
                return False
            del self._entries[idx]
            self._save()
            return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def names(self) -> List[str]:
        with self._lock:
            return [e["name"] for e in self._entries]

    def is_empty(self) -> bool:
        with self._lock:
            return not self._entries

    def find_best_match(
        self,
        embedding: Iterable[float],
    ) -> Optional[Tuple[str, float]]:
        """Return `(name, score)` of the closest enrolled face, or None.

        `score` is cosine similarity in [-1, 1]; we only return a match
        when score >= the configured threshold so the caller can render
        unmatched faces as "Unknown".
        """
        query = _l2_normalize(list(embedding))
        if not any(query):
            return None
        best_name: Optional[str] = None
        best_score = -1.0
        with self._lock:
            for entry in self._entries:
                score = _cosine(query, entry["embedding"])
                if score > best_score:
                    best_score = score
                    best_name = entry["name"]
        if best_name is None or best_score < self._threshold:
            return None
        return best_name, float(best_score)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _index_of(self, name: str) -> Optional[int]:
        target = name.strip().lower()
        for i, entry in enumerate(self._entries):
            if entry["name"].lower() == target:
                return i
        return None
