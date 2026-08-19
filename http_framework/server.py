#!/usr/bin/env python3
"""GPU-side HTTP server for single-step StreamVLN deployment."""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any, Dict, Optional

from http_framework.protocol import EpisodeService, InferenceError, ProtocolError


def create_app(service: EpisodeService, max_image_bytes: int = 5 * 1024 * 1024):
    try:
        from flask import Flask, jsonify, request
    except ImportError as exc:
        raise RuntimeError("Flask is required on the GPU server") from exc

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_image_bytes + 64 * 1024

    def error_response(code: str, message: str, status: int):
        return jsonify({"error": code, "message": message}), status

    @app.errorhandler(ProtocolError)
    def handle_protocol_error(exc: ProtocolError):
        return error_response(exc.code, str(exc), exc.status_code)

    @app.errorhandler(InferenceError)
    def handle_inference_error(exc: InferenceError):
        app.logger.exception("Inference failed", exc_info=exc.__cause__)
        return error_response("inference_failed", str(exc), 500)

    @app.errorhandler(413)
    def handle_too_large(_exc):
        return error_response("image_too_large", "request body is too large", 413)

    @app.get("/health")
    def health():
        return jsonify(service.health())

    @app.post("/reset")
    def reset():
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        return jsonify(service.reset(data.get("instruction"), data.get("request_id")))

    @app.post("/step")
    def step():
        image = request.files.get("image")
        if image is None:
            raise ProtocolError("multipart field 'image' is required")
        jpeg_bytes = image.read(max_image_bytes + 1)
        if len(jpeg_bytes) > max_image_bytes:
            raise ProtocolError("image is too large", 413, "image_too_large")
        return jsonify(
            service.step(
                request.form.get("episode_id"),
                request.form.get("step_id"),
                request.form.get("request_id"),
                jpeg_bytes,
            )
        )

    @app.post("/close")
    def close():
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        return jsonify(service.close(data.get("episode_id"), data.get("request_id")))

    return app


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_env("STREAMVLN_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(_env("STREAMVLN_HTTP_PORT", "5801")))
    parser.add_argument("--streamvln-root", default=_env("STREAMVLN_ROOT"), required=_env("STREAMVLN_ROOT") is None)
    parser.add_argument("--model-path", default=_env("STREAMVLN_MODEL_PATH"), required=_env("STREAMVLN_MODEL_PATH") is None)
    parser.add_argument("--device", default=_env("STREAMVLN_DEVICE", "cuda:0"))
    parser.add_argument("--num-future-steps", type=int, default=int(_env("STREAMVLN_NUM_FUTURE_STEPS", "4")))
    parser.add_argument("--num-frames", type=int, default=int(_env("STREAMVLN_NUM_FRAMES", "32")))
    parser.add_argument("--num-history", type=int, default=int(_env("STREAMVLN_NUM_HISTORY", "8")))
    parser.add_argument("--model-max-length", type=int, default=int(_env("STREAMVLN_MODEL_MAX_LENGTH", "4096")))
    parser.add_argument("--max-image-bytes", type=int, default=int(_env("STREAMVLN_MAX_IMAGE_BYTES", str(5 * 1024 * 1024))))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Heavy ML imports and model loading happen only for the executable server.
    from http_framework.streamvln_backend import StreamVLNBackend

    backend = StreamVLNBackend(
        streamvln_root=args.streamvln_root,
        model_path=args.model_path,
        device=args.device,
        num_future_steps=args.num_future_steps,
        num_frames=args.num_frames,
        num_history=args.num_history,
        model_max_length=args.model_max_length,
    )
    app = create_app(EpisodeService(backend), max_image_bytes=args.max_image_bytes)
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
