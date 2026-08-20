#!/usr/bin/env python3
"""Run Robot-local preflight checks before StreamVLN diagnostic examples."""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence


CONFLICT_MARKERS = (
    "streamvln_framework.robot.client",
    "streamvln_framework.robot.service",
    "http_framework.robot.service",
    "tcp_framework.robot.robot_main",
    "tcp_framework/robot/robot_main.py",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    skipped: bool = False


def check_python_dependencies() -> CheckResult:
    modules = (
        "numpy",
        "PIL",
        "requests",
        "rclpy",
        "cv_bridge",
        "sensor_msgs.msg",
        "unitree_api.msg",
        "unitree_go.msg",
    )
    missing = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing.append(f"{module_name} ({exc})")
    if missing:
        return CheckResult(
            "Python/ROS2 dependencies",
            False,
            "missing: " + "; ".join(missing),
        )
    return CheckResult("Python/ROS2 dependencies", True, "all imports succeeded")


def check_conflicting_processes() -> CheckResult:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("Conflicting controllers", False, f"ps failed: {exc}")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"ps exited with {result.returncode}"
        return CheckResult("Conflicting controllers", False, detail)

    conflicts = []
    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        command = fields[1]
        if pid != current_pid and any(marker in command for marker in CONFLICT_MARKERS):
            conflicts.append(f"pid={pid} command={command}")
    if conflicts:
        return CheckResult(
            "Conflicting controllers",
            False,
            "running: " + " | ".join(conflicts),
        )
    return CheckResult("Conflicting controllers", True, "none detected")


def check_action_runner(path: str) -> CheckResult:
    if not os.path.isfile(path):
        return CheckResult("action_runner", False, f"file does not exist: {path}")
    if not os.access(path, os.X_OK):
        return CheckResult("action_runner", False, f"file is not executable: {path}")
    return CheckResult("action_runner", True, path)


def check_ros2_topics(
    rgb_topic: str,
    odometry_topic: str,
    timeout_s: float,
) -> List[CheckResult]:
    try:
        import rclpy
        from cv_bridge import CvBridge
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image
        from unitree_go.msg import SportModeState
    except ImportError as exc:
        detail = f"cannot import ROS2 topic dependencies: {exc}"
        return [
            CheckResult("RGB topic", False, detail),
            CheckResult("Odometry topic", False, detail),
        ]

    received_rgb = False
    received_odometry = False
    bridge = CvBridge()
    owns_rclpy = not rclpy.ok()
    if owns_rclpy:
        rclpy.init(args=None)
    node = rclpy.create_node("streamvln_preflight_check")

    def rgb_callback(message) -> None:
        nonlocal received_rgb
        bridge.imgmsg_to_cv2(message, "bgr8")
        received_rgb = True

    def odometry_callback(message) -> None:
        nonlocal received_odometry
        float(message.position[0])
        float(message.position[1])
        float(message.imu_state.rpy[2])
        float(message.velocity[0])
        float(message.yaw_speed)
        received_odometry = True

    rgb_subscription = node.create_subscription(
        Image, rgb_topic, rgb_callback, qos_profile_sensor_data
    )
    odometry_subscription = node.create_subscription(
        SportModeState,
        odometry_topic,
        odometry_callback,
        qos_profile_sensor_data,
    )
    del rgb_subscription, odometry_subscription
    spin_error: Optional[Exception] = None
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline and not (
            received_rgb and received_odometry
        ):
            rclpy.spin_once(node, timeout_sec=0.1)
    except Exception as exc:
        spin_error = exc
    finally:
        node.destroy_node()
        if owns_rclpy and rclpy.ok():
            rclpy.shutdown()

    if spin_error is not None:
        detail = f"ROS2 spin failed: {spin_error}"
        return [
            CheckResult("RGB topic", False, detail),
            CheckResult("Odometry topic", False, detail),
        ]
    return [
        CheckResult(
            "RGB topic",
            received_rgb,
            f"received: {rgb_topic}"
            if received_rgb
            else f"no valid message within {timeout_s:.1f}s: {rgb_topic}",
        ),
        CheckResult(
            "Odometry topic",
            received_odometry,
            f"received: {odometry_topic}"
            if received_odometry
            else f"no valid message within {timeout_s:.1f}s: {odometry_topic}",
        ),
    ]


def check_stop_move(path: str, timeout_s: float, skip: bool) -> CheckResult:
    if skip:
        return CheckResult(
            "StopMove",
            True,
            "skipped by --skip-stop-check",
            skipped=True,
        )
    try:
        result = subprocess.run(
            [path, "stop"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("StopMove", False, str(exc))
    output = result.stdout.strip() or result.stderr.strip() or "no output"
    if result.returncode != 0:
        return CheckResult(
            "StopMove",
            False,
            f"exit={result.returncode}: {output}",
        )
    return CheckResult("StopMove", True, output)


def run_preflight(args: argparse.Namespace) -> List[CheckResult]:
    results = [
        check_conflicting_processes(),
        check_python_dependencies(),
        check_action_runner(args.action_runner),
    ]
    dependencies_ok = results[1].passed
    if dependencies_ok:
        try:
            results.extend(
                check_ros2_topics(
                    args.rgb_topic,
                    args.odometry_topic,
                    args.topic_timeout,
                )
            )
        except Exception as exc:
            detail = f"topic check crashed: {exc}"
            results.extend(
                [
                    CheckResult("RGB topic", False, detail),
                    CheckResult("Odometry topic", False, detail),
                ]
            )
    else:
        results.extend(
            [
                CheckResult(
                    "RGB topic",
                    False,
                    "not checked because ROS2 imports failed",
                ),
                CheckResult(
                    "Odometry topic",
                    False,
                    "not checked because ROS2 imports failed",
                ),
            ]
        )
    if results[2].passed:
        results.append(
            check_stop_move(
                args.action_runner,
                args.stop_timeout,
                args.skip_stop_check,
            )
        )
    else:
        results.append(
            CheckResult(
                "StopMove",
                False,
                "not checked because action_runner is unavailable",
            )
        )
    return results


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rgb-topic", default="/camera/camera/color/image_raw")
    parser.add_argument("--odometry-topic", default="/lf/sportmodestate")
    parser.add_argument(
        "--action-runner",
        default="/home/unitree/unitree_sdk2/build/bin/action_runner",
    )
    parser.add_argument("--topic-timeout", type=float, default=10.0)
    parser.add_argument("--stop-timeout", type=float, default=10.0)
    parser.add_argument(
        "--skip-stop-check",
        action="store_true",
        help="do not call action_runner stop",
    )
    args = parser.parse_args(argv)
    if args.topic_timeout <= 0 or args.stop_timeout <= 0:
        parser.error("topic-timeout and stop-timeout must be positive")
    return args


def main(
    argv: Optional[Sequence[str]] = None,
    runner: Callable[[argparse.Namespace], List[CheckResult]] = run_preflight,
) -> int:
    args = parse_args(argv)
    print("Manual safety prerequisite: Robot is stable, area is clear, remote E-stop is ready.")
    print("This script never sends forward/left/right; StopMove is the only command.")
    results = runner(args)
    for result in results:
        label = "SKIP" if result.skipped else ("PASS" if result.passed else "FAIL")
        print(f"[{label}] {result.name}: {result.detail}")
    failed = [result for result in results if not result.passed]
    if failed:
        print(f"Preflight FAILED: {len(failed)} required check(s) failed.", file=sys.stderr)
        return 1
    print("Preflight PASSED: Robot diagnostic service may be started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
