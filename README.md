# attack_vln_deployment

Unitree Go2-W 的自定义 VLN 真机部署与基础运动控制项目。

本仓库提供三套互不覆盖的方案：

- `http_framework/`：模型无关的 VLN HTTP 部署框架，推荐用于自定义 VLN 真机闭环。
- `streamvln_framework/`：StreamVLN 原版真机部署的独立移植，保留其 evaluator、action sequence、累计目标与 PD 控制。
- `tcp_framework/`：原有 TCP 视频与手动控制方案，作为回退。

通用 Go2-W 网络配置、SDK2 开发与运动控制知识见 [`docs/Go2W_Development_Guide.md`](./docs/Go2W_Development_Guide.md)。

## 目录结构

```text
attack_vln_deployment/
├── AGENTS.md                       # 仓库协作规范
├── README.md                       # 项目入口
├── docs/
│   └── Go2W_Development_Guide.md   # 通用开发指南
├── http_framework/                 # 模型无关 VLN HTTP 部署
│   ├── host/                       # GPU server / backend 模板
│   ├── robot/                      # Robot client / diagnostic service
│   ├── examples/                   # smoke / RGB / 单动作示例
│   ├── tests/                      # 无硬件测试
│   └── README.md
├── streamvln_framework/            # StreamVLN 原版移植
│   ├── host/                       # /eval_vln GPU server
│   ├── robot/                      # ROS2 client / PD controller / service
│   ├── examples/                   # RGB / 单动作示例
│   ├── tests/
│   └── README.md
├── tcp_framework/                  # TCP 视频 + 控制回退
│   ├── host/                       # PC 端命令/视频/键盘控制
│   ├── robot/                      # Unitree 端命令/视频服务
│   ├── examples/                   # 推荐手工入口
│   ├── tests/
│   ├── config.py
│   └── README.md
└── vision/
    └── capture_rgb.py              # RealSense 取图工具
```

## 系统框架

### http_framework（推荐）

GPU 服务器加载自定义 VLN backend，按 instruction 对每张新图像推理离散 action；Unitree 端通过 D435i 采集图像，经 HTTP 请求获取 action。默认使用 StreamVLN 风格的里程计反馈 PD 控制执行 `forward` / `left` / `right`，并保留 `action_runner` 定时 fallback。

默认 PD 公式：

```text
v = clip(3.0 * translation_error - 0.5 * measured_velocity, -1.0, 1.0)
w = clip(3.0 * yaw_error - 0.5 * measured_yaw_rate, -1.2, 1.2)
control_rate = 10 Hz
```

### streamvln_framework（原版兼容）

保留 StreamVLN 真机代码的 `/eval_vln`、4-step evaluator、action sequence、累计 `homo_goal`、10 Hz PD、ROS2 RGB/里程计订阅和 Unitree request publisher。模型源码与 checkpoint 仍从外部 StreamVLN 目录加载。

### tcp_framework（回退）

基于 TCP 的视频与手动控制。PC 端通过 `host/` 发送命令、接收 D435i 实时视频流；Unitree 端 `robot/` 提供命令服务和视频流服务。

## 连接开发板

| 项目    | 值                  | 说明               |
| ------- | ------------------- | ------------------ |
| 无线 IP | `192.168.1.50`    | 当前 Wi-Fi 静态 IP |
| 有线 IP | `192.168.123.18`  | 机身尾部网线直连   |
| 用户名  | `unitree`         |                    |
| 密码    | `<set-on-device>` | 不要提交真实密码   |

> 当前推荐无线 IP `192.168.1.50`。该地址从 `192.168.1.200` 迁移，以避开 IPv4 地址冲突。建议在路由器中为 Unitree 无线 MAC `4c:b7:e0:e7:3e:5d` 保留 `.50`；只有需要稳定有线调试时才接网线使用 `192.168.123.18`。

```bash
# 测试连通性
ping 192.168.1.50

# SSH 登录
ssh unitree@192.168.1.50
```

## Unitree ROS2 接口配置

Go2-W 的运控主机通过机身尾部网线与开发板（你 SSH 的这台 Ubuntu）的 `eth0` 相连，发布 ROS2 topic；开发板再通过 `wlan0` 供你远程 SSH。因此：

- `wlan0`：你和开发板之间的管理/HTTP 网络（`192.168.1.50`）。
- `eth0`：开发板与机器人运控主机之间的 ROS2/DDS 网络（`192.168.123.18`）。

首次使用前需要在开发板上安装/编译 `unitree_ros2`：

```bash
cd ~
git clone https://github.com/unitreerobotics/unitree_ros2.git
cd unitree_ros2/cyclonedds_ws/src
git clone https://github.com/eclipse-cyclonedds/cyclonedds -b releases/0.10.x
git clone https://github.com/ros2/rmw_cyclonedds -b foxy
cd ..
colcon build --packages-select cyclonedds
source /opt/ros/foxy/setup.bash
colcon build
cd ~/unitree_ros2/example
source ~/unitree_ros2/cyclonedds_ws/install/setup.bash
colcon build
```

