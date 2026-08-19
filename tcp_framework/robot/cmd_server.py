#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器狗端：控制命令 TCP Server
接收格式: "command\n"
返回格式: "OK: ...\n" 或 "ERROR: ...\n"
"""

import os
import socket
import sys
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import ROBOT_HOST, CMD_PORT
from utils import log_info, log_error, execute_action


def handle_client(conn, addr, running):
    """处理单个控制命令客户端"""
    log_info(f"Cmd client connected: {addr}")
    conn.settimeout(30)

    buffer = ""
    try:
        while running[0]:
            data = conn.recv(1024)
            if not data:
                break

            buffer += data.decode('utf-8', errors='ignore')

            # 按换行分割处理命令
            while '\n' in buffer:
                cmd, buffer = buffer.split('\n', 1)
                cmd = cmd.strip()
                if not cmd:
                    continue

                log_info(f"Received command: {cmd}")
                success, output = execute_action(cmd)

                if success:
                    response = f"OK: {output}\n"
                    log_info(f"Command '{cmd}' executed successfully")
                else:
                    response = f"ERROR: {output}\n"
                    log_error(f"Command '{cmd}' failed: {output}")

                conn.sendall(response.encode('utf-8'))
    except ConnectionResetError:
        log_info(f"Cmd client disconnected: {addr}")
    except Exception as e:
        log_error(f"Cmd client error {addr}: {e}")
    finally:
        conn.close()


def start_cmd_server(host=ROBOT_HOST, port=CMD_PORT):
    """启动控制命令 TCP Server"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    log_info(f"Cmd server listening on {host}:{port}")

    running = [True]

    try:
        while running[0]:
            conn, addr = server.accept()
            client_thread = threading.Thread(
                target=handle_client,
                args=(conn, addr, running),
                daemon=True
            )
            client_thread.start()
    except KeyboardInterrupt:
        log_info("Cmd server shutting down...")
    finally:
        running[0] = False
        server.close()
        log_info("Cmd server stopped")


if __name__ == "__main__":
    start_cmd_server()
