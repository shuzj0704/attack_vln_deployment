import unittest
from unittest import mock

import requests

from http_framework.robot.action_executor import (
    ActionExecutionError,
    ActionRunnerExecutor,
    parse_args,
)
from http_framework.robot.client import (
    HTTPProtocolError,
    NavigationLoop,
    VLNHTTPClient,
)
from http_framework.robot.pd_executor import (
    OdometryState,
    PoseTarget,
    StreamVLNPDController,
    StreamVLNPDExecutor,
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

    def test_streamvln_pd_is_default_and_action_runner_is_available(self):
        default_args = parse_args(["--action", "stop", "--execute"])
        fallback_args = parse_args(
            ["--action", "stop", "--execute", "--executor", "action-runner"]
        )
        self.assertEqual("streamvln-pd", default_args.executor)
        self.assertEqual("action-runner", fallback_args.executor)


class FakeSportMotionIO:
    def __init__(self, states):
        self.states = list(states)
        self.moves = []
        self.closed = False

    def read_odometry(self, _timeout_s):
        if not self.states:
            raise RuntimeError("no fake odometry")
        return self.states.pop(0)

    def move(self, vx, vy, vyaw):
        self.moves.append((vx, vy, vyaw))

    def close(self):
        self.closed = True


class FakeStopExecutor:
    def __init__(self):
        self.stops = 0
        self.closed = False

    def execute(self, _action):
        raise AssertionError("PD executor should only use the fallback StopMove")

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed = True


class StreamVLNPDExecutorTest(unittest.TestCase):
    def test_pd_law_matches_streamvln_parameters(self):
        controller = StreamVLNPDController()
        vx, vyaw, position_error, yaw_error = controller.solve(
            OdometryState(0.0, 0.0, 0.0, 0.2, 0.1),
            PoseTarget(0.25, 0.0, 0.2),
        )
        self.assertAlmostEqual(0.65, vx)
        self.assertAlmostEqual(0.55, vyaw)
        self.assertAlmostEqual(0.25, position_error)
        self.assertAlmostEqual(0.2, yaw_error)

    @mock.patch("http_framework.robot.pd_executor.time.sleep", return_value=None)
    def test_forward_uses_odometry_feedback_then_stopmove(self, _sleep):
        motion = FakeSportMotionIO(
            [
                OdometryState(0.0, 0.0, 0.0, 0.0, 0.0),
                OdometryState(0.05, 0.0, 0.0, 0.1, 0.0),
                OdometryState(0.16, 0.0, 0.0, 0.1, 0.0),
            ]
        )
        stop_executor = FakeStopExecutor()
        executor = StreamVLNPDExecutor(motion, stop_executor)

        executor.execute("forward")

        self.assertGreater(motion.moves[0][0], 0.0)
        self.assertEqual((0.0, 0.0, 0.0), motion.moves[-1])
        self.assertEqual(1, stop_executor.stops)

    @mock.patch("http_framework.robot.pd_executor.time.sleep", return_value=None)
    def test_left_uses_positive_yaw_feedback(self, _sleep):
        motion = FakeSportMotionIO(
            [
                OdometryState(0.0, 0.0, 0.0, 0.0, 0.0),
                OdometryState(0.0, 0.0, 0.10, 0.0, 0.0),
                OdometryState(0.0, 0.0, 0.18, 0.0, 0.0),
            ]
        )
        executor = StreamVLNPDExecutor(motion, FakeStopExecutor())

        executor.execute("left")

        self.assertGreater(motion.moves[0][2], 0.0)
        self.assertEqual((0.0, 0.0, 0.0), motion.moves[-1])

    @mock.patch("http_framework.robot.pd_executor.time.sleep", return_value=None)
    def test_right_uses_negative_yaw_feedback(self, _sleep):
        motion = FakeSportMotionIO(
            [
                OdometryState(0.0, 0.0, 0.0, 0.0, 0.0),
                OdometryState(0.0, 0.0, -0.10, 0.0, 0.0),
                OdometryState(0.0, 0.0, -0.18, 0.0, 0.0),
            ]
        )
        executor = StreamVLNPDExecutor(motion, FakeStopExecutor())

        executor.execute("right")

        self.assertLess(motion.moves[0][2], 0.0)
        self.assertEqual((0.0, 0.0, 0.0), motion.moves[-1])

    def test_odometry_failure_triggers_stopmove(self):
        motion = FakeSportMotionIO([])
        stop_executor = FakeStopExecutor()
        executor = StreamVLNPDExecutor(motion, stop_executor)

        with self.assertRaisesRegex(ActionExecutionError, "no fake odometry"):
            executor.execute("forward")

        self.assertEqual([(0.0, 0.0, 0.0)], motion.moves)
        self.assertEqual(1, stop_executor.stops)


if __name__ == "__main__":
    unittest.main()
