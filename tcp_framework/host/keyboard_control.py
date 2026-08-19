#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主机端：键盘实时控制机器狗 + 显示视频流
"""

import os
import socket
import struct
import sys
import threading
import time

import cv2
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import ROBOT_IP, VIDEO_PORT, CMD_PORT
from cmd_client import send_command


class VideoReceiver:
    """视频接收线程"""
    def __init__(self, robot_ip=ROBOT_IP, port=VIDEO_PORT):
        self.robot_ip = robot_ip
        self.port = port
        self.frame = None
        self.fps = 0
        self.running = True
        self.connected = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join(timeout=2)

    def recv_all(self, sock, n):
        data = b""
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data += packet
        return data

    def _run(self):
        while self.running:
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(5)
                client.connect((self.robot_ip, self.port))
                self.connected = True
                print(f"[INFO] Video connected to {self.robot_ip}:{self.port}")

                frame_count = 0
                start_time = time.time()

                while self.running:
                    header = self.recv_all(client, 4)
                    if header is None:
                        break

                    length = struct.unpack('>I', header)[0]
                    jpeg_data = self.recv_all(client, length)
                    if jpeg_data is None:
                        break

                    np_arr = np.frombuffer(jpeg_data, np.uint8)
                    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                    if frame is not None:
                        self.frame = frame
                        frame_count += 1
                        elapsed = time.time() - start_time
                        if elapsed > 0:
                            self.fps = frame_count / elapsed

            except Exception as e:
                print(f"[ERROR] Video connection error: {e}")
                self.connected = False
                time.sleep(1)
            finally:
                try:
                    client.close()
                except:
                    pass


def run_keyboard_control():
    """键盘控制主循环"""
    print("[INFO] Starting keyboard control...")
    print("Controls:")
    print("  w: forward (about 25cm)")
    print("  a: left (about 22.5 degrees)")
    print("  d: right (about 22.5 degrees)")
    print("  space: stop")
    print("  q: quit")

    receiver = VideoReceiver()
    receiver.start()

    # 等待视频连接
    wait_start = time.time()
    while not receiver.connected and time.time() - wait_start < 10:
        time.sleep(0.1)

    last_cmd_time = 0
    cmd_cooldown = 1.5  # 命令发送间隔（秒）

    try:
        while True:
            if receiver.frame is not None:
                display_frame = receiver.frame.copy()
                cv2.putText(display_frame, f"FPS: {receiver.fps:.1f}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1,
                            (0, 255, 0), 2)
                cv2.putText(display_frame, "W: fwd, A: left, D: right, SPACE: stop, Q: quit",
                            (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 255, 0), 2)
                cv2.imshow("Go2-W Keyboard Control", display_frame)

            key = cv2.waitKey(50) & 0xFF

            current_time = time.time()

            if key == ord('q'):
                print("[INFO] Quit")
                break
            elif key == ord(' '):
                if current_time - last_cmd_time > cmd_cooldown:
                    send_command("stop")
                    last_cmd_time = current_time
            elif key == ord('w'):
                if current_time - last_cmd_time > cmd_cooldown:
                    send_command("forward")
                    last_cmd_time = current_time
            elif key == ord('a'):
                if current_time - last_cmd_time > cmd_cooldown:
                    send_command("left")
                    last_cmd_time = current_time
            elif key == ord('d'):
                if current_time - last_cmd_time > cmd_cooldown:
                    send_command("right")
                    last_cmd_time = current_time

    finally:
        receiver.stop()
        cv2.destroyAllWindows()
        print("[INFO] Keyboard control stopped")


if __name__ == "__main__":
    run_keyboard_control()
