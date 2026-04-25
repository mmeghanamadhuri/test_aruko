import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from nina.controllers.dynamixel_manager import DynamixelManager


# Manifest entry can be either a plain string (the action's JSON file
# relative to actions_dir) or a dict like
#     {"file": "recordings/namaste.json", "audio": "audio/namaste.mp3"}
# Both forms are read/written transparently so existing manifests keep
# working.
ManifestEntry = Union[str, Dict[str, Any]]


class ActionRunner:
    def __init__(self, manifest_path: Path, actions_dir: Path, dxl: DynamixelManager) -> None:
        self.manifest_path = manifest_path
        self.actions_dir = actions_dir
        self.dxl = dxl

    def list_actions(self) -> Dict[str, str]:
        """Return {name: relative_action_file_path}, normalizing dict entries."""
        manifest = self._load_manifest()
        return {
            name: self._extract_file(value)
            for name, value in manifest.get("actions", {}).items()
        }

    def get_action_audio(self, action_name: str) -> Optional[str]:
        """Return the relative audio path for an action, if one is registered."""
        manifest = self._load_manifest()
        raw = manifest.get("actions", {}).get(action_name)
        if isinstance(raw, dict):
            audio = raw.get("audio")
            if isinstance(audio, str) and audio.strip():
                return audio
        return None

    def get_action_audio_offset(self, action_name: str) -> float:
        """
        Seconds to wait after the action starts before firing the audio
        clip. Defaults to 0.0 (audio fires immediately).
        """
        manifest = self._load_manifest()
        raw = manifest.get("actions", {}).get(action_name)
        if isinstance(raw, dict):
            offset = raw.get("audio_offset")
            if isinstance(offset, (int, float)):
                return max(0.0, float(offset))
        return 0.0

    def run_named_action(
        self,
        action_name: str,
        *,
        smooth: bool = True,
        sub_hz: float = 50.0,
        max_speed: int = 1023,
        speed: float = 1.0,
    ) -> Path:
        actions = self.list_actions()
        if action_name not in actions:
            raise ValueError(f"Unknown action '{action_name}'.")
        action_path = self.actions_dir / actions[action_name]
        if not action_path.exists():
            raise FileNotFoundError(f"Action file not found: {action_path}")
        if smooth:
            self.dxl.play_smooth(
                action_path,
                sub_hz=sub_hz,
                max_speed=max_speed,
                speed=speed,
            )
        else:
            self.dxl.execute_action_file(action_path)
        return action_path

    def _load_manifest(self) -> Dict:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def set_action_audio(
        self,
        action_name: str,
        audio_name: Optional[str],
        audio_offset: Optional[float] = None,
    ) -> None:
        """
        Update only the audio fields of an existing manifest entry.

        - The action's `file` is left untouched.
        - `audio_name=None` (or empty) removes the audio mapping (and the
          offset) and collapses the entry back to a plain string.
        - `audio_offset=None` preserves any existing offset; passing 0
          (or a negative number) clears it.
        """
        manifest = self._load_manifest()
        actions = manifest.setdefault("actions", {})
        existing = actions.get(action_name)
        if existing is None:
            raise ValueError(f"Action '{action_name}' is not in the manifest.")

        if isinstance(existing, dict):
            file_name = str(existing.get("file", ""))
            existing_offset = existing.get("audio_offset")
        else:
            file_name = str(existing)
            existing_offset = None

        if not audio_name or not str(audio_name).strip():
            actions[action_name] = file_name
        else:
            entry: Dict[str, Any] = {"file": file_name, "audio": str(audio_name)}
            if audio_offset is not None:
                if audio_offset > 0:
                    entry["audio_offset"] = float(audio_offset)
            elif isinstance(existing_offset, (int, float)) and float(existing_offset) > 0:
                entry["audio_offset"] = float(existing_offset)
            actions[action_name] = entry

        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def register_action(
        self,
        action_name: str,
        file_name: str,
        audio_name: Optional[str] = None,
        audio_offset: Optional[float] = None,
    ) -> None:
        """
        Add or update a manifest entry. Existing audio / audio_offset values
        are preserved unless the caller provides new ones.
        """
        manifest = self._load_manifest()
        actions = manifest.setdefault("actions", {})
        existing = actions.get(action_name)
        existing_audio: Optional[str] = None
        existing_offset: Optional[float] = None
        if isinstance(existing, dict):
            if isinstance(existing.get("audio"), str):
                existing_audio = existing["audio"]
            if isinstance(existing.get("audio_offset"), (int, float)):
                existing_offset = float(existing["audio_offset"])

        audio_to_keep = audio_name if audio_name is not None else existing_audio
        offset_to_keep = audio_offset if audio_offset is not None else existing_offset

        if audio_to_keep:
            entry: Dict[str, Any] = {"file": file_name, "audio": audio_to_keep}
            if offset_to_keep is not None and offset_to_keep > 0:
                entry["audio_offset"] = float(offset_to_keep)
            actions[action_name] = entry
        else:
            actions[action_name] = file_name
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _extract_file(value: ManifestEntry) -> str:
        if isinstance(value, dict):
            return str(value.get("file", ""))
        return str(value)
