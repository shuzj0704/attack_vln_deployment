#!/usr/bin/env python3
"""Robot-side request/action/capture loop for VLN HTTP deployment."""

from __future__ import annotations

import argparse
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import requests

from http_framework.protocol import VALID_ACTIONS
from http_framework.robot.action_executor import (
    ActionExecutor,
    add_executor_arguments,
    create_executor,
)
from http_framework.robot.camera import RealSenseCamera


class HTTPProtocolError(RuntimeError):
    """The GPU server returned an invalid or unsuccessful response."""


class VLNHTTPClient:
    def __init__(
        self,
        server_url: str,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 150.0,
        retries: int = 1,
        session: Optional[requests.Session] = None,
    ):
        self._base_url = server_url.rstrip("/")
        if not self._base_url.startswith(("http://", "https://")):
            raise ValueError("server_url must start with http:// or https://")
        self._timeout: Tuple[float, float] = (connect_timeout_s, read_timeout_s)
        self._retries = retries
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def health(self) -> Dict[str, Any]:
        response = self._session.get(
            f"{self._base_url}/health",
            timeout=self._timeout,
        )
        data = self._json_response(response)
        if data.get("status") != "ready":
            raise HTTPProtocolError(f"server is not ready: {data}")
        return data

    def reset(self, instruction: str, request_id: str) -> str:
        data = self._post_with_retry(
            "/reset",
            json={"instruction": instruction, "request_id": request_id},
        )
        self._require_equal(data, "request_id", request_id)
        episode_id = data.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id:
            raise HTTPProtocolError("reset response has no valid episode_id")
        if data.get("next_step_id") != 0:
            raise HTTPProtocolError("reset response must start at step_id 0")
        return episode_id

    def step(
        self,
        episode_id: str,
        step_id: int,
        request_id: str,
        jpeg_bytes: bytes,
    ) -> str:
        data = self._post_with_retry(
            "/step",
            data={
                "episode_id": episode_id,
                "step_id": str(step_id),
                "request_id": request_id,
            },
            files={"image": ("rgb.jpg", jpeg_bytes, "image/jpeg")},
        )
        self._require_equal(data, "episode_id", episode_id)
        self._require_equal(data, "step_id", step_id)
        self._require_equal(data, "request_id", request_id)
        action = data.get("action")
        if action not in VALID_ACTIONS:
            raise HTTPProtocolError(f"invalid action in response: {action!r}")
        return action

    def close_episode(self, episode_id: str) -> None:
        request_id = str(uuid.uuid4())
        data = self._post_with_retry(
            "/close",
            json={"episode_id": episode_id, "request_id": request_id},
        )
        self._require_equal(data, "episode_id", episode_id)
        self._require_equal(data, "request_id", request_id)
        if data.get("closed") is not True:
            raise HTTPProtocolError("close response did not confirm closure")

    def close(self) -> None:
        self._session.close()

    def _post_with_retry(self, path: str, **kwargs) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(self._retries + 1):
            try:
                response = self._session.post(
                    f"{self._base_url}{path}",
                    timeout=self._timeout,
                    **kwargs,
                )
                return self._json_response(response)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self._retries:
                    break
        raise HTTPProtocolError(f"HTTP request failed after retries: {last_error}") from last_error

    @staticmethod
    def _json_response(response) -> Dict[str, Any]:
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise HTTPProtocolError(
                f"server returned non-JSON response (HTTP {response.status_code})"
            ) from exc
        if not isinstance(data, dict):
            raise HTTPProtocolError("server JSON response must be an object")
        if not 200 <= response.status_code < 300:
            detail = data.get("message") or data.get("error") or data
            raise HTTPProtocolError(f"server returned HTTP {response.status_code}: {detail}")
        return data

    @staticmethod
    def _require_equal(data: Dict[str, Any], key: str, expected: Any) -> None:
        if data.get(key) != expected:
            raise HTTPProtocolError(
                f"response {key} mismatch: expected {expected!r}, got {data.get(key)!r}"
            )


