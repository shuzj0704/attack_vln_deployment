import io
import unittest
from unittest import mock

from http_framework.protocol import (
    EpisodeService,
    InferenceError,
    ProtocolError,
    first_legal_action,
)
from http_framework.host.server import create_app, load_backend


class FakeBackend:
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [[1]])
        self.reset_calls = []
        self.step_calls = []
        self.close_calls = 0

    def reset(self, instruction):
        self.reset_calls.append(instruction)

    def step(self, jpeg_bytes, instruction):
        self.step_calls.append((jpeg_bytes, instruction))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output

    def close(self):
        self.close_calls += 1


class EpisodeServiceTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend(outputs=[[1, 2], ["invalid", 3], [0]])
        self.service = EpisodeService(self.backend)
        self.reset = self.service.reset("Go to the chair.", "reset-request")
        self.episode_id = self.reset["episode_id"]

    def test_reset_is_idempotent(self):
        repeated = self.service.reset("Go to the chair.", "reset-request")
        self.assertEqual(self.episode_id, repeated["episode_id"])
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(["Go to the chair."], self.backend.reset_calls)

    def test_step_returns_only_first_legal_action(self):
        first = self.service.step(self.episode_id, 0, "step-0", b"jpeg-0")
        second = self.service.step(self.episode_id, 1, "step-1", b"jpeg-1")
        self.assertEqual("forward", first["action"])
        self.assertEqual("right", second["action"])
        self.assertEqual(2, len(self.backend.step_calls))

    def test_backend_may_return_one_action(self):
        self.assertEqual("forward", first_legal_action("forward"))
        self.assertEqual("left", first_legal_action(2))

    def test_duplicate_step_does_not_advance_backend(self):
        first = self.service.step(self.episode_id, 0, "step-0", b"same-jpeg")
        repeated = self.service.step(self.episode_id, 0, "retry-request", b"same-jpeg")
        self.assertEqual(first["action"], repeated["action"])
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(1, len(self.backend.step_calls))
        with self.assertRaises(ProtocolError) as context:
            self.service.step(self.episode_id, 1, "retry-request", b"next-jpeg")
        self.assertEqual("request_conflict", context.exception.code)

    def test_duplicate_stop_step_is_idempotent_after_episode_closes(self):
        backend = FakeBackend(outputs=[[0]])
        service = EpisodeService(backend)
        episode_id = service.reset("Stop at the door.", "reset")["episode_id"]
        service.step(episode_id, 0, "step", b"jpeg")
        repeated = service.step(episode_id, 0, "step", b"jpeg")
        self.assertEqual("stop", repeated["action"])
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(1, len(backend.step_calls))

    def test_changed_image_for_same_step_conflicts(self):
        self.service.step(self.episode_id, 0, "step-0", b"jpeg-a")
        with self.assertRaises(ProtocolError) as context:
            self.service.step(self.episode_id, 0, "step-0", b"jpeg-b")
        self.assertEqual("step_conflict", context.exception.code)
        self.assertEqual(1, len(self.backend.step_calls))

    def test_out_of_order_step_is_rejected(self):
        with self.assertRaises(ProtocolError) as context:
            self.service.step(self.episode_id, 1, "step-1", b"jpeg")
        self.assertEqual("step_out_of_order", context.exception.code)
        self.assertEqual([], self.backend.step_calls)

    def test_backend_failure_closes_episode(self):
        backend = FakeBackend(outputs=[RuntimeError("model failure")])
        service = EpisodeService(backend)
        episode_id = service.reset("Go forward.", "reset")["episode_id"]
        with self.assertRaises(InferenceError):
            service.step(episode_id, 0, "step-0", b"jpeg")
        with self.assertRaises(ProtocolError) as context:
            service.step(episode_id, 0, "step-0", b"jpeg")
        self.assertEqual("episode_failed", context.exception.code)


class FlaskProtocolTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend(outputs=[[2]])
        self.service = EpisodeService(self.backend)
        self.client = create_app(self.service).test_client()

    def test_health_reset_step_and_close(self):
        health = self.client.get("/health")
        self.assertEqual(200, health.status_code)
        self.assertEqual("receding_horizon_single_step", health.get_json()["deployment_mode"])

        reset = self.client.post(
            "/reset",
            json={"instruction": "Turn left.", "request_id": "reset-1"},
        )
        self.assertEqual(200, reset.status_code)
        episode_id = reset.get_json()["episode_id"]

        step = self.client.post(
            "/step",
            data={
                "episode_id": episode_id,
                "step_id": "0",
                "request_id": "step-0",
                "image": (io.BytesIO(b"fake-jpeg"), "rgb.jpg"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(200, step.status_code)
        self.assertEqual("left", step.get_json()["action"])

        close = self.client.post(
            "/close",
            json={"episode_id": episode_id, "request_id": "close-1"},
        )
        self.assertEqual(200, close.status_code)
        self.assertTrue(close.get_json()["closed"])

    def test_bad_request_is_json(self):
        response = self.client.post("/reset", json={"instruction": "missing id"})
        self.assertEqual(400, response.status_code)
        self.assertEqual("bad_request", response.get_json()["error"])


class BackendLoadingTest(unittest.TestCase):
    @mock.patch("http_framework.host.server.importlib.import_module")
    def test_loads_zero_argument_factory(self, import_module):
        backend = FakeBackend()
        module = mock.Mock()
        module.create_backend.return_value = backend
        import_module.return_value = module

        loaded = load_backend("my_vln.deployment:create_backend")

        self.assertIs(backend, loaded)
        import_module.assert_called_once_with("my_vln.deployment")
        module.create_backend.assert_called_once_with()

    def test_rejects_invalid_factory_spec(self):
        with self.assertRaisesRegex(ValueError, "module:callable"):
            load_backend("missing_separator")

    def test_smoke_backend_is_safe_stop_only(self):
        backend = load_backend("http_framework.examples.smoke_backend:create_backend")
        backend.reset("communication smoke test")
        self.assertEqual(
            "stop",
            backend.step(b"non-empty protocol test image", "communication smoke test"),
        )


if __name__ == "__main__":
    unittest.main()
