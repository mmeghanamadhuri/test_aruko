from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class HealthReport:
    connected: bool
    detected_motors: int
    expected_motors: int
    detail: str


@dataclass
class ActionDefinition:
    name: str
    file_path: str
    metadata: Dict[str, Any]
