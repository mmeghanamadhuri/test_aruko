import json
from pathlib import Path
from typing import Dict

from nina.controllers.dynamixel_manager import DynamixelManager


class ActionRunner:
    def __init__(self, manifest_path: Path, actions_dir: Path, dxl: DynamixelManager) -> None:
        self.manifest_path = manifest_path
        self.actions_dir = actions_dir
        self.dxl = dxl

    def list_actions(self) -> Dict[str, str]:
        manifest = self._load_manifest()
        return manifest.get("actions", {})

    def run_named_action(
        self,
        action_name: str,
        *,
        smooth: bool = True,
        sub_hz: float = 60.0,
        max_speed: int = 1023,
    ) -> Path:
        actions = self.list_actions()
        if action_name not in actions:
            raise ValueError(f"Unknown action '{action_name}'.")
        action_path = self.actions_dir / actions[action_name]
        if not action_path.exists():
            raise FileNotFoundError(f"Action file not found: {action_path}")
        if smooth:
            self.dxl.play_smooth(action_path, sub_hz=sub_hz, max_speed=max_speed)
        else:
            self.dxl.execute_action_file(action_path)
        return action_path

    def _load_manifest(self) -> Dict:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def register_action(self, action_name: str, file_name: str) -> None:
        manifest = self._load_manifest()
        actions = manifest.setdefault("actions", {})
        actions[action_name] = file_name
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
