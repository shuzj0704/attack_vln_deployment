#!/usr/bin/env python3
"""GPU-side HTTP server for a pluggable single-step VLN backend."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
from typing import Any, Dict, Optional

from http_framework.protocol import (
    EpisodeService,
    InferenceBackend,
    InferenceError,
    ProtocolError,
)


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


def load_backend(factory_spec: str) -> InferenceBackend:
    """Load a zero-argument backend factory from ``module:callable``."""
    try:
        module_name, factory_name = factory_spec.rsplit(":", 1)
    except ValueError as exc:
        raise ValueError("backend factory must use the format module:callable") from exc
    if not module_name or not factory_name:
        raise ValueError("backend factory must use the format module:callable")

    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        raise TypeError(f"backend factory is not callable: {factory_spec}")
    backend = factory()
    for method_name in ("reset", "step", "close"):
        if not callable(getattr(backend, method_name, None)):
            raise TypeError(f"backend is missing callable method: {method_name}")
    return backend


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_env("VLN_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(_env("VLN_HTTP_PORT", "5801")))
    parser.add_argument(
        "--backend-factory",
        default=_env("VLN_BACKEND_FACTORY", "http_framework.vln_backend:create_backend"),
        help="zero-argument backend factory in module:callable format",
    )
    parser.add_argument(
        "--max-image-bytes",
        type=int,
        default=int(_env("VLN_MAX_IMAGE_BYTES", str(5 * 1024 * 1024))),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Model-specific imports and checkpoint loading stay inside the selected factory.
    backend = load_backend(args.backend_factory)
    app = create_app(EpisodeService(backend), max_image_bytes=args.max_image_bytes)
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
