#!/usr/bin/env python3
"""Robot-side HTTP service for RGB inspection and single-action tests."""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from http_framework.protocol import VALID_ACTIONS
from http_framework.robot.action_executor import (
    ActionExecutionError,
    ActionExecutor,
    add_executor_arguments,
    create_executor,
)
from http_framework.robot.camera import RealSenseCamera


class RobotServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class RobotDiagnosticService:
    """Serialize access to the camera and local primitive-action executor."""

    def __init__(
        self,
        camera: RealSenseCamera,
        executor: ActionExecutor,
        action_cache_size: int = 256,
    ):
        self._camera = camera
        self._executor = executor
        self._camera_lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._action_cache_size = action_cache_size
        self._actions: "OrderedDict[str, Tuple[str, Dict[str, Any]]]" = OrderedDict()

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ready",
            "service": "robot_diagnostic_http",
            "motion_executor": type(self._executor).__name__,
            "valid_actions": list(VALID_ACTIONS),
        }

    def capture_rgb(self) -> bytes:
        with self._camera_lock:
            return self._camera.capture_jpeg()

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
                if action != cached_action:
                    raise RobotServiceError(
                        "request_id was already used for a different action",
                        status_code=409,
                    )
                response = dict(cached_response)
                response["deduplicated"] = True
                return response

            try:
                self._executor.execute(action)
            except ActionExecutionError as exc:
                raise RobotServiceError(str(exc), status_code=500) from exc

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
        finally:
            try:
                self._executor.close()
            finally:
                self._camera.close()


def _handler_for(service: RobotDiagnosticService):
    class RobotRequestHandler(BaseHTTPRequestHandler):
        server_version = "RobotDiagnosticHTTP/1.0"

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
                    jpeg_bytes = service.capture_rgb()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg_bytes)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(jpeg_bytes)
                    return
                self._send_error_json(404, "unknown endpoint")
            except Exception as exc:
                logging.exception("Robot HTTP GET failed")
                self._send_error_json(500, str(exc))

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path != "/action":
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
                    payload.get("action"), payload.get("request_id")
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


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_env("ROBOT_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(_env("ROBOT_HTTP_PORT", "5802")))
    add_executor_arguments(parser)
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=480)
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--jpeg-quality", type=int, default=90)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    camera = RealSenseCamera(
        width=args.camera_width,
        height=args.camera_height,
        camera_fps=args.camera_fps,
        jpeg_quality=args.jpeg_quality,
    )
    executor = create_executor(args)
    service = RobotDiagnosticService(camera, executor)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s")
    server = None
    try:
        camera.start()
        server = create_server(service, args.host, args.port)
        print(
            f"Robot diagnostic HTTP service listening on {args.host}:{args.port}",
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
