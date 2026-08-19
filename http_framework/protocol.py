"""Thread-safe protocol state for receding-horizon StreamVLN deployment."""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Protocol, Union


VALID_ACTIONS = ("forward", "left", "right", "stop")
ACTION_IDS = {0: "stop", 1: "forward", 2: "left", 3: "right"}
ActionValue = Union[str, int]


class ProtocolError(Exception):
    """A client-visible protocol error."""

    def __init__(self, message: str, status_code: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class InferenceError(RuntimeError):
    """The backend failed after an inference request started."""


class InferenceBackend(Protocol):
    """Minimal interface implemented by the GPU-side StreamVLN adapter."""

    def reset(self, instruction: str) -> None:
        ...

    def step(self, jpeg_bytes: bytes, instruction: str) -> Iterable[ActionValue]:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class StepRecord:
    request_id: str
    image_sha256: str
    action: str


def _require_nonempty_string(value: Any, name: str, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ProtocolError(f"{name} is too long")
    return value


def first_legal_action(actions: Iterable[ActionValue]) -> str:
    """Return the first legal primitive from a generated action sequence."""
    if actions is None:
        return "stop"
    for raw_action in actions:
        if isinstance(raw_action, bool):
            continue
        if isinstance(raw_action, int) and raw_action in ACTION_IDS:
            return ACTION_IDS[raw_action]
        if isinstance(raw_action, str):
            action = raw_action.strip().lower()
            if action in VALID_ACTIONS:
                return action
    # An empty or wholly invalid model output fails safe.
    return "stop"


class EpisodeService:
    """Serialize model access and enforce ordering/idempotency for one model env."""

    def __init__(self, backend: InferenceBackend):
        self._backend = backend
        self._lock = threading.RLock()
        self._episode_id: Optional[str] = None
        self._instruction: Optional[str] = None
        self._next_step_id = 0
        self._closed = True
        self._failed = False
        self._steps: Dict[int, StepRecord] = {}
        self._request_steps: Dict[str, int] = {}
        self._reset_request_id: Optional[str] = None
        self._reset_instruction: Optional[str] = None
        self._close_request_id: Optional[str] = None

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": "ready",
                "deployment_mode": "receding_horizon_single_step",
                "active_episode_id": self._episode_id if not self._closed else None,
            }

    def reset(self, instruction: Any, request_id: Any) -> Dict[str, Any]:
        instruction = _require_nonempty_string(instruction, "instruction", max_length=16384)
        request_id = _require_nonempty_string(request_id, "request_id", max_length=256)

        with self._lock:
            if request_id == self._reset_request_id:
                if instruction != self._reset_instruction:
                    raise ProtocolError(
                        "request_id was already used with a different instruction",
                        409,
                        "request_conflict",
                    )
                return {
                    "episode_id": self._episode_id,
                    "request_id": request_id,
                    "next_step_id": self._next_step_id,
                    "deduplicated": True,
                }

            try:
                self._backend.reset(instruction)
            except Exception as exc:
                self._failed = True
                self._closed = True
                raise InferenceError("StreamVLN reset failed; no episode was started") from exc
            self._episode_id = str(uuid.uuid4())
            self._instruction = instruction
            self._next_step_id = 0
            self._closed = False
            self._failed = False
            self._steps = {}
            self._request_steps = {}
            self._reset_request_id = request_id
            self._reset_instruction = instruction
            self._close_request_id = None
            return {
                "episode_id": self._episode_id,
                "request_id": request_id,
                "next_step_id": 0,
                "deduplicated": False,
            }

    def step(
        self,
        episode_id: Any,
        step_id: Any,
        request_id: Any,
        jpeg_bytes: bytes,
    ) -> Dict[str, Any]:
        episode_id = _require_nonempty_string(episode_id, "episode_id", max_length=256)
        request_id = _require_nonempty_string(request_id, "request_id", max_length=256)
        if isinstance(step_id, bool):
            raise ProtocolError("step_id must be a non-negative integer")
        try:
            step_id = int(step_id)
        except (TypeError, ValueError):
            raise ProtocolError("step_id must be a non-negative integer") from None
        if step_id < 0:
            raise ProtocolError("step_id must be a non-negative integer")
        if not isinstance(jpeg_bytes, bytes) or not jpeg_bytes:
            raise ProtocolError("image must be a non-empty JPEG file")

        image_sha256 = hashlib.sha256(jpeg_bytes).hexdigest()
        with self._lock:
            if episode_id != self._episode_id:
                raise ProtocolError("unknown episode_id", 404, "episode_not_found")

            existing = self._steps.get(step_id)
            if existing is not None:
                if existing.image_sha256 != image_sha256:
                    raise ProtocolError(
                        "step_id was already used with a different image",
                        409,
                        "step_conflict",
                    )
                prior_step = self._request_steps.get(request_id)
                if prior_step is not None and prior_step != step_id:
                    raise ProtocolError(
                        "request_id was already used for a different step",
                        409,
                        "request_conflict",
                    )
                self._request_steps[request_id] = step_id
                return self._step_response(step_id, request_id, existing.action, True)

            self._require_active_episode(episode_id)

            prior_step = self._request_steps.get(request_id)
            if prior_step is not None:
                raise ProtocolError(
                    "request_id was already used for a different step",
                    409,
                    "request_conflict",
                )
            if step_id != self._next_step_id:
                raise ProtocolError(
                    f"out-of-order step_id: expected {self._next_step_id}, got {step_id}",
                    409,
                    "step_out_of_order",
                )

            try:
                generated_actions = self._backend.step(jpeg_bytes, self._instruction or "")
                action = first_legal_action(generated_actions)
            except Exception as exc:
                # Backend state may already have changed; never continue this episode.
                self._failed = True
                self._closed = True
                raise InferenceError("StreamVLN inference failed; episode was closed") from exc

            self._steps[step_id] = StepRecord(request_id, image_sha256, action)
            self._request_steps[request_id] = step_id
            self._next_step_id += 1
            if action == "stop":
                self._closed = True
            return self._step_response(step_id, request_id, action, False)

    def close(self, episode_id: Any, request_id: Any) -> Dict[str, Any]:
        episode_id = _require_nonempty_string(episode_id, "episode_id", max_length=256)
        request_id = _require_nonempty_string(request_id, "request_id", max_length=256)
        with self._lock:
            if episode_id != self._episode_id:
                raise ProtocolError("unknown episode_id", 404, "episode_not_found")
            if request_id == self._close_request_id:
                return {
                    "episode_id": episode_id,
                    "request_id": request_id,
                    "closed": True,
                    "deduplicated": True,
                }
            was_closed = self._closed
            self._closed = True
            if not was_closed:
                self._backend.close()
            self._close_request_id = request_id
            return {
                "episode_id": episode_id,
                "request_id": request_id,
                "closed": True,
                "deduplicated": was_closed,
            }

    def _require_active_episode(self, episode_id: str) -> None:
        if episode_id != self._episode_id:
            raise ProtocolError("unknown episode_id", 404, "episode_not_found")
        if self._failed:
            raise ProtocolError("episode failed and must be reset", 409, "episode_failed")
        if self._closed:
            raise ProtocolError("episode is closed", 409, "episode_closed")

    def _step_response(
        self, step_id: int, request_id: str, action: str, deduplicated: bool
    ) -> Dict[str, Any]:
        if action not in VALID_ACTIONS:
            raise RuntimeError(f"internal invalid action: {action}")
        return {
            "episode_id": self._episode_id,
            "step_id": step_id,
            "request_id": request_id,
            "action": action,
            "deduplicated": deduplicated,
        }
