#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器狗端工具函数
"""

import os
import subprocess
import sys
from datetime import datetime

# 从父目录导入 config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import ACTION_RUNNER, VALID_COMMANDS


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

    try:
        result = subprocess.run(
            [ACTION_RUNNER, cmd],
            capture_output=True,
            text=True,
            timeout=15
        )
        output = result.stdout.strip()
        if result.returncode == 0:
            return True, output
        else:
            return False, output + "\n" + result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Action timeout"
    except FileNotFoundError:
        return False, f"action_runner not found: {ACTION_RUNNER}"
    except Exception as e:
        return False, str(e)