编辑 `~/unitree_ros2/setup.sh`，把 CycloneDDS 网卡改成 `eth0`：

```bash
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces>
                            <NetworkInterface name="eth0" priority="default" multicast="default" />
                        </Interfaces></General></Domain></CycloneDDS>'
```

验证（开发板上执行）：

```bash
source ~/unitree_ros2/setup.sh
# 查看 topic
ros2 topic list | grep sportmodestate
# 确认有 publisher
ros2 topic info /lf/sportmodestate
# 读取状态
~/unitree_ros2/example/install/unitree_ros2_example/bin/read_motion_state
```

> 注意：
>
> - Go2-W 实际发布的是 `/lf/sportmodestate`（低频次状态），`/sportmodestate` 没有 publisher。本仓库相关脚本默认使用 `/lf/sportmodestate`。
> - 如果 `ros2 topic info /lf/sportmodestate` 的 **Publisher count 为 0**，说明 CycloneDDS 没绑定到正确的网卡。检查 `CYCLONEDDS_URI` 里的 interface 必须是 `eth0`（机器人内部 DDS 网络），而不是 `wlan0` 或 ZeroTier 等远程 VPN 接口。

## 自定义 VLN HTTP 部署

GPU 服务器和 Unitree 都通过 `git clone` 拉取本仓库即可部署：

```bash
git clone https://github.com/shuzj0704/attack_vln_deployment.git
```

> 私有仓库需配置 SSH key 或使用 Personal Access Token。

部署分工：

| 位置          | 运行程序                                   | 负责内容                                           |
| ------------- | ------------------------------------------ | -------------------------------------------------- |
| GPU 服务器    | `python3 -m http_framework.host.server`  | 加载自定义 VLN、维护 model memory、返回离散 action |
| Unitree Go2-W | `python3 -m http_framework.robot.client` | D435i 拍照、HTTP 请求、执行动作、本地 StopMove     |

分别启动：

```bash
# GPU 服务器（仓库根目录）
python3 -m http_framework.host.server \
  --backend-factory <python_module>:<factory_function> \
  --host 0.0.0.0 \
  --port 5801

# Unitree
python3 -m http_framework.robot.client \
  --server-url http://<gpu_server_host>:5801 \
  --instruction "<navigation_instruction>" \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

未指定 `--executor` 时默认 `streamvln-pd`。需要原定时控制时增加 `--executor action-runner`。

完整协议、环境变量和安全说明见 [`http_framework/README.md`](./http_framework/README.md)。首次运行前请确认 `action_runner stop` 能在机器人本地调用 Unitree SDK2 `StopMove`。

### HTTP 通信快速测试

先在 GPU 服务器启动只返回 `stop` 的测试 backend：

```bash
# 确认 GPU 服务器用于 Unitree 的 IP
ip route get 192.168.1.50

python3 -m http_framework.host.server \
  --backend-factory http_framework.examples.smoke_backend:create_backend \
  --host 0.0.0.0 \
  --port 5801
```

在 Unitree 上测试端口和 health：

```bash
ssh unitree@192.168.1.50
cd /home/unitree/workspace/hkd/attack_vln_deployment

GPU_SERVER_IP=192.168.1.X  # 替换为 GPU 服务器实际地址
ping -c 3 "$GPU_SERVER_IP"
nc -zv "$GPU_SERVER_IP" 5801
curl -fsS "http://$GPU_SERVER_IP:5801/health"
```

返回 `"status":"ready"` 后，运行只执行 `stop` 的完整链路：

```bash
/home/unitree/unitree_sdk2/build/bin/action_runner stop

python3 -m http_framework.robot.client \
  --server-url "http://$GPU_SERVER_IP:5801" \
  --instruction "communication smoke test" \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner \
  --max-steps 1
```

预期看到 `step_id=0 action=stop` 和 `episode completed`。

## D435i RGB 与动作控制 examples

Robot 启动 `5802` HTTP service，Host 通过 `GET /rgb` 和 `POST /action` 独立检查相机和单个动作。

### 同步并启动 Robot service

Host 同步代码：

```bash
cd /home/shu22/navigation/indoor_vln/attack_vln_deployment

rsync -av --exclude '__pycache__' \
  http_framework/ \
  unitree@192.168.1.50:/home/unitree/workspace/hkd/attack_vln_deployment/http_framework/
```

Robot 启动 service：

```bash
ssh unitree@192.168.1.50
cd /home/unitree/workspace/hkd/attack_vln_deployment
python3 -m http_framework.robot.service \
  --host 0.0.0.0 \
  --port 5802 \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

默认启动 `streamvln-pd`；若 ROS2 topic 尚未准备好，可显式增加 `--executor action-runner`。

### Host example：查看 RGB

```bash
curl -fsS http://192.168.1.50:5802/health
python3 -m http_framework.examples.view_d435i_rgb
```

应打开 `Go2-W D435i` 窗口显示 `640×480` RGB，按 `q` 退出。

### Host example：控制单个动作

