"""StreamVLN-style odometry-feedback PD primitive executor for Unitree Go2-W."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from http_framework.protocol import VALID_ACTIONS
from http_framework.robot.action_executor import (
    ActionExecutionError,
    ActionExecutor,
)


SPORT_API_ID_MOVE = 1008


@dataclass(frozen=True)
class OdometryState:
    x: float
    y: float
    yaw: float
    linear_velocity: float
    yaw_rate: float


@dataclass(frozen=True)
class PoseTarget:
    x: float
    y: float
    yaw: float


class SportMotionIO(Protocol):
    """Minimal ROS2 transport used by the PD executor."""

    def read_odometry(self, timeout_s: float) -> OdometryState: ...

    def move(self, vx: float, vy: float, vyaw: float) -> None: ...

    def close(self) -> None: ...


def normalize_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class StreamVLNPDController:
    """The P-D law used by StreamVLN's real-world Go2 client."""

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
        self._kp_translation = kp_translation
        self._kd_translation = kd_translation
        self._kp_yaw = kp_yaw
        self._kd_yaw = kd_yaw
        self._max_linear_velocity = max_linear_velocity
        self._max_yaw_rate = max_yaw_rate

    def solve(
        self,
        odometry: OdometryState,
        target: PoseTarget,
    ) -> tuple[float, float, float, float]:
        dx = target.x - odometry.x
        dy = target.y - odometry.y
        translation_error = dx * math.cos(odometry.yaw) + dy * math.sin(
            odometry.yaw
        )
        yaw_error = normalize_angle(target.yaw - odometry.yaw)

        clipped_translation = max(-1.0, min(1.0, translation_error))
        clipped_yaw = max(-1.0, min(1.0, yaw_error))
        linear_velocity = (
            self._kp_translation * clipped_translation
            - self._kd_translation * odometry.linear_velocity
        )
        yaw_rate = (
            self._kp_yaw * clipped_yaw - self._kd_yaw * odometry.yaw_rate
        )
        linear_velocity = max(
            -self._max_linear_velocity,
            min(self._max_linear_velocity, linear_velocity),
        )
        yaw_rate = max(-self._max_yaw_rate, min(self._max_yaw_rate, yaw_rate))
        return linear_velocity, yaw_rate, translation_error, yaw_error


class ROS2SportMotionIO:
    """Lazy ROS2 adapter matching StreamVLN's topics and Unitree Move request."""

    def __init__(
        self,
        state_topic: str = "/lf/sportmodestate",
        request_topic: str = "/api/sport/request",
        node_name: str = "attack_vln_pd_executor",
    ):
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import qos_profile_sensor_data
            from unitree_api.msg import Request, RequestHeader
            from unitree_go.msg import SportModeState
        except ImportError as exc:
            raise ActionExecutionError(
                "streamvln-pd requires ROS2 rclpy, unitree_api and unitree_go messages"
            ) from exc

        self._rclpy = rclpy
        self._Request = Request
        self._RequestHeader = RequestHeader
        self._lock = threading.Lock()
        self._odometry: Optional[OdometryState] = None
        self._received_odometry = threading.Event()
        self._closed = threading.Event()
        self._spin_error: Optional[BaseException] = None
        self._owns_rclpy = not rclpy.ok()
        if self._owns_rclpy:
            rclpy.init(args=None)
        self._node: Node = rclpy.create_node(node_name)
        self._publisher = self._node.create_publisher(Request, request_topic, 5)
        self._subscription = self._node.create_subscription(
            SportModeState,
            state_topic,
            self._odometry_callback,
            qos_profile_sensor_data,
        )
        self._spin_thread = threading.Thread(
            target=self._spin,
            name="streamvln-pd-ros2",
            daemon=True,
        )
        self._spin_thread.start()

    def _spin(self) -> None:
        try:
            while not self._closed.is_set():
                self._rclpy.spin_once(self._node, timeout_sec=0.1)
        except BaseException as exc:
            self._spin_error = exc
            self._received_odometry.set()

    def _odometry_callback(self, message) -> None:
        state = OdometryState(
            x=float(message.position[0]),
            y=float(message.position[1]),
            yaw=float(message.imu_state.rpy[2]),
            linear_velocity=float(message.velocity[0]),
            yaw_rate=float(message.yaw_speed),
        )
        with self._lock:
            self._odometry = state
        self._received_odometry.set()

    def read_odometry(self, timeout_s: float) -> OdometryState:
        if self._spin_error is not None:
            raise ActionExecutionError(
                f"ROS2 odometry spin failed: {self._spin_error}"
            ) from self._spin_error
        self._received_odometry.clear()
        if not self._received_odometry.wait(timeout_s):
            raise ActionExecutionError(
                f"no fresh odometry received within {timeout_s:.3f}s"
            )
        if self._spin_error is not None:
            raise ActionExecutionError(
                f"ROS2 odometry spin failed: {self._spin_error}"
            ) from self._spin_error
        with self._lock:
            if self._odometry is None:
                raise ActionExecutionError("odometry callback returned no state")
            return self._odometry

    def move(self, vx: float, vy: float, vyaw: float) -> None:
        parameter = json.dumps({"x": vx, "y": vy, "z": vyaw})
        header = self._RequestHeader()
        header.identity._api_id = SPORT_API_ID_MOVE
        header.identity.id = time.monotonic_ns()
        request = self._Request(parameter=parameter, header=header)
        self._publisher.publish(request)

    def close(self) -> None:
        self._closed.set()
        self._spin_thread.join(timeout=1.0)
        self._node.destroy_node()
        if self._owns_rclpy and self._rclpy.ok():
            self._rclpy.shutdown()


