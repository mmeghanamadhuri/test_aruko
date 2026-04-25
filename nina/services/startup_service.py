from dataclasses import dataclass

from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager


@dataclass
class StartupResult:
    success: bool
    message: str


class StartupService:
    def __init__(self, dxl: DynamixelManager, action_runner: ActionRunner, neutral_action_name: str) -> None:
        self.dxl = dxl
        self.action_runner = action_runner
        self.neutral_action_name = neutral_action_name

    def boot(self) -> StartupResult:
        self.dxl.initialize_bus()

        health = self.dxl.run_health_check()
        if not health.connected:
            print(
                f"[warn] Startup health check: {health.detected_motors}/"
                f"{health.expected_motors} motors responded. {health.detail} "
                "(continuing; missing motors will simply not move)"
            )

        self.dxl.set_torque_all(True)
        self.action_runner.run_named_action(self.neutral_action_name)
        if health.connected:
            return StartupResult(success=True, message="Startup complete. Motors healthy and neutral pose applied.")
        return StartupResult(
            success=True,
            message=(
                f"Startup complete with partial bus ({health.detected_motors}/"
                f"{health.expected_motors} motors). Neutral pose applied to "
                "responsive motors."
            ),
        )
