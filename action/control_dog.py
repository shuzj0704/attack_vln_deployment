#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Go2-W 运动控制脚本

使用方法：
1. 修改下面的 COMMAND 变量为你想要的动作
2. 运行：python3 control_dog.py

本脚本会自动判断运行环境：
- 如果在机器狗上运行，直接调用 action_runner
- 如果在本机运行，通过 SSH 连接到机器狗执行
"""

import os
import subprocess
import sys

# ============================================================
# 只需修改这里：选择你要执行的动作
# ============================================================
# 警告：移动类动作（forward/backward/left/right/turn_left/turn_right）
# 会实际控制机器狗运动！请确保周围空旷、地面平坦，并随时准备遥控器急停。
# 初次测试建议使用 "stand_up" 或 "balance" 等非移动动作。
COMMAND = "stand_up"  # 修改这个变量即可

# 可选动作列表：
# - forward     : 前进
# - backward    : 后退
# - left        : 左移
# - right       : 右移
# - turn_left   : 左转
# - turn_right  : 右转
# - stand_up    : 站立锁定
# - stand_down  : 趴下
# - balance     : 平衡站立
# - damp        : 阻尼/急停
# - stop        : 停止运动
# - sit         : 坐下
# - rise_sit    : 从坐下站起
# ============================================================

# 动作参数（可按需调整）
DURATION = 2.0      # 移动类动作持续时间（秒），最大 10 秒
SPEED = 0.3         # 移动速度（m/s 或 rad/s），最大 1.0

# 机器狗配置
# 无线连接使用 192.168.1.200；接网线时使用 192.168.123.18
ROBOT_IP = "192.168.1.200"
ROBOT_USER = "unitree"
ROBOT_PASS = os.environ.get("UNITREE_ROBOT_PASSWORD")
ACTION_RUNNER = "/home/unitree/unitree_sdk2/build/bin/action_runner"


def validate_command(cmd):
    valid_commands = [
        "forward", "backward", "left", "right",
        "turn_left", "turn_right",
        "stand_up", "stand_down", "balance", "damp", "stop",
        "sit", "rise_sit"
    ]
    if cmd not in valid_commands:
        print(f"[ERROR] 未知动作: {cmd}")
        print(f"[INFO] 可选动作: {', '.join(valid_commands)}")
        return False
    return True


def is_running_on_robot():
    """判断是否直接在机器狗上运行"""
    return os.path.exists(ACTION_RUNNER) and os.access(ACTION_RUNNER, os.X_OK)


def run_on_robot():
    """直接在机器狗上运行 action_runner"""
    cmd = [ACTION_RUNNER, COMMAND, str(DURATION), str(SPEED)]
    print(f"[INFO] 在机器狗上直接执行: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=True, text=True)
        print(f"[OK] 动作 {COMMAND} 执行完成")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] 动作执行失败，退出码: {e.returncode}")
        sys.exit(1)


def run_via_ssh():
    """通过 SSH 在机器狗上运行 action_runner"""
    if not ROBOT_PASS:
        print("[ERROR] 请通过 UNITREE_ROBOT_PASSWORD 环境变量提供 SSH 密码")
        sys.exit(1)

    try:
        import paramiko
    except ImportError:
        print("[ERROR] 本机缺少 paramiko，请先安装: pip install paramiko")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(ROBOT_IP, username=ROBOT_USER, password=ROBOT_PASS, timeout=10)

        remote_cmd = f"{ACTION_RUNNER} {COMMAND} {DURATION} {SPEED}"
        print(f"[INFO] 通过 SSH 在机器狗上执行: {remote_cmd}")

        stdin, stdout, stderr = client.exec_command(remote_cmd, timeout=30)

        for line in stdout:
            print(line.strip())

        errors = stderr.read().decode('utf-8', errors='ignore').strip()
        if errors:
            print("[STDERR]")
            print(errors)

        exit_code = stdout.channel.recv_exit_status()
        if exit_code == 0:
            print(f"[OK] 动作 {COMMAND} 执行完成")
        else:
            print(f"[ERROR] 动作执行失败，退出码: {exit_code}")
            sys.exit(1)

    except paramiko.AuthenticationException:
        print("[ERROR] SSH 认证失败，请检查用户名/密码")
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"[ERROR] SSH 连接失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] unexpected error: {e}")
        sys.exit(1)
    finally:
        client.close()


def main():
    if not validate_command(COMMAND):
        sys.exit(1)

    print(f"[INFO] 准备执行动作: {COMMAND}")
    print(f"[INFO] 持续时间: {DURATION}s, 速度: {SPEED}")

    if is_running_on_robot():
        print("[INFO] 检测到在机器狗上运行，直接执行")
        run_on_robot()
    else:
        print("[INFO] 检测到在本机运行，通过 SSH 连接机器狗")
        run_via_ssh()


if __name__ == "__main__":
    main()
