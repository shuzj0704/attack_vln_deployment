#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器狗端：D435i 视频流 TCP Server
发送格式: [4字节长度(大端)] + [JPEG数据]
"""

import socket
import struct
import threading
import time

import cv2
import numpy as np

from tcp_framework.config import (
    FPS,
    HEIGHT,
    JPEG_QUALITY,
    ROBOT_HOST,
    VIDEO_PORT,
    WIDTH,
)
from tcp_framework.robot.utils import log_error, log_info


def encode_frame_to_jpeg(frame):
    """将 numpy 图像编码为 JPEG bytes"""
    success, encoded = cv2.imencode('.jpg', frame, [
        cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY
    ])
    if not success:
        return None
    return encoded.tobytes()


def handle_client(conn, addr, pipeline, running):
    """处理单个视频客户端连接"""
    log_info(f"Video client connected: {addr}")
    try:
        while running[0]:
            # 等待一帧
            frames = pipeline.wait_for_frames(timeout_ms=1000)
            color_frame = frames.get_color_frame()
            if not color_frame:
                continue

            color_image = np.asanyarray(color_frame.get_data())
            jpeg_bytes = encode_frame_to_jpeg(color_image)
            if jpeg_bytes is None:
                continue

            # 发送 [4字节长度 + JPEG数据]
            length = len(jpeg_bytes)
            header = struct.pack('>I', length)
            conn.sendall(header + jpeg_bytes)

            # 控制发送频率
            time.sleep(1.0 / FPS)
    except BrokenPipeError:
        log_info(f"Video client disconnected: {addr}")
    except Exception as e:
        log_error(f"Video client error {addr}: {e}")
    finally:
        conn.close()


def start_video_server(host=ROBOT_HOST, port=VIDEO_PORT):
    """启动 D435i 视频流 TCP Server"""
    try:
        import pyrealsense2 as rs
    except ImportError as e:
        log_error(f"Missing pyrealsense2: {e}")
        return

    # 配置 RealSense
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.bgr8, FPS)

    log_info("Starting RealSense pipeline...")
    try:
        pipeline.start(config)
    except RuntimeError as e:
        log_error(f"Failed to start RealSense: {e}")
        return

    # 启动 TCP server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    log_info(f"Video server listening on {host}:{port}")

    running = [True]

    try:
        while running[0]:
            conn, addr = server.accept()
            client_thread = threading.Thread(
                target=handle_client,
                args=(conn, addr, pipeline, running),
                daemon=True
            )
            client_thread.start()
    except KeyboardInterrupt:
        log_info("Video server shutting down...")
    finally:
        running[0] = False
        server.close()
        pipeline.stop()
        log_info("Video server stopped")


if __name__ == "__main__":
    start_video_server()