class NavigationLoop:
    def __init__(
        self,
        client: VLNHTTPClient,
        camera: RealSenseCamera,
        executor: ActionExecutor,
        settle_time_s: float = 0.5,
        max_steps: int = 500,
    ):
        self._client = client
        self._camera = camera
        self._executor = executor
        self._settle_time_s = settle_time_s
        self._max_steps = max_steps

    def run(self, instruction: str) -> str:
        episode_id: Optional[str] = None
        try:
            self._executor.stop()
            episode_id = self._client.reset(instruction, str(uuid.uuid4()))
            for step_id in range(self._max_steps):
                # Capture only after StopMove and a configurable stabilization delay.
                self._executor.stop()
                time.sleep(self._settle_time_s)
                jpeg_bytes = self._camera.capture_jpeg()
                action = self._client.step(
                    episode_id,
                    step_id,
                    str(uuid.uuid4()),
                    jpeg_bytes,
                )
                print(f"step_id={step_id} action={action}", flush=True)
                self._executor.execute(action)
                if action == "stop":
                    return episode_id
            raise RuntimeError(f"maximum step count reached: {self._max_steps}")
        except BaseException:
            self._safe_stop()
            raise
        finally:
            self._safe_stop()
            if episode_id is not None:
                try:
                    self._client.close_episode(episode_id)
                except Exception:
                    # Local StopMove is authoritative; server cleanup is best effort.
                    pass

    def _safe_stop(self) -> None:
        try:
            self._executor.stop()
        except Exception:
            pass


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=_env("VLN_SERVER_URL"), required=_env("VLN_SERVER_URL") is None)
    instruction_group = parser.add_mutually_exclusive_group(required=True)
    instruction_group.add_argument("--instruction")
    instruction_group.add_argument("--instruction-file")
    add_executor_arguments(parser)
    parser.add_argument("--connect-timeout", type=float, default=float(_env("VLN_CONNECT_TIMEOUT", "5")))
    parser.add_argument("--read-timeout", type=float, default=float(_env("VLN_READ_TIMEOUT", "150")))
    parser.add_argument("--retries", type=int, default=int(_env("VLN_HTTP_RETRIES", "1")))
    parser.add_argument("--settle-time", type=float, default=float(_env("VLN_SETTLE_TIME", "0.5")))
    parser.add_argument("--max-steps", type=int, default=int(_env("VLN_MAX_STEPS", "500")))
    parser.add_argument("--jpeg-quality", type=int, default=int(_env("VLN_JPEG_QUALITY", "90")))
    parser.add_argument("--camera-width", type=int, default=int(_env("VLN_CAMERA_WIDTH", "640")))
    parser.add_argument("--camera-height", type=int, default=int(_env("VLN_CAMERA_HEIGHT", "480")))
    parser.add_argument("--camera-fps", type=int, default=int(_env("REALSENSE_CAMERA_FPS", "30")))
    return parser.parse_args()


def _read_instruction(args: argparse.Namespace) -> str:
    if args.instruction is not None:
        instruction = args.instruction
    else:
        with open(args.instruction_file, "r", encoding="utf-8") as handle:
            instruction = handle.read()
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("instruction must not be empty")
    return instruction


def main() -> None:
    args = parse_args()
    instruction = _read_instruction(args)
    client = VLNHTTPClient(
        args.server_url,
        connect_timeout_s=args.connect_timeout,
        read_timeout_s=args.read_timeout,
        retries=args.retries,
    )
    executor = create_executor(args)
    camera = RealSenseCamera(
        width=args.camera_width,
        height=args.camera_height,
        jpeg_quality=args.jpeg_quality,
        camera_fps=args.camera_fps,
    )
    try:
        client.health()
        with camera:
            episode_id = NavigationLoop(
                client,
                camera,
                executor,
                settle_time_s=args.settle_time,
                max_steps=args.max_steps,
            ).run(instruction)
        print(f"episode completed: {episode_id}", flush=True)
    finally:
        try:
            executor.stop()
        finally:
            try:
                executor.close()
            finally:
                client.close()


if __name__ == "__main__":
    main()
