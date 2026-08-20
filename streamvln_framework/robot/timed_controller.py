#!/usr/bin/env python3
"""Open-loop timed action executor as a fallback when odometry is unreliable."""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

from streamvln_framework.protocol import ACTION_IDS, VALID_ACTIONS
from streamvln_framework.robot.client import SPORT_API_ID_ECONOMICGAIT, StopMoveRunner


class RobotServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# Default open-loop profiles.
#   forward: 0.25 m at 0.5 m/s -> 0.5 s
#   left:    +15 deg at 0.5 rad/s -> ~0.524 s
#   right:   -15 deg at 0.5 rad/s -> ~0.524 s
DEFAULT_PROFILES: Dict[str, Tuple[float, float, float]] = {
    "forward": (0.5, 0.0, 0.5),
    "left": (0.0, 0.5, 0.2617993877991494 / 0.5),
    "right": (0.0, -0.5, 0.2617993877991494 / 0.5),
}


class TimedController:
    """Execute one action by sending a constant velocity for a fixed duration.

    This avoids relying on `/lf/sportmodestate` position/velocity updates.
    It still publishes to the same `/api/sport/request` topic and calls
    `StopMove` after each action.
    """

    def __init__(
        self,
        robot_io,
        stop_runner: StopMoveRunner,
        profiles: Optional[Dict[str, Tuple[float, float, float]]] = None,
        control_rate: float = 10.0,
    ):
        self._robot_io = robot_io
        self._stop_runner = stop_runner
        self._profiles = dict(profiles or DEFAULT_PROFILES)
        self._period_s = 1.0 / control_rate
        self._gait_set = False

    def execute(self, action: str) -> None:
        if action not in VALID_ACTIONS:
            raise RobotServiceError(f"invalid action: {action!r}")
        if action == "stop":
            self.stop()
            return

        if action not in self._profiles:
            raise RobotServiceError(f"no timed profile for action: {action!r}")

        vx, vyaw, duration = self._profiles[action]
        if duration <= 0:
            raise RobotServiceError(f"invalid timed profile for {action}: duration <= 0")

        if not self._gait_set:
            self._robot_io.set_economic_gait()
            self._gait_set = True
            time.sleep(0.5)

        error: Optional[Exception] = None
        try:
            logging.info("[timed] %s: vx=%.3f vyaw=%.3f duration=%.3fs",
                         action, vx, vyaw, duration)
            start = time.monotonic()
            deadline = start + duration
            while time.monotonic() < deadline:
                self._robot_io.move(vx, 0.0, vyaw)
                time.sleep(self._period_s)
        except Exception as exc:
            error = exc
        finally:
            try:
                self.stop()
            except Exception as stop_exc:
                if error is None:
                    error = stop_exc
                else:
                    logging.exception("StopMove also failed after timed action")
        if error is not None:
            if isinstance(error, RobotServiceError):
                raise error
            raise RobotServiceError(
                f"failed to execute {action}: {error}", status_code=500
            ) from error

    def stop(self) -> None:
        move_error: Optional[Exception] = None
        try:
            self._robot_io.move(0.0, 0.0, 0.0)
        except Exception as exc:
            move_error = exc
        try:
            self._stop_runner.stop()
        except Exception as exc:
            if move_error is None:
                move_error = exc
        if move_error is not None:
            raise RobotServiceError(f"StopMove failed: {move_error}", status_code=500)
