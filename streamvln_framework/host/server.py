#!/usr/bin/env python3
"""GPU server compatible with StreamVLN's original ``POST /eval_vln`` API."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from typing import Any, Optional, Protocol, Sequence

import numpy as np
from PIL import Image

from streamvln_framework.robot.pd_controller import normalize_actions


DEFAULT_INSTRUCTION = "Walk forward and immediately stop when you exit the room."


class StreamVLNBackend(Protocol):
    def reset(self) -> None: ...

    def step(self, image_bgr: np.ndarray, instruction: str) -> Sequence[int]: ...


class EvaluatorBackend:
    """Adapter preserving the upstream evaluator's four-step action-sequence logic."""

    def __init__(self, evaluator: Any, future_steps: int = 4):
        if future_steps <= 0:
            raise ValueError("future_steps must be positive")
        self._evaluator = evaluator
        self._future_steps = future_steps
        self._action_sequence: Sequence[int] = (0,)
        self._terminated = False

    def reset(self) -> None:
        self._evaluator.reset_memory()
        self._action_sequence = (0,)
        self._terminated = False

    def step(self, image_bgr: np.ndarray, instruction: str) -> Sequence[int]:
        if self._terminated:
            return (0,)
        for _ in range(self._future_steps):
            returned_actions, _generate_time, _llm_output = self._evaluator.step(
                0,
                image_bgr,
                instruction,
                run_model=(self._evaluator.step_id % self._future_steps == 0),
            )
            if returned_actions is not None:
                self._action_sequence = normalize_actions(returned_actions)
            self._evaluator.step_id += 1
        actions = normalize_actions(self._action_sequence)
        if 0 in actions:
            self._terminated = True
        return actions


def create_app(
    backend: StreamVLNBackend,
    instruction: str = DEFAULT_INSTRUCTION,
    max_image_bytes: int = 5 * 1024 * 1024,
):
    try:
        from flask import Flask, jsonify, request
    except ImportError as exc:
        raise RuntimeError("Flask is required on the StreamVLN GPU server") from exc

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_image_bytes + 64 * 1024

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ready",
                "protocol": "streamvln_eval_vln",
                "instruction": instruction,
            }
        )

    @app.post("/eval_vln")
    def eval_vln():
        image_file = request.files.get("image")
        if image_file is None:
            return jsonify({"error": "multipart field 'image' is required"}), 400
        try:
            metadata = json.loads(request.form.get("json", "{}"))
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"invalid json form field: {exc}"}), 400
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("reset"), bool
        ):
            return jsonify({"error": "json.reset must be a boolean"}), 400
        try:
            image = Image.open(image_file.stream).convert("RGB")
            image_bgr = np.asarray(image)[..., ::-1]
            if metadata["reset"]:
                backend.reset()
            actions = normalize_actions(backend.step(image_bgr, instruction))
        except Exception as exc:
            app.logger.exception("StreamVLN evaluation failed")
            return jsonify({"error": str(exc)}), 500
        return jsonify({"action": list(actions)})

    return app


def load_evaluator_backend(args: argparse.Namespace) -> EvaluatorBackend:
    streamvln_root = os.path.abspath(args.streamvln_root)
    if not os.path.isdir(streamvln_root):
        raise FileNotFoundError(f"StreamVLN root does not exist: {streamvln_root}")
    if streamvln_root not in sys.path:
        sys.path.insert(0, streamvln_root)

    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    agent_module = importlib.import_module("streamvln.streamvln_agent")
    model_module = importlib.import_module("streamvln.model.stream_video_vln")

    tokenizer = transformers.AutoTokenizer.from_pretrained(
        args.model_path,
        model_max_length=args.model_max_length,
        padding_side="right",
    )
    config = transformers.AutoConfig.from_pretrained(args.model_path)
    model = model_module.StreamVLNForCausalLM.from_pretrained(
        args.model_path,
        attn_implementation=args.attn_implementation,
        torch_dtype=torch.bfloat16,
        config=config,
        low_cpu_mem_usage=False,
    )
    model.model.num_history = args.num_history
    model.reset(1)
    model.requires_grad_(False)
    model.to(args.device)
    model.eval()

    sensor_config = {
        "rgb_height": args.rgb_height,
        "camera_intrinsic": np.array(
            [
                [192.0, 0.0, 191.42857143, 0.0],
                [0.0, 192.0, 191.42857143, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        ),
    }
    evaluator = agent_module.VLNEvaluator(
        sensor_config,
        model=model,
        tokenizer=tokenizer,
        args=args,
    )
    evaluator.step(
        0,
        np.zeros((480, 640, 3), dtype=np.uint8),
        "move forward 25 cm",
        run_model=True,
    )
    return EvaluatorBackend(evaluator, future_steps=args.num_future_steps)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streamvln-root", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5801)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--num-future-steps", type=int, default=4)
    parser.add_argument("--num-frames", type=int, default=32)
    parser.add_argument("--num-history", type=int, default=8)
    parser.add_argument("--model-max-length", type=int, default=4096)
    parser.add_argument("--rgb-height", type=float, default=1.25)
    parser.add_argument("--attn-implementation", default="flash_attention_2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.num_future_steps <= 0:
        raise SystemExit("--num-future-steps must be positive")
    backend = load_evaluator_backend(args)
    create_app(backend, instruction=args.instruction).run(
        host=args.host,
        port=args.port,
        threaded=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
