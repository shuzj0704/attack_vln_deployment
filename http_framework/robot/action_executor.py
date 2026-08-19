"""Replaceable local motion executor for Unitree Go2-W."""

from __future__ import annotations

import abc
import math
import subprocess
from dataclasses import dataclass
from typing import Sequence

from http_framework.protocol import VALID_ACTIONS


class ActionExecutionError(RuntimeError):
    """A primitive action or the following StopMove failed."""


class ActionExecutor(abc.ABC):
    """Interface for a local, blocking primitive-action controller."""

    @abc.abstractmethod
    def execute(self, action: str) -> None:
        """Execute one primitive and return only after the robot has stopped."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Issue a local StopMove command."""


@dataclass(frozen=True)
class PrimitiveMotion:
    command: str
    duration_s: float
    speed: float


class ActionRunnerExecutor(ActionExecutor):
    """Unitree SDK2 adapter using the existing action_runner executable."""

    FORWARD_DISTANCE_M = 0.25
    TURN_ANGLE_RAD = math.radians(15.0)

    def __init__(
        self,
        action_runner: str,
        forward_speed_mps: float = 0.25,
        turn_speed_radps: float = math.radians(15.0),
        timeout_margin_s: float = 10.0,
    ):
        if not action_runner:
            raise ValueError("action_runner must not be empty")
        if forward_speed_mps <= 0 or turn_speed_radps <= 0:
            raise ValueError("motion speeds must be positive")
        self._action_runner = action_runner
        self._forward_speed_mps = forward_speed_mps
        self._turn_speed_radps = turn_speed_radps
        self._timeout_margin_s = timeout_margin_s

    def execute(self, action: str) -> None:
        if action not in VALID_ACTIONS:
            self._best_effort_stop()
            raise ActionExecutionError(f"invalid action: {action}")
        if action == "stop":
            self.stop()
            return

        motion = self._motion_for(action)
        try:
            self._run(
                [
                    self._action_runner,
                    motion.command,
                    f"{motion.duration_s:.6f}",
                    f"{motion.speed:.6f}",
                ],
                timeout=motion.duration_s + self._timeout_margin_s,
            )
        except Exception as exc:
            self._best_effort_stop()
            if isinstance(exc, ActionExecutionError):
                raise
            raise ActionExecutionError(f"failed to execute {action}: {exc}") from exc

        # StopMove is part of every primitive's completion contract.
        self.stop()

    def stop(self) -> None:
        self._run(
            [self._action_runner, "stop"],
            timeout=self._timeout_margin_s,
        )

    def _best_effort_stop(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    def _motion_for(self, action: str) -> PrimitiveMotion:
        if action == "forward":
            return PrimitiveMotion(
                "forward",
                self.FORWARD_DISTANCE_M / self._forward_speed_mps,
                self._forward_speed_mps,
            )
        if action == "left":
            return PrimitiveMotion(
                "turn_left",
                self.TURN_ANGLE_RAD / self._turn_speed_radps,
                self._turn_speed_radps,
            )
        if action == "right":
            return PrimitiveMotion(
                "turn_right",
                self.TURN_ANGLE_RAD / self._turn_speed_radps,
                self._turn_speed_radps,
            )
        raise ActionExecutionError(f"action has no motion primitive: {action}")

    @staticmethod
    def _run(command: Sequence[str], timeout: float) -> None:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActionExecutionError(str(exc)) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise ActionExecutionError(
                f"action_runner exited with {result.returncode}: {detail}"
            )
