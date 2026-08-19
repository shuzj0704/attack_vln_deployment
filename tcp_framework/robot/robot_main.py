#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器狗端入口：同时启动视频流 server 和控制命令 server
"""

import os
import sys
import threading
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from video_server import start_video_server
from cmd_server import start_cmd_server
from utils import log_info


if __name__ == "__main__":
    log_info("Starting Go2-W TCP framework server...")

    # 启动视频 server
    video_thread = threading.Thread(target=start_video_server, daemon=True)
    video_thread.start()

    # 稍微等待，避免两个 server 日志混在一起
    time.sleep(0.5)

    # 启动命令 server
    cmd_thread = threading.Thread(target=start_cmd_server, daemon=True)
    cmd_thread.start()

    log_info("Both servers started. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_info("Shutting down...")
