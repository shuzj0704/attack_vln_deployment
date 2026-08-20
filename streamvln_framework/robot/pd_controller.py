"""PD control and cumulative-goal updates ported from StreamVLN realworld."""

from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

import numpy as np


VALID_ACTION_IDS = (0, 1, 2, 3)


def pose_matrix(x: float, y: float, yaw: float) -> np.ndarray:
    pose = np.eye(4, dtype=np.float64)
    pose[:2, :2] = [
        [math.cos(yaw), -math.sin(yaw)],
        [math.sin(yaw), math.cos(yaw)],
    ]
    pose[0, 3] = x
    pose[1, 3] = y
    return pose


def normalize_actions(actions: Iterable[int]) -> Tuple[int, ...]:
    normalized = tuple(int(action) for action in actions)
    if not normalized:
        return (0,)
    invalid = [action for action in normalized if action not in VALID_ACTION_IDS]
    if invalid:
        raise ValueError(f"invalid StreamVLN action ids: {invalid}")
    return normalized


class PDController:
    """StreamVLN's controller (named PID upstream, but containing no I term)."""

    def __init__(
        self,
        kp_translation: float = 3.0,
        kd_translation: float = 0.5,
        kp_yaw: float = 3.0,
        kd_yaw: float = 0.5,
        max_linear_velocity: float = 1.0,
        max_yaw_rate: float = 1.2,
    ):
        values = (
            kp_translation,
            kd_translation,
            kp_yaw,
            kd_yaw,
            max_linear_velocity,
            max_yaw_rate,
        )
        if any(value < 0 for value in values):
            raise ValueError("PD gains and velocity limits must be non-negative")
        if max_linear_velocity == 0 or max_yaw_rate == 0:
            raise ValueError("PD velocity limits must be positive")
        self.kp_translation = kp_translation
        self.kd_translation = kd_translation
        self.kp_yaw = kp_yaw
        self.kd_yaw = kd_yaw
        self.max_linear_velocity = max_linear_velocity
        self.max_yaw_rate = max_yaw_rate

    def solve(
        self,
        odometry: np.ndarray,
        target: np.ndarray,
        velocity: Sequence[float] = (0.0, 0.0),
    ) -> Tuple[float, float, float, float]:
        translation_error, yaw_error = self.calculate_errors(odometry, target)
        clipped_translation = max(-1.0, min(1.0, translation_error))
        clipped_yaw = max(-1.0, min(1.0, yaw_error))
        linear_velocity = (
            self.kp_translation * clipped_translation
            - self.kd_translation * float(velocity[0])
        )
        yaw_rate = self.kp_yaw * clipped_yaw - self.kd_yaw * float(velocity[1])
        linear_velocity = max(
            -self.max_linear_velocity,
            min(self.max_linear_velocity, linear_velocity),
        )
        yaw_rate = max(-self.max_yaw_rate, min(self.max_yaw_rate, yaw_rate))
        return linear_velocity, yaw_rate, translation_error, yaw_error

    @staticmethod
    def calculate_errors(
        odometry: np.ndarray,
        target: np.ndarray,
    ) -> Tuple[float, float]:
        dx = float(target[0, 3] - odometry[0, 3])
        dy = float(target[1, 3] - odometry[1, 3])
        odometry_yaw = math.atan2(odometry[1, 0], odometry[0, 0])
        target_yaw = math.atan2(target[1, 0], target[0, 0])
        translation_error = dx * math.cos(odometry_yaw) + dy * math.sin(
            odometry_yaw
        )
        yaw_error = (target_yaw - odometry_yaw + math.pi) % (
            2.0 * math.pi
        ) - math.pi
        return translation_error, yaw_error


def apply_actions_to_goal(
    goal: np.ndarray,
    actions: Iterable[int],
    forward_distance_m: float = 0.25,
    turn_angle_rad: float = math.radians(15.0),
) -> np.ndarray:
    """Accumulate an action sequence into the persistent StreamVLN goal pose."""
    updated = np.array(goal, dtype=np.float64, copy=True)
    for action in normalize_actions(actions):
        if action == 0:
            continue
        if action == 1:
            yaw = math.atan2(updated[1, 0], updated[0, 0])
            updated[0, 3] += forward_distance_m * math.cos(yaw)
            updated[1, 3] += forward_distance_m * math.sin(yaw)
            continue
        angle = turn_angle_rad if action == 2 else -turn_angle_rad
        rotation = np.array(
            [
                [math.cos(angle), -math.sin(angle), 0.0],
                [math.sin(angle), math.cos(angle), 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        updated[:3, :3] = rotation @ updated[:3, :3]
    return updated


# Compatibility with the upstream class name.
PID_controller = PDController
