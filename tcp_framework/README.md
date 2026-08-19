# Go2-W TCP 视频 + 控制框架

通过 TCP 在主机和机器狗之间传输 D435i 实时图像和控制命令。

## 架构

```text
机器狗 (192.168.1.200)                          主机
├── video_server.py :5000  ───────────────►    ├── video_client.py
│   D435i JPEG 视频流                            │   显示实时画面
│                                               │
└── cmd_server.py   :6000  ◄───────────────    └── cmd_client.py / keyboard_control.py
    接收并执行控制命令                            发送 forward/left/right/stop 命令
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `config.py` | 共享配置：IP、端口、相机参数 |
| `robot/video_server.py` | 机器狗视频流 server |
| `robot/cmd_server.py` | 机器狗控制命令 server |
| `robot/robot_main.py` | 机器狗端入口 |
| `host/video_client.py` | 主机视频接收显示 |
| `host/cmd_client.py` | 主机发送单次命令 |
| `host/keyboard_control.py` | 主机键盘实时控制 |
| `host/host_main.py` | 主机端入口 |

## 启动步骤

### 1. 机器狗端

```bash
ssh unitree@192.168.1.200
cd <robot_project_root>/tcp_framework
python3 robot/robot_main.py
```

### 2. 主机端（键盘控制）

```bash
cd <local_project_root>/tcp_framework
python3 host/host_main.py
```

键盘控制：

| 按键 | 动作 |
|------|------|
| `W` | 前进约 25cm |
| `A` | 左转约 22.5 度 |
| `D` | 右转约 22.5 度 |
| `空格` | 停止 |
| `Q` | 退出 |

### 3. 主机端（仅发送单次命令）

```bash
python3 host/cmd_client.py forward
python3 host/cmd_client.py stop
```

### 4. 主机端（仅查看视频）

```bash
python3 host/video_client.py
```

## 配置修改

编辑 `config.py`：

```python
ROBOT_IP = "192.168.1.200"   # 机器狗 WiFi IP
VIDEO_PORT = 5000             # 视频流端口
CMD_PORT = 6000               # 控制命令端口
WIDTH = 640                   # 图像宽度
HEIGHT = 480                  # 图像高度
FPS = 15                      # 帧率
JPEG_QUALITY = 80             # JPEG 质量
```

## 通信协议

### 视频流

```text
[4 字节长度（大端）] + [JPEG 图像数据]
```

### 控制命令

```text
"command\n"
```

返回：

```text
"OK: ...\n" 或 "ERROR: ...\n"
```

## 支持的控制命令

| 命令 | 动作 |
|------|------|
| `forward` | 前进约 25cm |
| `left` | 左转约 22.5 度 |
| `right` | 右转约 22.5 度 |
| `stop` | 停止运动 |

> 所有运动命令基于机器狗里程计做闭环控制，精度受地面打滑影响。
