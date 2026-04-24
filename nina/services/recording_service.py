import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class RecordingSession:
    name: str
    started_at: float
    samples: List[Dict] = field(default_factory=list)


class RecordingService:
    def __init__(self, recordings_dir: Path) -> None:
        self.recordings_dir = recordings_dir
        self.current_session: Optional[RecordingSession] = None

    def start(self, name: str) -> None:
        if self.current_session is not None:
            raise RuntimeError("A recording session is already in progress.")
        self.current_session = RecordingSession(name=name, started_at=time.time())

    def add_sample(self, sample: Dict) -> None:
        if self.current_session is None:
            raise RuntimeError("No active recording session.")
        self.current_session.samples.append(sample)

    def stop(self) -> Path:
        if self.current_session is None:
            raise RuntimeError("No active recording session.")

        session = self.current_session
        self.current_session = None

        payload = {
            "robot": "nina",
            "recording_name": session.name,
            "created_at": session.started_at,
            "frame_count": len(session.samples),
            "frames": session.samples,
        }
        out_path = self.recordings_dir / f"{session.name}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out_path
