#!/usr/bin/env python3
"""ROS2 Go2 client preserving StreamVLN's cumulative-goal real-world loop."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import threading
import time
from typing import Optional, Sequence

import numpy as np
import requests
from PIL import Image as PILImage

from streamvln_framework.robot.pd_controller import (
    PDController,
    apply_actions_to_goal,
    normalize_actions,
    pose_matrix,
)


SPORT_API_ID_MOVE = 1008
SPORT_API_ID_ECONOMICGAIT = 1063


class StreamVLNProtocolError(RuntimeError):
    """The StreamVLN-compatible GPU server returned an invalid response."""


class StreamVLNHTTPClient:
    def __init__(
        self,
        server_url: str,
        timeout_s: float = 150.0,
        session: Optional[requests.Session] = None,
    ):
        self._server_url = server_url.rstrip("/")
        if not self._server_url.startswith(("http://", "https://")):
            raise ValueError("server_url must start with http:// or https://")
        self._timeout_s = timeout_s
        self._session = session or requests.Session()
        self._reset = True

    def health(self) -> None:
        response = self._session.get(
            f"{self._server_url}/health",
            timeout=self._timeout_s,
        )
        self._raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise StreamVLNProtocolError(f"server is not ready: {payload}")

    def evaluate(self, image_bgr: np.ndarray) -> Sequence[int]:
        # Preserve the upstream client encoding behavior.
        image_bytes = io.BytesIO()
        PILImage.fromarray(image_bgr).save(image_bytes, format="JPEG")
        image_bytes.seek(0)
        response = self._session.post(
            f"{self._server_url}/eval_vln",
            files={"image": ("rgb_image.jpg", image_bytes, "image/jpeg")},
            data={"json": json.dumps({"reset": self._reset})},
            timeout=self._timeout_s,
        )
        self._raise_for_status(response)
        try:
            payload = response.json()
            actions = normalize_actions(payload["action"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StreamVLNProtocolError(
                f"invalid /eval_vln response: {response.text}"
            ) from exc
        self._reset = False
        return actions

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _raise_for_status(response) -> None:
        if not 200 <= response.status_code < 300:
            raise StreamVLNProtocolError(
                f"server returned HTTP {response.status_code}: {response.text}"
            )


class StopMoveRunner:
    """Keep an explicit SDK2 StopMove path around the upstream ROS2 velocity loop."""

    def __init__(self, action_runner: str, timeout_s: float = 10.0):
        self._action_runner = action_runner
        self._timeout_s = timeout_s

    def stop(self) -> None:
        result = subprocess.run(
            [self._action_runner, "stop"],
            capture_output=True,
            text=True,
            timeout=self._timeout_s,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise RuntimeError(
                f"action_runner stop exited with {result.returncode}: {detail}"
            )


def build_robot_node(
    args: argparse.Namespace,
    http_client: StreamVLNHTTPClient,
):
    """Import ROS2 only on the Robot and construct the deployable node."""
    try:
        import rclpy
        from cv_bridge import CvBridge
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image
        from unitree_api.msg import Request, RequestHeader
        from unitree_go.msg import SportModeState
    except ImportError as exc:
        raise RuntimeError(
            "Robot requires rclpy, cv_bridge, sensor_msgs, unitree_api and unitree_go"
        ) from exc

    class StreamVLNRobotNode(Node):
        def __init__(self):
            super().__init__("streamvln_realworld_client")
            self._bridge = CvBridge()
            self._http_client = http_client
            self._pd = PDController(
                kp_translation=args.kp_translation,
                kd_translation=args.kd_translation,
                kp_yaw=args.kp_yaw,
                kd_yaw=args.kd_yaw,
                max_linear_velocity=args.max_linear_velocity,
                max_yaw_rate=args.max_yaw_rate,
            )
            self._stop_runner = StopMoveRunner(args.action_runner)
            self._state_lock = threading.Lock()
            self._shutdown = threading.Event()
            self._plan_event = threading.Event()
            self._rgb_image: Optional[np.ndarray] = None
            self._odometry: Optional[np.ndarray] = None
            self._goal: Optional[np.ndarray] = None
            self._velocity: Optional[Sequence[float]] = None
            self._odometry_count = 0
            self._planning = False
            self._terminate_after_goal = False
            self._terminated = False
            self._stop_sent = False

            self._rgb_subscription = self.create_subscription(
                Image,
                args.rgb_topic,
                self._rgb_callback,
                qos_profile_sensor_data,
            )
            self._odometry_subscription = self.create_subscription(
                SportModeState,
                args.odometry_topic,
                self._odometry_callback,
                qos_profile_sensor_data,
            )
            self._control_publisher = self.create_publisher(
                Request,
                args.request_topic,
                5,
            )
            self._planning_thread = threading.Thread(
                target=self._planning_loop,
                name="streamvln-planning",
                daemon=True,
            )
            self._control_thread = threading.Thread(
                target=self._control_loop,
                name="streamvln-control",
                daemon=True,
            )
            self._planning_thread.start()
            self._control_thread.start()

        def _rgb_callback(self, message) -> None:
            image = self._bridge.imgmsg_to_cv2(message, "bgr8")
            with self._state_lock:
                self._rgb_image = np.asarray(image).copy()
                should_plan = (
                    self._goal is not None
                    and not self._planning
                    and not self._terminated
                )
                if should_plan:
                    self._planning = True
            if should_plan:
                self._plan_event.set()

        def _odometry_callback(self, message) -> None:
            self._odometry_count += 1
            if self._odometry_count % args.odometry_downsample != 0:
                return
            odometry = pose_matrix(
                float(message.position[0]),
                float(message.position[1]),
                float(message.imu_state.rpy[2]),
            )
            velocity = (float(message.velocity[0]), float(message.yaw_speed))
            with self._state_lock:
                self._odometry = odometry
                self._velocity = velocity
                if self._goal is None:
                    self._goal = odometry.copy()

        def _planning_loop(self) -> None:
            while not self._shutdown.is_set():
                if not self._plan_event.wait(timeout=0.1):
                    continue
                self._plan_event.clear()
                with self._state_lock:
                    image = None if self._rgb_image is None else self._rgb_image.copy()
                    terminated = self._terminated
                if terminated:
                    continue
                if image is None:
                    with self._state_lock:
                        self._planning = False
                    continue
                try:
                    actions = normalize_actions(self._http_client.evaluate(image))
                    with self._state_lock:
                        if self._goal is None:
                            raise RuntimeError("odometry goal is not initialized")
                        self._goal = apply_actions_to_goal(self._goal, actions)
                        self._terminate_after_goal = 0 in actions
                        self._planning = False
                    self.get_logger().info(f"received actions={list(actions)}")
                except Exception as exc:
                    self.get_logger().error(f"planning failed: {exc}")
                    with self._state_lock:
                        self._terminated = True
                        self._planning = False
                    self._safe_stop()

        def _control_loop(self) -> None:
            period_s = 1.0 / args.control_rate
            while not self._shutdown.is_set():
                with self._state_lock:
                    odometry = (
                        None if self._odometry is None else self._odometry.copy()
                    )
                    goal = None if self._goal is None else self._goal.copy()
                    velocity = self._velocity
                    planning = self._planning
                    terminate_after_goal = self._terminate_after_goal
                    terminated = self._terminated
                if terminated:
                    self._publish_move(0.0, 0.0, 0.0)
                    time.sleep(period_s)
                    continue
                if odometry is None or goal is None or velocity is None:
                    time.sleep(period_s)
                    continue

                linear, yaw_rate, position_error, yaw_error = self._pd.solve(
                    odometry,
                    goal,
                    velocity,
                )
                reached = (
                    abs(position_error) < args.position_tolerance
                    and abs(yaw_error) < args.yaw_tolerance
                )
                if reached:
                    self._publish_move(0.0, 0.0, 0.0)
                    if terminate_after_goal:
                        with self._state_lock:
                            self._terminated = True
                        self._safe_stop()
                    elif not planning:
                        with self._state_lock:
                            if not self._planning:
                                self._planning = True
                                self._plan_event.set()
                else:
                    self._publish_move(linear, 0.0, yaw_rate)
                time.sleep(period_s)

        def _publish_move(self, vx: float, vy: float, vyaw: float) -> None:
            parameter = json.dumps({"x": vx, "y": vy, "z": vyaw})
            header = RequestHeader()
            header.identity.api_id = SPORT_API_ID_MOVE
            header.identity.id = time.monotonic_ns()
            request = Request(parameter=parameter, header=header)
            self._control_publisher.publish(request)

        def _safe_stop(self) -> None:
            self._publish_move(0.0, 0.0, 0.0)
            with self._state_lock:
                if self._stop_sent:
                    return
                self._stop_sent = True
            try:
                self._stop_runner.stop()
            except Exception as exc:
                with self._state_lock:
                    self._stop_sent = False
                self.get_logger().error(f"StopMove failed: {exc}")

        def shutdown(self) -> None:
            self._shutdown.set()
            self._plan_event.set()
            self._planning_thread.join(timeout=2.0)
            self._control_thread.join(timeout=2.0)
            self._safe_stop()

    return StreamVLNRobotNode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", required=True)
    parser.add_argument(
        "--action-runner",
        default="/home/unitree/unitree_sdk2/build/bin/action_runner",
    )
    parser.add_argument("--rgb-topic", default="/camera/color/image_raw")
    parser.add_argument("--odometry-topic", default="/lf/sportmodestate")
    parser.add_argument("--request-topic", default="/api/sport/request")
    parser.add_argument("--http-timeout", type=float, default=150.0)
    parser.add_argument("--odometry-downsample", type=int, default=5)
    parser.add_argument("--control-rate", type=float, default=10.0)
    parser.add_argument("--position-tolerance", type=float, default=0.1)
    parser.add_argument("--yaw-tolerance", type=float, default=0.1)
    parser.add_argument("--kp-translation", type=float, default=3.0)
    parser.add_argument("--kd-translation", type=float, default=0.5)
    parser.add_argument("--kp-yaw", type=float, default=3.0)
    parser.add_argument("--kd-yaw", type=float, default=0.5)
    parser.add_argument("--max-linear-velocity", type=float, default=1.0)
    parser.add_argument("--max-yaw-rate", type=float, default=1.2)
    args = parser.parse_args()
    if args.odometry_downsample <= 0 or args.control_rate <= 0:
        parser.error("odometry-downsample and control-rate must be positive")
    return args


def main() -> None:
    args = parse_args()
    client = StreamVLNHTTPClient(args.server_url, timeout_s=args.http_timeout)
    try:
        client.health()
        try:
            import rclpy
        except ImportError as exc:
            raise RuntimeError("rclpy is required on the Robot") from exc

        rclpy.init(args=None)
        node = None
        try:
            node = build_robot_node(args, client)
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            if node is not None:
                node.shutdown()
                node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
    finally:
        client.close()


if __name__ == "__main__":
    main()
