"""Safe example backend for testing HTTP without loading a VLN model."""

from __future__ import annotations

from http_framework.protocol import InferenceBackend


class SmokeBackend:
    """Return ``stop`` for every observation so communication tests cannot move."""

    def __init__(self) -> None:
        self._instruction = ""

    def reset(self, instruction: str) -> None:
        self._instruction = instruction

    def step(self, jpeg_bytes: bytes, instruction: str) -> str:
        if instruction != self._instruction:
            raise RuntimeError("instruction changed without resetting the episode")
        return "stop"

    def close(self) -> None:
        self._instruction = ""


def create_backend() -> InferenceBackend:
    """Create the test-only backend used by README smoke-test commands."""
    return SmokeBackend()
