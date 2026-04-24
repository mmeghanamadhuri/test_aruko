from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class NinaSettings:
    serial_port: str
    baudrate: int
    neutral_action_name: str
    actions_dir: Path
    manifest_path: Path
    recordings_dir: Path
    recording_sample_hz: float


def load_settings(repo_root: Path) -> NinaSettings:
    actions_dir = repo_root / "nina" / "actions"
    recordings_dir = repo_root / "nina" / "actions" / "recordings"
    manifest_path = actions_dir / "manifest.json"

    recordings_dir.mkdir(parents=True, exist_ok=True)

    return NinaSettings(
        serial_port=os.environ.get("NINA_DXL_PORT", "/dev/ttyUSB0"),
        baudrate=int(os.environ.get("NINA_DXL_BAUD", "222222")),
        neutral_action_name=os.environ.get("NINA_NEUTRAL_ACTION", "neutral"),
        actions_dir=actions_dir,
        manifest_path=manifest_path,
        recordings_dir=recordings_dir,
        recording_sample_hz=float(os.environ.get("NINA_RECORD_HZ", "20")),
    )
