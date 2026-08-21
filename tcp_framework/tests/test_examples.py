import unittest
from unittest import mock

from tcp_framework.examples import control_robot, view_d435i_rgb
from tcp_framework.robot import utils


class RobotActionMappingTest(unittest.TestCase):
    @mock.patch("tcp_framework.robot.utils.ActionRunnerExecutor")
    def test_left_uses_shared_http_action_executor(self, executor_type):
        success, output = utils.execute_action("left")

        self.assertTrue(success)
        self.assertEqual("left completed and stopped", output)
        executor_type.return_value.execute.assert_called_once_with("left")


class ControlRobotExampleTest(unittest.TestCase):
    @mock.patch("tcp_framework.examples.control_robot.send_command")
    def test_left_is_sent(self, send_command):
        send_command.return_value = True

        result = control_robot.main(["left"])

        self.assertEqual(0, result)
        send_command.assert_called_once_with("left", "192.168.1.50", 6000)

    @mock.patch("tcp_framework.examples.control_robot.send_command")
    def test_dry_run_skips_send(self, send_command):
        result = control_robot.main(["forward", "--dry-run"])

        self.assertEqual(0, result)
        send_command.assert_not_called()


class ViewRGBExampleTest(unittest.TestCase):
    @mock.patch("tcp_framework.examples.view_d435i_rgb.start_video_client")
    def test_uses_configured_robot_endpoint(self, start_video_client):
        result = view_d435i_rgb.main([])

        self.assertEqual(0, result)
        start_video_client.assert_called_once_with(robot_ip="192.168.1.50", port=5000)


if __name__ == "__main__":
    unittest.main()
