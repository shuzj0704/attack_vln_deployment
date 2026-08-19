import unittest
from unittest import mock

import requests

from http_framework.robot.action_executor import ActionRunnerExecutor
from http_framework.robot.client import (
    HTTPProtocolError,
    NavigationLoop,
    VLNHTTPClient,
)


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
        self.headers = {}
        self.closed = False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class HTTPClientTest(unittest.TestCase):
    def test_step_retry_reuses_request_identifiers_and_image(self):
        expected = {
            "episode_id": "episode",
            "step_id": 4,
            "request_id": "request",
            "action": "right",
        }
        session = FakeSession([requests.Timeout("late"), FakeResponse(200, expected)])
        client = VLNHTTPClient(
            "http://gpu:5801",
            retries=1,
            session=session,
        )
        action = client.step("episode", 4, "request", b"jpeg")
        self.assertEqual("right", action)
        self.assertEqual(2, len(session.posts))
        for _url, kwargs in session.posts:
            self.assertEqual("4", kwargs["data"]["step_id"])
            self.assertEqual("request", kwargs["data"]["request_id"])
            self.assertEqual(b"jpeg", kwargs["files"]["image"][1])

    def test_invalid_action_is_rejected(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "episode_id": "episode",
                        "step_id": 0,
                        "request_id": "request",
                        "action": "backward",
                    },
                )
            ]
        )
        client = VLNHTTPClient("http://gpu:5801", session=session)
        with self.assertRaises(HTTPProtocolError):
            client.step("episode", 0, "request", b"jpeg")


class FakePolicyClient:
    def __init__(self, actions):
        self.actions = list(actions)
        self.closed = []

    def reset(self, instruction, request_id):
        return "episode"

    def step(self, episode_id, step_id, request_id, jpeg_bytes):
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def close_episode(self, episode_id):
        self.closed.append(episode_id)


class FakeCamera:
    def __init__(self):
        self.captures = 0

    def capture_jpeg(self):
        self.captures += 1
        return f"jpeg-{self.captures}".encode()


class FakeExecutor:
    def __init__(self, fail_action=None):
        self.events = []
        self.fail_action = fail_action

    def execute(self, action):
        self.events.append(("execute", action))
        if action == self.fail_action:
            raise RuntimeError("motion failed")

    def stop(self):
        self.events.append(("stop", None))


class NavigationLoopTest(unittest.TestCase):
    @mock.patch("http_framework.robot.client.time.sleep", return_value=None)
    def test_one_capture_per_action_until_stop(self, _sleep):
        policy = FakePolicyClient(["forward", "left", "stop"])
        camera = FakeCamera()
        executor = FakeExecutor()
        episode_id = NavigationLoop(policy, camera, executor).run("Go to the chair.")
        self.assertEqual("episode", episode_id)
        self.assertEqual(3, camera.captures)
        self.assertEqual(
            [("execute", "forward"), ("execute", "left"), ("execute", "stop")],
            [event for event in executor.events if event[0] == "execute"],
        )

    @mock.patch("http_framework.robot.client.time.sleep", return_value=None)
    def test_network_failure_causes_local_stop(self, _sleep):
        policy = FakePolicyClient([HTTPProtocolError("timeout")])
        executor = FakeExecutor()
        with self.assertRaises(HTTPProtocolError):
            NavigationLoop(policy, FakeCamera(), executor).run("Go forward.")
        self.assertEqual(("stop", None), executor.events[-1])

    @mock.patch("http_framework.robot.client.time.sleep", return_value=None)
    def test_action_failure_causes_local_stop(self, _sleep):
        executor = FakeExecutor(fail_action="forward")
        with self.assertRaisesRegex(RuntimeError, "motion failed"):
            NavigationLoop(
                FakePolicyClient(["forward"]), FakeCamera(), executor
            ).run("Go forward.")
        self.assertEqual(("stop", None), executor.events[-1])


class ActionRunnerExecutorTest(unittest.TestCase):
    @mock.patch.object(ActionRunnerExecutor, "_run")
    def test_vln_action_semantics(self, run):
        executor = ActionRunnerExecutor(
            "/opt/unitree/action_runner",
            forward_speed_mps=0.25,
            turn_speed_radps=0.25,
        )
        executor.execute("forward")
        executor.execute("left")
        executor.execute("right")

        movement_commands = [call.args[0] for call in run.call_args_list if call.args[0][1] != "stop"]
        self.assertEqual("forward", movement_commands[0][1])
        self.assertAlmostEqual(1.0, float(movement_commands[0][2]))
        self.assertEqual("turn_left", movement_commands[1][1])
        self.assertAlmostEqual(1.047198, float(movement_commands[1][2]), places=5)
        self.assertEqual("turn_right", movement_commands[2][1])


if __name__ == "__main__":
    unittest.main()
