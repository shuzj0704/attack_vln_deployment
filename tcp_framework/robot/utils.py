#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器狗端工具函数
"""

from datetime import datetime

from http_framework.robot.action_executor import (
    ActionExecutionError,
    ActionRunnerExecutor,
)
from tcp_framework.config import ACTION_RUNNER, VALID_COMMANDS


def log_info(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}][INFO] {msg}")


def log_error(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}][ERROR] {msg}")


def validate_command(cmd):
    """验证控制命令是否合法"""
    return cmd in VALID_COMMANDS


def execute_action(cmd):
    """
    调用 action_runner 执行动作
    返回: (success: bool, output: str)
    """
    if not validate_command(cmd):
        return False, f"Unknown command: {cmd}"

    executor = ActionRunnerExecutor(ACTION_RUNNER)
    try:
        executor.execute(cmd)
        return True, f"{cmd} completed and stopped"
    except ActionExecutionError as exc:
        return False, str(exc)
