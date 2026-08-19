"""Adapter around the official StreamVLN evaluator without modifying it."""

from __future__ import annotations

import argparse
import io
import os
import sys
from typing import Iterable


class StreamVLNBackend:
    """Run one fresh-image inference per primitive action."""

    def __init__(
        self,
        streamvln_root: str,
        model_path: str,
        device: str = "cuda:0",
        num_future_steps: int = 4,
        num_frames: int = 32,
        num_history: int = 8,
        model_max_length: int = 4096,
    ):
        streamvln_root = os.path.abspath(streamvln_root)
        model_path = os.path.abspath(model_path)
        if not os.path.isdir(streamvln_root):
            raise ValueError(f"StreamVLN root does not exist: {streamvln_root}")
        if not os.path.exists(model_path):
            raise ValueError(f"model path does not exist: {model_path}")

        # The upstream evaluator contains imports relative to both repository root
        # and streamvln/. Keep this compatibility local to the deployment adapter.
        for path in (streamvln_root, os.path.join(streamvln_root, "streamvln")):
            if path not in sys.path:
                sys.path.insert(0, path)

        import numpy as np
        import torch
        import transformers
        from streamvln.model.stream_video_vln import StreamVLNForCausalLM
        from streamvln.streamvln_agent import VLNEvaluator

        self._np = np
        self._torch = torch
        self._pil_image = self._load_pillow()
        self._instruction = ""

        evaluator_args = argparse.Namespace(
            model_path=model_path,
            num_future_steps=num_future_steps,
            num_frames=num_frames,
            num_history=num_history,
            model_max_length=model_max_length,
            device=device,
        )
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            model_path,
            model_max_length=model_max_length,
            padding_side="right",
        )
        config = transformers.AutoConfig.from_pretrained(model_path)
        model = StreamVLNForCausalLM.from_pretrained(
            model_path,
            attn_implementation="flash_attention_2",
            torch_dtype=torch.bfloat16,
            config=config,
            low_cpu_mem_usage=False,
        )
        model.model.num_history = num_history
        model.reset(1)
        model.requires_grad_(False)
        model.to(device)
        model.eval()

        sensor_config = {
            "rgb_height": 1.25,
            "camera_intrinsic": np.array(
                [
                    [192.0, 0.0, 191.42857143, 0.0],
                    [0.0, 192.0, 191.42857143, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ]
            ),
        }
        self._evaluator = VLNEvaluator(
            sensor_config,
            model=model,
            tokenizer=tokenizer,
            args=evaluator_args,
        )
        # Upstream currently defaults this field to cuda:0.
        self._evaluator.device = torch.device(device)

    @staticmethod
    def _load_pillow():
        from PIL import Image

        return Image

    def reset(self, instruction: str) -> None:
        self._instruction = instruction
        self._evaluator.reset_memory()

    def step(self, jpeg_bytes: bytes, instruction: str) -> Iterable[int]:
        if instruction != self._instruction:
            raise RuntimeError("instruction changed without resetting the episode")
        with self._pil_image.open(io.BytesIO(jpeg_bytes)) as image:
            rgb = self._np.asarray(image.convert("RGB"))
        if rgb.shape != (480, 640, 3):
            raise ValueError(f"expected a 640x480 RGB image, got shape {rgb.shape}")

        with self._torch.inference_mode():
            action_sequence, _generate_time, _llm_output = self._evaluator.step(
                0,
                rgb,
                instruction,
                run_model=True,
            )
        # The robot executes exactly one primitive, then supplies a fresh frame.
        self._evaluator.step_id += 1
        return action_sequence

    def close(self) -> None:
        self._evaluator.reset_memory()
        self._instruction = ""
