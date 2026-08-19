"""On-demand Intel RealSense D435i JPEG capture."""

from __future__ import annotations


class CameraError(RuntimeError):
    """The camera could not provide a valid RGB frame."""


class RealSenseCamera:
    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        camera_fps: int = 30,
        jpeg_quality: int = 90,
        warmup_frames: int = 30,
        frame_timeout_ms: int = 5000,
    ):
        if width <= 0 or height <= 0:
            raise ValueError("camera width and height must be positive")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be in [1, 100]")
        self._width = width
        self._height = height
        self._camera_fps = camera_fps
        self._jpeg_quality = jpeg_quality
        self._warmup_frames = warmup_frames
        self._frame_timeout_ms = frame_timeout_ms
        self._pipeline = None
        self._rs = None

    def start(self) -> None:
        if self._pipeline is not None:
            return
        try:
            import pyrealsense2 as rs
        except ImportError as exc:
            raise CameraError("pyrealsense2 is required on the robot") from exc

        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(
            rs.stream.color,
            self._width,
            self._height,
            rs.format.bgr8,
            self._camera_fps,
        )
        try:
            pipeline.start(config)
            for _ in range(self._warmup_frames):
                pipeline.wait_for_frames(timeout_ms=self._frame_timeout_ms)
        except Exception as exc:
            try:
                pipeline.stop()
            except Exception:
                pass
            raise CameraError(f"failed to start/warm up D435i: {exc}") from exc
        self._pipeline = pipeline
        self._rs = rs

    def capture_jpeg(self) -> bytes:
        if self._pipeline is None:
            raise CameraError("camera is not started")
        try:
            import cv2
            import numpy as np

            frames = self._pipeline.wait_for_frames(timeout_ms=self._frame_timeout_ms)
            color_frame = frames.get_color_frame()
            if not color_frame:
                raise CameraError("no color frame received")
            image = np.asanyarray(color_frame.get_data())
            if image.shape != (self._height, self._width, 3):
                raise CameraError(f"unexpected color frame shape: {image.shape}")
            ok, encoded = cv2.imencode(
                ".jpg",
                image,
                [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality],
            )
            if not ok:
                raise CameraError("OpenCV failed to encode JPEG")
            return encoded.tobytes()
        except CameraError:
            raise
        except Exception as exc:
            raise CameraError(f"failed to capture D435i frame: {exc}") from exc

    def close(self) -> None:
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            finally:
                self._pipeline = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()
