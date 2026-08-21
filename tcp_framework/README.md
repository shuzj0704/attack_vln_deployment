# TCP Framework

Go2-W TCP 视频 + 控制回退方案。PC 端通过 TCP 接收 D435i 实时图像并发送控制命令；项目默认优先使用 `http_framework/`，本目录作为备用。

## 目录结构

```text
tcp_framework/
├── config.py                       # IP、端口、相机参数
├── host/                           # PC 端
│   ├── cmd_client.py               # 单次命令发送
│   ├── video_client.py             # 视频流接收显示
│   └── keyboard_control.py         # 键盘控制实现
├── robot/                          # Unitree 端
│   ├── robot_main.py               # 机器狗端入口
│   ├── cmd_server.py               # 控制命令服务
│   ├── video_server.py             # D435i 视频流服务
│   └── utils.py
├── examples/                       # 推荐手工入口
│   ├── view_d435i_rgb.py           # 查看 RGB
│   ├── control_robot.py            # 发送单个动作
│   └── keyboard_control.py         # 键盘控制
└── tests/                          # 无硬件自动化测试
```

## 架构

```text
机器狗 (192.168.1.50)                         PC 端
├── video_server.py :5000  ───────────►    ├── video_client.py
│   D435i JPEG 视频流                          │   显示实时画面
│                                             │
└── cmd_server.py   :6000  ◄───────────    ├── cmd_client.py
    接收并执行控制命令                          └── control_robot.py / keyboard_control.py
```

## 启动步骤

### 1. 机器狗端

```bash
ssh unitree@192.168.1.50
cd /home/unitree/workspace/hkd/attack_vln_deployment
python3 -m tcp_framework.robot.robot_main
```

### 2. PC 端查看 RGB

```bash
python3 -m tcp_framework.examples.view_d435i_rgb
```

按 `q` 退出。

### 3. PC 端发送单个动作

确认机器狗站稳、场地空旷、无台阶且遥控器急停可用；每次只发送一个动作：

```bash
# 只检查目标，不发送
python3 -m tcp_framework.examples.control_robot forward --dry-run

# 分别测试
python3 -m tcp_framework.examples.control_robot stop
python3 -m tcp_framework.examples.control_robot forward
python3 -m tcp_framework.examples.control_robot left
python3 -m tcp_framework.examples.control_robot right
```

`left` / `right` 映射为 `action_runner turn_left / turn_right`，每个 primitive 后自动 `stop`。

### 4. PC 端键盘控制

```bash
python3 -m tcp_framework.examples.keyboard_control
```

| 按键 | 动作 |
|---|---|
| `W` | 前进约 25 cm |
| `A` | 左转约 15° |
| `D` | 右转约 15° |
| 空格 | 停止 |
| `Q` | 退出 |

### 5. 底层入口（可选）

```bash
python3 -m tcp_framework.host.cmd_client forward
python3 -m tcp_framework.host.cmd_client stop
python3 -m tcp_framework.host.video_client
```

## 配置修改

编辑 `tcp_framework/config.py`：

```python
ROBOT_IP = "192.168.1.50"    # 机器狗 WiFi IP
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

## 支持的命令

| 命令 | 动作 |
|---|---|
| `forward` | 前进约 25 cm |
| `left` | 左转约 15°（`turn_left`） |
| `right` | 右转约 15°（`turn_right`） |
| `stop` | 停止运动 |

`forward` / `left` / `right` 由共享的 `ActionRunnerExecutor` 执行，每个 primitive 后调用 `stop`。实际距离和角度会受地面打滑及底层控制状态影响。

## 无硬件测试

```bash
python3 -m compileall -q tcp_framework
python3 -m unittest discover -s tcp_framework/tests -v
```
