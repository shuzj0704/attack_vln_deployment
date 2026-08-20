import io
import http.client
import json
import math
import threading
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from streamvln_framework.examples import (
    control_robot,
    fetch_robot_rgb,
    preflight_check,
)
from streamvln_framework.host.server import EvaluatorBackend, create_app
from streamvln_framework.robot.service import (
    OdometrySample,
    RobotDiagnosticService,
    StreamVLNSingleActionExecutor,
    create_server,
)
from streamvln_framework.robot.pd_controller import (
    PDController,
    apply_actions_to_goal,
    pose_matrix,
)


class FakeBackend:
    def __init__(self):
        self.resets = 0
        self.images = []

    def reset(self):
        self.resets += 1

    def step(self, image_bgr, instruction):
        self.images.append((image_bgr, instruction))
        return (1, 2, 0)


class ProtocolTest(unittest.TestCase):
    def test_eval_vln_reset_image_and_action_sequence(self):
        backend = FakeBackend()
        client = create_app(backend, instruction="Leave the room.").test_client()
        image_bytes = io.BytesIO()
        Image.new("RGB", (4, 3), color=(10, 20, 30)).save(
            image_bytes,
            format="JPEG",
        )
        image_bytes.seek(0)

        response = client.post(
            "/eval_vln",
            data={
                "json": json.dumps({"reset": True}),
                "image": (image_bytes, "rgb.jpg"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual([1, 2, 0], response.get_json()["action"])
        self.assertEqual(1, backend.resets)
        self.assertEqual("Leave the room.", backend.images[0][1])
        self.assertEqual((3, 4, 3), backend.images[0][0].shape)

    def test_invalid_reset_metadata_is_rejected(self):
        client = create_app(FakeBackend()).test_client()
        response = client.post(
            "/eval_vln",
            data={"json": "{}", "image": (io.BytesIO(b"x"), "rgb.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(400, response.status_code)


class FakeEvaluator:
    def __init__(self):
        self.step_id = 0
        self.reset_calls = 0
        self.calls = []

    def reset_memory(self):
        self.reset_calls += 1

    def step(self, _episode, _image, _instruction, run_model):
        self.calls.append(run_model)
        return ([1, 2, 3, 0], 0.0, "output")


class EvaluatorBackendTest(unittest.TestCase):
    def test_preserves_four_internal_steps_and_stop_state(self):
        evaluator = FakeEvaluator()
        backend = EvaluatorBackend(evaluator, future_steps=4)
        backend.reset()

        first = backend.step(np.zeros((2, 2, 3)), "instruction")
        second = backend.step(np.zeros((2, 2, 3)), "instruction")

        self.assertEqual((1, 2, 3, 0), first)
        self.assertEqual((0,), second)
        self.assertEqual([True, False, False, False], evaluator.calls)
        self.assertEqual(1, evaluator.reset_calls)


class PDAndGoalTest(unittest.TestCase):
    def test_action_sequence_accumulates_like_streamvln(self):
        goal = apply_actions_to_goal(pose_matrix(0.0, 0.0, 0.0), [1, 2, 1, 3])
        self.assertAlmostEqual(0.25 + 0.25 * math.cos(math.radians(15)), goal[0, 3])
        self.assertAlmostEqual(0.25 * math.sin(math.radians(15)), goal[1, 3])
        self.assertAlmostEqual(0.0, math.atan2(goal[1, 0], goal[0, 0]))

    def test_pd_equation_matches_upstream_defaults(self):
        controller = PDController()
        linear, yaw_rate, position_error, yaw_error = controller.solve(
            pose_matrix(0.0, 0.0, 0.0),
            pose_matrix(0.25, 0.0, 0.2),
            velocity=(0.2, 0.1),
        )
        self.assertAlmostEqual(0.65, linear)
        self.assertAlmostEqual(0.55, yaw_rate)
        self.assertAlmostEqual(0.25, position_error)
        self.assertAlmostEqual(0.2, yaw_error)


class FakeRobotIO:
    def __init__(self, samples=None):
        self.samples = list(samples or [])
        self.moves = []
        self.closed = False

    def status(self):
        return {"rgb_received": True, "odometry_received": True}

    def capture_jpeg(self, _timeout):
        return b"jpeg"

    def read_odometry(self, _timeout, after_sequence=None):
        sample = self.samples.pop(0)
        if after_sequence is not None:
            assert sample.sequence > after_sequence
        return sample

    def move(self, vx, vy, vyaw):
        self.moves.append((vx, vy, vyaw))

    def close(self):
        self.closed = True


class FakeStopRunner:
    def __init__(self):
        self.calls = 0

    def stop(self):
        self.calls += 1


class FakeExecutor:
    def __init__(self):
        self.actions = []
        self.stop_calls = 0

    def execute(self, action):
        self.actions.append(action)

    def stop(self):
        self.stop_calls += 1


class RobotExampleServiceTest(unittest.TestCase):
    @mock.patch("streamvln_framework.robot.service.time.sleep")
    def test_left_action_uses_positive_yaw_pd_then_stops(self, _sleep):
        robot_io = FakeRobotIO(
            [
                OdometrySample(pose_matrix(0.0, 0.0, 0.0), (0.0, 0.0), 1),
                OdometrySample(pose_matrix(0.0, 0.0, 0.0), (0.0, 0.0), 2),
                OdometrySample(
                    pose_matrix(0.0, 0.0, math.radians(15)),
                    (0.0, 0.0),
                    3,
                ),
            ]
        )
        stop_runner = FakeStopRunner()
        executor = StreamVLNSingleActionExecutor(robot_io, stop_runner)

        executor.execute("left")

        self.assertGreater(robot_io.moves[0][2], 0.0)
        self.assertEqual((0.0, 0.0, 0.0), robot_io.moves[-1])
        self.assertEqual(1, stop_runner.calls)

    def test_http_rgb_and_action_endpoints(self):
        robot_io = FakeRobotIO()
        executor = FakeExecutor()
        service = RobotDiagnosticService(robot_io, executor)
        server = create_server(service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=2,
        )
        try:
            connection.request("GET", "/rgb")
            rgb = connection.getresponse()
            self.assertEqual(200, rgb.status)
            self.assertEqual(b"jpeg", rgb.read())

            body = json.dumps({"action": "left", "request_id": "request-1"})
            connection.request(
                "POST",
                "/action",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            action = connection.getresponse()
            self.assertEqual(200, action.status)
            self.assertEqual("left", json.loads(action.read())["action"])
            self.assertEqual(["left"], executor.actions)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class RobotExamplesTest(unittest.TestCase):
    @mock.patch("streamvln_framework.examples.control_robot.requests.post")
    def test_control_robot_sends_action(self, post):
        response = mock.Mock()
        response.json.return_value = {
            "action": "right",
            "request_id": "request",
            "stopped": True,
        }
        post.return_value = response

        result = control_robot.main(["right"])

        self.assertEqual(0, result)
        self.assertEqual("http://192.168.1.50:5803/action", post.call_args.args[0])
        self.assertEqual("right", post.call_args.kwargs["json"]["action"])

    @mock.patch("streamvln_framework.examples.control_robot.requests.post")
    def test_dry_run_skips_post(self, post):
        result = control_robot.main(["forward", "--dry-run"])

        self.assertEqual(0, result)
        post.assert_not_called()

    @mock.patch("streamvln_framework.examples.fetch_robot_rgb.requests.get")
    def test_fetch_robot_rgb_gets_one_valid_jpeg(self, get):
        image_bytes = io.BytesIO()
        Image.new("RGB", (2, 2), color=(10, 20, 30)).save(image_bytes, "JPEG")
        response = mock.Mock(
            content=image_bytes.getvalue(),
            headers={"Content-Type": "image/jpeg"},
        )
        get.return_value = response

        received = fetch_robot_rgb.fetch_rgb(
            "http://192.168.1.50:5803",
            10.0,
        )

        self.assertEqual(image_bytes.getvalue(), received)
        get.assert_called_once_with(
            "http://192.168.1.50:5803/rgb",
            timeout=10.0,
        )


class PreflightCheckTest(unittest.TestCase):
    def test_main_returns_zero_when_all_checks_pass(self):
        def runner(_args):
            return [
                preflight_check.CheckResult("dependencies", True, "ok"),
                preflight_check.CheckResult("topics", True, "ok"),
                preflight_check.CheckResult("StopMove", True, "ok"),
            ]

        self.assertEqual(0, preflight_check.main([], runner=runner))

    def test_main_returns_one_when_a_required_check_fails(self):
        def runner(_args):
            return [
                preflight_check.CheckResult("RGB topic", False, "no message"),
            ]

        self.assertEqual(1, preflight_check.main([], runner=runner))

    @mock.patch("streamvln_framework.examples.preflight_check.subprocess.run")
    def test_conflicting_controller_is_reported(self, run):
        run.return_value = mock.Mock(
            returncode=0,
            stdout="123 python3 -m streamvln_framework.robot.client\n",
            stderr="",
        )

        result = preflight_check.check_conflicting_processes()

        self.assertFalse(result.passed)
        self.assertIn("pid=123", result.detail)

    @mock.patch("streamvln_framework.examples.preflight_check.subprocess.run")
    def test_stop_check_calls_only_stop(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="stopped", stderr="")

        result = preflight_check.check_stop_move("/path/action_runner", 5.0, False)

        self.assertTrue(result.passed)
        run.assert_called_once_with(
            ["/path/action_runner", "stop"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