class StreamVLNPDExecutor(ActionExecutor):
    """Execute 0.25 m / 15 degree primitives using StreamVLN-style PD control."""

    FORWARD_DISTANCE_M = 0.25
    TURN_ANGLE_RAD = math.radians(15.0)

    def __init__(
        self,
        motion_io: SportMotionIO,
        stop_executor: ActionExecutor,
        controller: Optional[StreamVLNPDController] = None,
        position_tolerance_m: float = 0.1,
        yaw_tolerance_rad: float = 0.1,
        control_rate_hz: float = 10.0,
        odometry_timeout_s: float = 2.0,
        action_timeout_s: float = 10.0,
    ):
        if position_tolerance_m <= 0 or yaw_tolerance_rad <= 0:
            raise ValueError("PD position and yaw tolerances must be positive")
        if control_rate_hz <= 0 or odometry_timeout_s <= 0 or action_timeout_s <= 0:
            raise ValueError("PD rates and timeouts must be positive")
        self._motion_io = motion_io
        self._stop_executor = stop_executor
        self._controller = controller or StreamVLNPDController()
        self._position_tolerance_m = position_tolerance_m
        self._yaw_tolerance_rad = yaw_tolerance_rad
        self._period_s = 1.0 / control_rate_hz
        self._odometry_timeout_s = odometry_timeout_s
        self._action_timeout_s = action_timeout_s

    def execute(self, action: str) -> None:
        if action not in VALID_ACTIONS:
            self._best_effort_stop()
            raise ActionExecutionError(f"invalid action: {action}")
        if action == "stop":
            self.stop()
            return

        try:
            initial = self._motion_io.read_odometry(self._odometry_timeout_s)
            target = self._target_for(action, initial)
            deadline = time.monotonic() + self._action_timeout_s
            while True:
                if time.monotonic() >= deadline:
                    raise ActionExecutionError(
                        f"streamvln-pd action timed out: {action}"
                    )
                odometry = self._motion_io.read_odometry(self._odometry_timeout_s)
                vx, vyaw, position_error, yaw_error = self._controller.solve(
                    odometry,
                    target,
                )
                if (
                    abs(position_error) < self._position_tolerance_m
                    and abs(yaw_error) < self._yaw_tolerance_rad
                ):
                    break
                self._motion_io.move(vx, 0.0, vyaw)
                time.sleep(self._period_s)
        except ActionExecutionError:
            self._best_effort_stop()
            raise
        except Exception as exc:
            self._best_effort_stop()
            raise ActionExecutionError(
                f"streamvln-pd failed to execute {action}: {exc}"
            ) from exc
        self.stop()

    def stop(self) -> None:
        try:
            self._motion_io.move(0.0, 0.0, 0.0)
        finally:
            self._stop_executor.stop()

    def close(self) -> None:
        try:
            self._motion_io.close()
        finally:
            self._stop_executor.close()

    def _best_effort_stop(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    def _target_for(self, action: str, initial: OdometryState) -> PoseTarget:
        if action == "forward":
            return PoseTarget(
                x=initial.x + self.FORWARD_DISTANCE_M * math.cos(initial.yaw),
                y=initial.y + self.FORWARD_DISTANCE_M * math.sin(initial.yaw),
                yaw=initial.yaw,
            )
        if action == "left":
            return PoseTarget(
                x=initial.x,
                y=initial.y,
                yaw=normalize_angle(initial.yaw + self.TURN_ANGLE_RAD),
            )
        if action == "right":
            return PoseTarget(
                x=initial.x,
                y=initial.y,
                yaw=normalize_angle(initial.yaw - self.TURN_ANGLE_RAD),
            )
        raise ActionExecutionError(f"action has no PD target: {action}")
