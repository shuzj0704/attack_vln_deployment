import http.client
import json
import threading
import unittest
from unittest import mock

from http_framework.examples import control_robot, view_d435i_rgb
from http_framework.robot.service import (
    RobotDiagnosticService,
    RobotServiceError,
    create_server,
)


class FakeCamera:
    def __init__(self):
        self.closed = False

    def capture_jpeg(self):
        return b"jpeg"

    def close(self):
        self.closed = True


class FakeExecutor:
    def __init__(self):
        self.actions = []
        self.stopped = False

    def execute(self, action):
        self.actions.append(action)

    def stop(self):
        self.stopped = True


class RobotDiagnosticServiceTest(unittest.TestCase):
    def setUp(self):
        self.camera = FakeCamera()
        self.executor = FakeExecutor()
        self.service = RobotDiagnosticService(self.camera, self.executor)

    def test_rgb_and_action(self):
        self.assertEqual(b"jpeg", self.service.capture_rgb())
        response = self.service.execute_action("left", "request-1")
        self.assertEqual(["left"], self.executor.actions)
        self.assertTrue(response["stopped"])

    def test_duplicate_request_does_not_repeat_action(self):
        self.service.execute_action("forward", "request-1")
        response = self.service.execute_action("forward", "request-1")
        self.assertEqual(["forward"], self.executor.actions)
        self.assertTrue(response["deduplicated"])

    def test_request_id_cannot_change_action(self):
        self.service.execute_action("left", "request-1")
        with self.assertRaises(RobotServiceError) as context:
            self.service.execute_action("right", "request-1")
        self.assertEqual(409, context.exception.status_code)

    def test_http_health_rgb_and_action_endpoints(self):
        server = create_server(self.service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=2,
        )
        try:
            connection.request("GET", "/health")
            health = connection.getresponse()
            self.assertEqual(200, health.status)
            self.assertEqual("ready", json.loads(health.read())["status"])

            connection.request("GET", "/rgb")
            rgb = connection.getresponse()
            self.assertEqual(200, rgb.status)
            self.assertEqual("image/jpeg", rgb.getheader("Content-Type"))
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
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class HTTPControlExampleTest(unittest.TestCase):
    @mock.patch("http_framework.examples.control_robot.requests.post")
    def test_left_uses_http_action_endpoint(self, post):
        response = mock.Mock()
        response.json.return_value = {
            "action": "left",
            "request_id": "request",
            "stopped": True,
        }
        post.return_value = response

        result = control_robot.main(["left"])

        self.assertEqual(0, result)
        self.assertEqual("http://192.168.1.50:5802/action", post.call_args.args[0])
        self.assertEqual("left", post.call_args.kwargs["json"]["action"])

    @mock.patch("http_framework.examples.control_robot.requests.post")
    def test_dry_run_skips_post(self, post):
        result = control_robot.main(["forward", "--dry-run"])

        self.assertEqual(0, result)
        post.assert_not_called()


class HTTPRGBExampleTest(unittest.TestCase):
    @mock.patch("http_framework.examples.view_d435i_rgb.cv2.imdecode")
    def test_fetches_rgb_over_http(self, imdecode):
        expected = object()
        imdecode.return_value = expected
        response = mock.Mock(content=b"jpeg")
        session = mock.Mock()
        session.get.return_value = response

        image = view_d435i_rgb.fetch_rgb_frame(
            session,
            "http://192.168.1.50:5802",
            10.0,
        )

        self.assertIs(expected, image)
        session.get.assert_called_once_with(
            "http://192.168.1.50:5802/rgb",
            timeout=10.0,
        )


if __name__ == "__main__":
    unittest.main()
