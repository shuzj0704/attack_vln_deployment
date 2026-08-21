"""Replaceable local motion executor for Unitree Go2-W."""

from __future__ import annotations

import abc
import argparse
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

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

    def close(self) -> None:
        """Release executor resources; stateless executors need no cleanup."""


EXECUTOR_CHOICES = ("streamvln-pd", "action-runner")


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


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def add_executor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--executor",
        choices=EXECUTOR_CHOICES,
        default=_env("UNITREE_ACTION_EXECUTOR", "streamvln-pd"),
        help="motion executor (default: StreamVLN-style odometry PD)",
    )
    parser.add_argument(
        "--action-runner",
        default=_env("UNITREE_ACTION_RUNNER", "action_runner"),
        help="used for the fallback executor and PD StopMove",
    )
    parser.add_argument(
        "--forward-speed",
        type=float,
        default=float(_env("UNITREE_FORWARD_SPEED", "0.25")),
        help="action-runner fallback speed in m/s",
    )
    parser.add_argument(
        "--turn-speed-deg",
        type=float,
        default=float(_env("UNITREE_TURN_SPEED_DEG", "15")),
        help="action-runner fallback yaw speed in degrees/s",
    )
    parser.add_argument("--pd-kp-translation", type=float, default=3.0)
    parser.add_argument("--pd-kd-translation", type=float, default=0.5)
    parser.add_argument("--pd-kp-yaw", type=float, default=3.0)
    parser.add_argument("--pd-kd-yaw", type=float, default=0.5)
    parser.add_argument("--pd-max-linear-velocity", type=float, default=1.0)
    parser.add_argument("--pd-max-yaw-rate", type=float, default=1.2)
    parser.add_argument("--pd-position-tolerance", type=float, default=0.1)
    parser.add_argument("--pd-yaw-tolerance", type=float, default=0.1)
    parser.add_argument("--pd-control-rate", type=float, default=10.0)
    parser.add_argument("--pd-odometry-timeout", type=float, default=2.0)
    parser.add_argument("--pd-action-timeout", type=float, default=10.0)
    parser.add_argument("--sport-state-topic", default="/lf/sportmodestate")
    parser.add_argument("--sport-request-topic", default="/api/sport/request")


def create_executor(args: argparse.Namespace) -> ActionExecutor:
    stop_executor = ActionRunnerExecutor(
        args.action_runner,
        forward_speed_mps=args.forward_speed,
        turn_speed_radps=math.radians(args.turn_speed_deg),
    )
    if args.executor == "action-runner":
        return stop_executor

    from http_framework.robot.pd_executor import (
        ROS2SportMotionIO,
        StreamVLNPDController,
        StreamVLNPDExecutor,
    )

    motion_io = ROS2SportMotionIO(
        state_topic=args.sport_state_topic,
        request_topic=args.sport_request_topic,
    )
    controller = StreamVLNPDController(
        kp_translation=args.pd_kp_translation,
        kd_translation=args.pd_kd_translation,
        kp_yaw=args.pd_kp_yaw,
        kd_yaw=args.pd_kd_yaw,
        max_linear_velocity=args.pd_max_linear_velocity,
        max_yaw_rate=args.pd_max_yaw_rate,
    )
    return StreamVLNPDExecutor(
        motion_io,
        stop_executor,
        controller=controller,
        position_tolerance_m=args.pd_position_tolerance,
        yaw_tolerance_rad=args.pd_yaw_tolerance,
        control_rate_hz=args.pd_control_rate,
        odometry_timeout_s=args.pd_odometry_timeout,
        action_timeout_s=args.pd_action_timeout,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute one HTTP-framework primitive on the local robot."
    )
    parser.add_argument("--action", choices=VALID_ACTIONS, required=True)
    add_executor_arguments(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="required safety acknowledgement for actual robot execution",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.execute:
        print("refusing to move without --execute", file=sys.stderr)
        return 2

    executor = create_executor(args)
    try:
        executor.execute(args.action)
    except ActionExecutionError as exc:
        print(f"action failed: {exc}", file=sys.stderr)
        return 1
    else:
        print(f"action completed and stopped: {args.action}", flush=True)
        return 0
    finally:
        executor.close()


if __name__ == "__main__":
    raise SystemExit(main())
