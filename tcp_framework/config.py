#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TCP 视频 + 控制框架共享配置
"""

# 机器狗网络配置
ROBOT_IP = "192.168.1.50"       # 机器狗 WiFi IP
ROBOT_HOST = "0.0.0.0"           # 机器狗监听地址

# 主机配置
HOST_IP = "0.0.0.0"              # 主机监听地址（如需反向连接）

# 通信端口
VIDEO_PORT = 5000                # 视频流 TCP 端口
CMD_PORT = 6000                  # 控制命令 TCP 端口

# D435i 相机参数
WIDTH = 640
HEIGHT = 480
FPS = 15

# JPEG 压缩质量 (0-100)
JPEG_QUALITY = 80

# 机器狗 action_runner 路径
ACTION_RUNNER = "/home/unitree/unitree_sdk2/build/bin/action_runner"

# 合法控制命令
# forward: 前进约 25cm
# left:    左转约 15 度
# right:   右转约 15 度
# stop:    停止运动
VALID_COMMANDS = ["forward", "left", "right", "stop"]
