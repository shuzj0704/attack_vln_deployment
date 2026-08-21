#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主机端：接收机器狗视频流并显示
"""

import socket
import struct
import time

import cv2
import numpy as np

from tcp_framework.config import ROBOT_IP, VIDEO_PORT


def recv_all(sock, n):
    """从 socket 接收 n 字节数据"""
    data = b""
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def start_video_client(robot_ip=ROBOT_IP, port=VIDEO_PORT):
    """连接机器狗视频流并显示"""
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(10)

    try:
        print(f"[INFO] Connecting to video server {robot_ip}:{port}...")
        client.connect((robot_ip, port))
        print("[INFO] Connected to video server")
    except Exception as e:
        print(f"[ERROR] Failed to connect: {e}")
        return

    frame_count = 0
    start_time = time.time()

    try:
        while True:
            # 读取 4 字节长度
            header = recv_all(client, 4)
            if header is None:
                print("[INFO] Video server disconnected")
                break

            length = struct.unpack('>I', header)[0]

            # 读取 JPEG 数据
            jpeg_data = recv_all(client, length)
            if jpeg_data is None:
                print("[INFO] Video server disconnected")
                break

            # 解码显示
            np_arr = np.frombuffer(jpeg_data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is not None:
                frame_count += 1
                elapsed = time.time() - start_time
                fps = frame_count / elapsed if elapsed > 0 else 0

                cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.imshow("Go2-W D435i", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("[INFO] Quit by user")
                break

    except Exception as e:
        print(f"[ERROR] Video client error: {e}")
    finally:
        client.close()
        cv2.destroyAllWindows()
        print("[INFO] Video client stopped")


if __name__ == "__main__":
    start_video_client()
