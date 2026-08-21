#!/usr/bin/env python3
"""Robot-side HTTP service for one StreamVLN action or one ROS2 RGB frame."""

from __future__ import annotations

import argparse
import io
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
from PIL import Image as PILImage

from streamvln_framework.protocol import ACTION_IDS, VALID_ACTIONS
from streamvln_framework.robot.client import (
    SPORT_API_ID_ECONOMICGAIT,
    SPORT_API_ID_MOVE,
    StopMoveRunner,
)
from streamvln_framework.robot.pd_controller import (
    PDController,
    apply_actions_to_goal,
    pose_matrix,
)
from streamvln_framework.robot.timed_controller import TimedController


class RobotServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OdometrySample:
    pose: np.ndarray
    velocity: Tuple[float, float]
    sequence: int


class ROS2RobotIO:
    """One ROS2 node shared by RGB capture, odometry and Unitree Move requests."""

    def __init__(
        self,
        rgb_topic: str,
        odometry_topic: str,
        request_topic: str,
        jpeg_quality: int = 90,
    ):
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be in [1, 100]")
        try:
            import rclpy
            from cv_bridge import CvBridge
            from rclpy.qos import qos_profile_sensor_data
            from sensor_msgs.msg import Image
            from unitree_api.msg import Request, RequestHeader
            from unitree_go.msg import SportModeState
        except ImportError as exc:
            raise RuntimeError(
                "Robot service requires rclpy, cv_bridge, sensor_msgs, "
                "unitree_api and unitree_go"
            ) from exc

        self._rclpy = rclpy
        self._Request = Request
        self._RequestHeader = RequestHeader
        self._bridge = CvBridge()
        self._jpeg_quality = jpeg_quality
        self._condition = threading.Condition()
        self._latest_rgb: Optional[np.ndarray] = None
        self._latest_odometry: Optional[OdometrySample] = None
        self._odometry_sequence = 0
        self._closed = threading.Event()
        self._spin_error: Optional[BaseException] = None
        self._owns_rclpy = not rclpy.ok()
        if self._owns_rclpy:
            rclpy.init(args=None)

        self._node = rclpy.create_node("streamvln_example_service")
        self._rgb_subscription = self._node.create_subscription(
            Image,
            rgb_topic,
            self._rgb_callback,
            qos_profile_sensor_data,
        )
        self._odometry_subscription = self._node.create_subscription(
            SportModeState,
            odometry_topic,
            self._odometry_callback,
            qos_profile_sensor_data,
        )
        self._publisher = self._node.create_publisher(Request, request_topic, 5)
        self._spin_thread = threading.Thread(
            target=self._spin,
            name="streamvln-example-ros2",
            daemon=True,
        )
        self._spin_thread.start()

    def _spin(self) -> None:
        try:
            while not self._closed.is_set():
                self._rclpy.spin_once(self._node, timeout_sec=0.1)
        except BaseException as exc:
            with self._condition:
                self._spin_error = exc
                self._condition.notify_all()

    def _rgb_callback(self, message) -> None:
        image = self._bridge.imgmsg_to_cv2(message, "bgr8")
        with self._condition:
            self._latest_rgb = np.asarray(image).copy()
            self._condition.notify_all()

    def _odometry_callback(self, message) -> None:
        with self._condition:
            self._odometry_sequence += 1
            self._latest_odometry = OdometrySample(
                pose=pose_matrix(
                    float(message.position[0]),
                    float(message.position[1]),
                    float(message.imu_state.rpy[2]),
                ),
                velocity=(float(message.velocity[0]), float(message.yaw_speed)),
                sequence=self._odometry_sequence,
            )
            self._condition.notify_all()

    def capture_jpeg(self, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while self._latest_rgb is None:
                self._check_state()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RobotServiceError(
                        f"no RGB image received within {timeout_s:.3f}s",
                        status_code=503,
                    )
                self._condition.wait(remaining)
            image_bgr = self._latest_rgb.copy()

        output = io.BytesIO()
        PILImage.fromarray(image_bgr[..., ::-1]).save(
            output,
            format="JPEG",
            quality=self._jpeg_quality,
        )
        return output.getvalue()

    def read_odometry(
        self,
        timeout_s: float,
        after_sequence: Optional[int] = None,
    ) -> OdometrySample:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while self._latest_odometry is None or (
                after_sequence is not None
                and self._latest_odometry.sequence <= after_sequence
            ):
                self._check_state()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RobotServiceError(
                        f"no fresh odometry received within {timeout_s:.3f}s",
                        status_code=503,
                    )
                self._condition.wait(remaining)
            sample = self._latest_odometry
            return OdometrySample(
                pose=sample.pose.copy(),
                velocity=sample.velocity,
                sequence=sample.sequence,
            )

    def move(self, vx: float, vy: float, vyaw: float) -> None:
        parameter = json.dumps({"x": vx, "y": vy, "z": vyaw})
        header = self._RequestHeader()
        header.identity.api_id = SPORT_API_ID_MOVE
        header.identity.id = time.monotonic_ns()
        request = self._Request(parameter=parameter, header=header)
        self._publisher.publish(request)

    def set_economic_gait(self) -> None:
        header = self._RequestHeader()
        header.identity.api_id = SPORT_API_ID_ECONOMICGAIT
        header.identity.id = time.monotonic_ns()
        request = self._Request(parameter="", header=header)
        self._publisher.publish(request)

    def status(self) -> Dict[str, bool]:
        with self._condition:
            return {
                "rgb_received": self._latest_rgb is not None,
                "odometry_received": self._latest_odometry is not None,
            }

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            self._condition.notify_all()
        self._spin_thread.join(timeout=2.0)
        self._node.destroy_node()
        if self._owns_rclpy and self._rclpy.ok():
            self._rclpy.shutdown()

    def _check_state(self) -> None:
        if self._spin_error is not None:
            raise RobotServiceError(
                f"ROS2 spin failed: {self._spin_error}",
                status_code=500,
            )
        if self._closed.is_set():
            raise RobotServiceError("Robot service is closing", status_code=503)


class StreamVLNSingleActionExecutor:
    """Execute one named action with StreamVLN's odometry-feedback PD law."""

    def __init__(
        self,
        robot_io: ROS2RobotIO,
        stop_runner: StopMoveRunner,
        controller: Optional[PDController] = None,
        position_tolerance: float = 0.1,
        yaw_tolerance: float = 0.1,
        control_rate: float = 10.0,
        odometry_timeout: float = 2.0,
        action_timeout: float = 10.0,
    ):
        if position_tolerance <= 0 or yaw_tolerance <= 0:
            raise ValueError("position and yaw tolerances must be positive")
        if control_rate <= 0 or odometry_timeout <= 0 or action_timeout <= 0:
            raise ValueError("rates and timeouts must be positive")
        self._robot_io = robot_io
        self._stop_runner = stop_runner
        self._controller = controller or PDController()
        self._position_tolerance = position_tolerance
        self._yaw_tolerance = yaw_tolerance
        self._period_s = 1.0 / control_rate
        self._odometry_timeout = odometry_timeout
        self._action_timeout = action_timeout
        self._gait_set = False

    def execute(self, action: str) -> None:
        if action not in VALID_ACTIONS:
            raise RobotServiceError(f"invalid action: {action!r}")
        if action == "stop":
            self.stop()
            return

        if not self._gait_set:
            self._robot_io.set_economic_gait()
            self._gait_set = True
            time.sleep(0.5)

        error: Optional[Exception] = None
        try:
            sample = self._robot_io.read_odometry(self._odometry_timeout)
            goal = apply_actions_to_goal(sample.pose, (ACTION_IDS[action],))
            deadline = time.monotonic() + self._action_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RobotServiceError(
                        f"StreamVLN PD action timed out: {action}",
                        status_code=504,
                    )
                sample = self._robot_io.read_odometry(
                    min(self._odometry_timeout, remaining),
                    after_sequence=sample.sequence,
                )
                vx, vyaw, position_error, yaw_error = self._controller.solve(
                    sample.pose,
                    goal,
                    sample.velocity,
                )
                if (
                    abs(position_error) < self._position_tolerance
                    and abs(yaw_error) < self._yaw_tolerance
                ):
                    break
                self._robot_io.move(vx, 0.0, vyaw)
                time.sleep(self._period_s)
        except Exception as exc:
            error = exc
        try:
            self.stop()
        except Exception as stop_exc:
            if error is None:
                error = stop_exc
            else:
                logging.exception("StopMove also failed after action error")
        if error is not None:
            if isinstance(error, RobotServiceError):
                raise error
            raise RobotServiceError(
                f"failed to execute {action}: {error}",
                status_code=500,
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


class RobotDiagnosticService:
    """Serialize single actions while allowing one-shot RGB requests."""

    def __init__(
        self,
        robot_io: ROS2RobotIO,
        executor: StreamVLNSingleActionExecutor,
        rgb_timeout: float = 5.0,
        action_cache_size: int = 256,
    ):
        self._robot_io = robot_io
        self._executor = executor
        self._rgb_timeout = rgb_timeout
        self._action_lock = threading.Lock()
        self._action_cache_size = action_cache_size
        self._actions: "OrderedDict[str, Tuple[str, Dict[str, Any]]]" = OrderedDict()

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ready",
            "service": "streamvln_robot_examples",
            "valid_actions": list(VALID_ACTIONS),
            **self._robot_io.status(),
        }

    def capture_rgb(self) -> bytes:
        return self._robot_io.capture_jpeg(self._rgb_timeout)

    def execute_action(self, action: Any, request_id: Any) -> Dict[str, Any]:
        if action not in VALID_ACTIONS:
            raise RobotServiceError(f"invalid action: {action!r}")
        if not isinstance(request_id, str) or not request_id.strip():
            raise RobotServiceError("request_id must be a non-empty string")
        request_id = request_id.strip()
        if len(request_id) > 256:
            raise RobotServiceError("request_id is too long")

        with self._action_lock:
            cached = self._actions.get(request_id)
            if cached is not None:
                cached_action, cached_response = cached
                if cached_action != action:
                    raise RobotServiceError(
                        "request_id was already used for a different action",
                        status_code=409,
                    )
                response = dict(cached_response)
                response["deduplicated"] = True
                return response

            self._executor.execute(action)
            response = {
                "action": action,
                "request_id": request_id,
                "completed": True,
                "stopped": True,
                "deduplicated": False,
            }
            self._actions[request_id] = (action, response)
            while len(self._actions) > self._action_cache_size:
                self._actions.popitem(last=False)
            return dict(response)

    def close(self) -> None:
        try:
            self._executor.stop()
        except Exception:
            logging.exception("Final StopMove failed")
        finally:
            self._robot_io.close()


def _handler_for(service: RobotDiagnosticService):
    class RobotRequestHandler(BaseHTTPRequestHandler):
        server_version = "StreamVLNRobotExamples/1.0"

        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json(status, {"error": "request_failed", "message": message})

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/health":
                    self._send_json(200, service.health())
                    return
                if path == "/rgb":
                    jpeg = service.capture_rgb()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(jpeg)
                    return
                self._send_error_json(404, "unknown endpoint")
            except RobotServiceError as exc:
                self._send_error_json(exc.status_code, str(exc))
            except Exception as exc:
                logging.exception("Robot HTTP GET failed")
                self._send_error_json(500, str(exc))

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/action":
                self._send_error_json(404, "unknown endpoint")
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 16 * 1024:
                    raise RobotServiceError("invalid request body size")
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise RobotServiceError("JSON body must be an object")
                response = service.execute_action(
                    payload.get("action"),
                    payload.get("request_id"),
                )
                self._send_json(200, response)
            except RobotServiceError as exc:
                self._send_error_json(exc.status_code, str(exc))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._send_error_json(400, f"invalid JSON body: {exc}")
            except Exception as exc:
                logging.exception("Robot HTTP action failed")
                self._send_error_json(500, str(exc))

        def log_message(self, message_format: str, *args: Any) -> None:
            logging.info("%s - %s", self.address_string(), message_format % args)

    return RobotRequestHandler


def create_server(
    service: RobotDiagnosticService,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _handler_for(service))
    server.daemon_threads = True
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5803)
    parser.add_argument("--rgb-topic", default="/camera/color/image_raw")
    parser.add_argument("--odometry-topic", default="/lf/sportmodestate")
    parser.add_argument("--request-topic", default="/api/sport/request")
    parser.add_argument(
        "--action-runner",
        default="/home/unitree/unitree_sdk2/build/bin/action_runner",
    )
    parser.add_argument("--jpeg-quality", type=int, default=90)
    parser.add_argument("--rgb-timeout", type=float, default=5.0)
    parser.add_argument("--odometry-timeout", type=float, default=2.0)
    parser.add_argument("--action-timeout", type=float, default=10.0)
    parser.add_argument("--control-rate", type=float, default=10.0)
    parser.add_argument("--position-tolerance", type=float, default=0.1)
    parser.add_argument("--yaw-tolerance", type=float, default=0.1)
    parser.add_argument("--max-linear-velocity", type=float, default=1.0)
    parser.add_argument("--max-yaw-rate", type=float, default=1.2)
    parser.add_argument(
        "--executor",
        choices=["streamvln", "timed"],
        default="timed",
        help="open-loop timed (timed, default) or closed-loop StreamVLN PD (streamvln)",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be in [1, 65535]")
    if args.rgb_timeout <= 0:
        parser.error("rgb-timeout must be positive")
    return args


def main() -> None:
    args = parse_args()
    robot_io = ROS2RobotIO(
        rgb_topic=args.rgb_topic,
        odometry_topic=args.odometry_topic,
        request_topic=args.request_topic,
        jpeg_quality=args.jpeg_quality,
    )
    stop_runner = StopMoveRunner(args.action_runner)
    if args.executor == "timed":
        executor = TimedController(
            robot_io,
            stop_runner,
            control_rate=args.control_rate,
        )
    else:
        controller = PDController(
            max_linear_velocity=args.max_linear_velocity,
            max_yaw_rate=args.max_yaw_rate,
        )
        executor = StreamVLNSingleActionExecutor(
            robot_io,
            stop_runner,
            controller=controller,
            position_tolerance=args.position_tolerance,
            yaw_tolerance=args.yaw_tolerance,
            control_rate=args.control_rate,
            odometry_timeout=args.odometry_timeout,
            action_timeout=args.action_timeout,
        )
    service = RobotDiagnosticService(
        robot_io,
        executor,
        rgb_timeout=args.rgb_timeout,
    )
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s")
    server = None
    try:
        server = create_server(service, args.host, args.port)
        print(
            f"StreamVLN Robot example service listening on {args.host}:{args.port}",
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        service.close()


if __name__ == "__main__":
    main()
