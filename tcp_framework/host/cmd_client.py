#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主机端：发送单次控制命令给机器狗
"""

import os
import socket
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import ROBOT_IP, CMD_PORT, VALID_COMMANDS


def send_command(cmd, robot_ip=ROBOT_IP, port=CMD_PORT, timeout=10):
    """发送控制命令并返回结果"""
    if cmd not in VALID_COMMANDS:
        print(f"[ERROR] Unknown command: {cmd}")
        print(f"[INFO] Valid commands: {', '.join(VALID_COMMANDS)}")
        return False

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(timeout)

    try:
        client.connect((robot_ip, port))
        client.sendall((cmd + "\n").encode('utf-8'))

        response = client.recv(1024).decode('utf-8', errors='ignore').strip()
        print(f"[RESPONSE] {response}")
        return response.startswith("OK")
    except Exception as e:
        print(f"[ERROR] Failed to send command: {e}")
        return False
    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <command>")
        print(f"Valid commands: {', '.join(VALID_COMMANDS)}")
        sys.exit(1)

    cmd = sys.argv[1]
    success = send_command(cmd)
    sys.exit(0 if success else 1)