确认机器狗站稳、场地空旷、无台阶且遥控器急停可用。每次只运行一条，等待机器人完全停止后再继续：

```bash
# 只检查目标和动作，不发送
python3 -m http_framework.examples.control_robot left --dry-run

# StopMove
python3 -m http_framework.examples.control_robot stop

# 分别测试
python3 -m http_framework.examples.control_robot forward
python3 -m http_framework.examples.control_robot left
python3 -m http_framework.examples.control_robot right
```

`forward` 目标为当前位姿前方 `0.25 m`，`left/right` 目标为当前 yaw ± `15°`；每个动作结束或异常后都会发送零速度并 best-effort 调用 `action_runner stop`。

### 通过标准

| 测试项                   | 通过标志                       |
| ------------------------ | ------------------------------ |
| ping                     | 0% packet loss                 |
| SSH                      | 登录成功                       |
| `curl ...:5802/health` | 返回`status: ready`          |
| `view_d435i_rgb.py`    | 能显示连续 RGB 画面            |
| `control_robot.py`     | 返回`completed` 且动作后停止 |

`5801` 是 GPU VLN inference server，`5802` 是 Robot diagnostic service，不要混用。

## StreamVLN 原版真机部署

移植位于 `streamvln_framework/`，不覆盖 `http_framework/` 的 backend。

GPU 端启动（替换 checkpoint 路径和 instruction）：

```bash
python3 -m streamvln_framework.host.server \
  --streamvln-root /home/shu22/navigation/indoor_vln/StreamVLN \
  --model-path /path/to/streamvln_checkpoint \
  --instruction "<navigation_instruction>" \
  --host 0.0.0.0 \
  --port 5801 \
  --device cuda:0
```

Robot 端确认 ROS2 RGB、`/lf/sportmodestate` 和 StopMove 正常后启动：

```bash
python3 -m streamvln_framework.robot.client \
  --server-url http://<gpu_server_ip>:5801 \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

`streamvln_framework` 与 `http_framework` 默认都监听 GPU 端 `5801`，不能同时占用。完整说明见 [`streamvln_framework/README.md`](./streamvln_framework/README.md)。

### StreamVLN 单次 RGB / action examples

不加载模型时，先从 Host 用一条命令在 Robot 运行前期检查；它只调用 StopMove，不会
发送移动动作：

```bash
ssh -t unitree@192.168.1.50 \
  'cd /home/unitree/workspace/hkd/attack_vln_deployment && \
   python3 -m streamvln_framework.examples.preflight_check \
     --topic-timeout 10'
```

退出码 `0` 和 `Preflight PASSED` 表示依赖、冲突进程、RGB、里程计、`action_runner` 与
StopMove 均通过。出现 `[FAIL]` 或退出码 `1` 时不要开始动作测试。详细参数和手工排查
见 [`streamvln_framework/README.md#43-robot-一键前期检查推荐`](./streamvln_framework/README.md#43-robot-一键前期检查推荐)。

检查通过后，在 Robot 启动独立的 `5803` service；运行前先停止自动导航 client：

```bash
python3 -m streamvln_framework.robot.service \
  --host 0.0.0.0 \
  --port 5803 \
  --max-linear-velocity 0.25 \
  --max-yaw-rate 0.5
```

Host 获取一帧 RGB，或在 `--dry-run` 检查后发送一个 action：

```bash
python3 -m streamvln_framework.examples.fetch_robot_rgb \
  --output robot_rgb.jpg

python3 -m streamvln_framework.examples.control_robot left --dry-run
python3 -m streamvln_framework.examples.control_robot left
```

## 开发板系统

实测为 **NVIDIA Jetson Orin NX (16GB)**：

- OS：Ubuntu 20.04.5 LTS
- 内核：Linux 5.10.104-tegra
- 架构：arm64
- JetPack：5.1.1 / L4T 35.3.1
- CUDA：11.4

## 基础运动控制

Unitree SDK2 运动控制示例（`BalanceStand`、`Move`、`StopMove` 等）见 [`docs/Go2W_Development_Guide.md`](./docs/Go2W_Development_Guide.md)。运行控制程序前请确认机器狗已站稳、周围空旷且遥控器急停可用。

## 常用指令速查

| 指令                      | 说明           |
| ------------------------- | -------------- |
| `BalanceStand()`        | 平衡站立       |
| `Move(vx, vy, vyaw)`    | 速度控制       |
| `StopMove()`            | 停止当前运动   |
| `Damp()`                | 急停/阻尼      |
| `Sit() / RiseSit()`     | 坐下 / 站起    |
| `SwitchGait(d)`         | 切换步态       |
| `SwitchJoystick(false)` | 关闭遥控器响应 |

## 注意事项

- 首次测试建议用网线直连，在开阔平地操作。
- 随时准备好遥控器急停，或程序中保留 `Damp()` 调用路径。
- 低电量时机器狗会进入保护状态，请先充电。

---

更详细的网络配置、SDK 安装、状态订阅、故障排查等内容，请参考 [`docs/Go2W_Development_Guide.md`](./docs/Go2W_Development_Guide.md)。
