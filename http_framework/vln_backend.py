"""Integration point for the user's VLN inference implementation.

Keep model imports and checkpoint loading in this module (or another module selected
with ``--backend-factory``) so the robot-side client has no ML dependencies.
"""

from __future__ import annotations

from http_framework.protocol import BackendOutput, InferenceBackend


class VLNBackend:
    """Template for adapting a VLN model to the deployment protocol."""

    def __init__(self) -> None:
        # Load the model/checkpoint and initialize model memory here.
        raise NotImplementedError("initialize your VLN model in VLNBackend.__init__")

    def reset(self, instruction: str) -> None:
        """Start a new episode and reset all model-side temporal state."""
        raise NotImplementedError

    def step(self, jpeg_bytes: bytes, instruction: str) -> BackendOutput:
        """Infer actions from one JPEG observation and the current instruction.

        Return one action (or an action sequence) using names ``forward``,
        ``left``, ``right``, ``stop`` or integer IDs 1, 2, 3, 0. The framework
        executes only the first legal action and requests a fresh image next.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release/reset episode state; model weights may remain loaded."""
        raise NotImplementedError


def create_backend() -> InferenceBackend:
    """Default zero-argument factory used by ``http_framework.server``."""
    return VLNBackend()
