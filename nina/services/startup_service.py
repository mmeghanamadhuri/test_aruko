import os
from dataclasses import dataclass

from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager


@dataclass
class StartupResult:
    success: bool
    message: str


def health_mode() -> str:
    """Return the current health-check mode: 'off', 'warn' (default), or 'strict'."""
    return os.environ.get("NINA_HEALTH_CHECK", "warn").strip().lower()


class StartupService:
    def __init__(self, dxl: DynamixelManager, action_runner: ActionRunner, neutral_action_name: str) -> None:
        self.dxl = dxl
        self.action_runner = action_runner
        self.neutral_action_name = neutral_action_name

    def boot(self) -> StartupResult:
        self.dxl.initialize_bus()

        mode = health_mode()
        if mode != "off":
            health = self.dxl.run_health_check()
            if not health.connected:
                msg = (
                    f"Health check: {health.detected_motors}/{health.expected_motors} motors responded. "
                    f"{health.detail}"
                )
                if mode == "strict":
                    return StartupResult(success=False, message=f"Startup aborted (strict mode). {msg}")
                print(f"[warn] {msg} (continuing; set NINA_HEALTH_CHECK=strict to abort)")

        self.dxl.set_torque_all(True)
        self.action_runner.run_named_action(self.neutral_action_name)
        return StartupResult(success=True, message="Startup complete. Neutral pose applied.")
