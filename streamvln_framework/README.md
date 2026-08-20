# StreamVLN 原版真机部署兼容框架

`streamvln_framework/` 是从 StreamVLN 原仓库真机代码拆出的独立入口，与 `http_framework/`、`tcp_framework/` 同级。它保留 StreamVLN 的模型调用协议和 Go2-W 闭环控制方式，不替换模型无关的 `http_framework/`。

```text
Robot ROS2 RGB ── POST /eval_vln ──> GPU StreamVLN evaluator
       ^                                  │
       │                            action sequence
       │                                  │
/lf/sportmodestate ──> cumulative homo_goal ─┘
                         │
                    10 Hz PD
                         │
                  /api/sport/request
```

## 目录结构

```text
streamvln_framework/
├── host/
│   └── server.py              # GPU：加载原 StreamVLN 并提供 /eval_vln
├── robot/
│   ├── client.py              # Go2-W：ROS2 图像、里程计、规划与速度控制
│   ├── pd_controller.py       # 累计目标与原版 PD 公式
│   └── service.py             # RGB / 单动作 diagnostic service
├── examples/
│   ├── smoke_server.py        # 无模型 smoke server
│   ├── preflight_check.py     # Robot 一键前期检查
│   ├── fetch_robot_rgb.py     # 获取单帧 RGB
│   └── control_robot.py       # 发送单个动作
├── tests/
│   └── test_deployment.py
└── README.md
```

## 与上游映射

| StreamVLN 原文件                       | 本框架文件                 |
| -------------------------------------- | -------------------------- |
| `realworld/go2_vln_client.py`        | `robot/client.py`        |
| `realworld/pid_controller.py`        | `robot/pd_controller.py` |
| `streamvln/http_realworld_server.py` | `host/server.py`         |

## 核心行为

- Robot 订阅 `/camera/camera/color/image_raw` 和 `/lf/sportmodestate`。
- GPU 接收 `POST /eval_vln`，每次请求执行默认 4 个 evaluator internal steps，返回 action sequence。
- action ID：`0=stop`、`1=forward 0.25 m`、`2=left 15°`、`3=right 15°`。
- action sequence 累加到持久的 `homo_goal`，以 10 Hz PD 发布 Unitree `SPORT_API_ID_MOVE=1008` 到 `/api/sport/request`。
- PD 默认参数与上游一致：

  ```text
  Kp/Kd translation = 3.0 / 0.5
  Kp/Kd yaw         = 3.0 / 0.5
  max_v             = 1.0
  max_w             = 1.2
  ```

部署层安全与可维护性修正：硬编码的 checkpoint、instruction 和 server 地址改为 CLI 参数；HTTP 输入校验；线程可退出；停止、异常和关闭时额外调用本机 `action_runner stop`。模型、训练代码和 checkpoint 不复制到本仓库，GPU 端仍从外部 StreamVLN 源码目录加载。

## 1. 无模型通信测试

在项目根目录启动只返回 `stop` 的安全 smoke server：

```bash
python3 -m streamvln_framework.examples.smoke_server \
  --host 0.0.0.0 \
  --port 5801
```

另一终端检查：

```bash
curl -fsS http://127.0.0.1:5801/health
```

该测试不加载模型，也不会自行连接 Robot。

## 2. GPU 端启动真实 StreamVLN

先按 StreamVLN 原仓库安装模型依赖并准备 checkpoint，然后在项目根目录运行：

```bash
python3 -m streamvln_framework.host.server \
  --streamvln-root /path/to/StreamVLN \
  --model-path /path/to/streamvln_checkpoint \
  --instruction "<navigation_instruction>" \
  --host 0.0.0.0 \
  --port 5801 \
  --device cuda:0
```

`--streamvln-root` 必须包含 `streamvln/streamvln_agent.py`。启动时会加载模型并执行一次 warmup；看到 Flask 监听日志后检查：

```bash
curl -fsS http://127.0.0.1:5801/health
```

`5801` 也是 `http_framework` 默认端口，同一台 GPU 上一次只能启动其中一个 server；需要同时运行时给其中一个指定其他端口，并同步修改 Robot 的 `--server-url`。

## 3. Robot 端准备与启动

Robot 必须能导入 `rclpy`、`cv_bridge`、`sensor_msgs`、`unitree_api` 和 `unitree_go`。启动前确认 RealSense 与 Unitree topics：

```bash
ros2 topic list | grep -E 'color/image_raw|sportmodestate'
ros2 topic hz /camera/camera/color/image_raw
ros2 topic hz /lf/sportmodestate
```

若 RGB topic 不存在，启动 RealSense ROS2 包，例如：

```bash
ros2 launch realsense2_camera rs_launch.py
```

确认机器人站稳、周围无障碍和台阶、遥控器急停可用后，先验证本机 StopMove：

```bash
/home/unitree/unitree_sdk2/build/bin/action_runner stop
```

再启动闭环 client：

```bash
python3 -m streamvln_framework.robot.client \
  --server-url http://<gpu_server_ip>:5801 \
  --rgb-topic /camera/camera/color/image_raw \
  --odometry-topic /lf/sportmodestate \
  --request-topic /api/sport/request \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

首次真机测试建议临时降低速度上限，例如 `--max-linear-velocity 0.25 --max-yaw-rate 0.5`。这会偏离原版速度上限，但不改变 forward/turn 的目标距离和角度。按 `Ctrl+C` 时 client 会发送零速度并 best-effort 执行 StopMove。

## 4. Action 测试

本节不加载 StreamVLN 模型，只验证 Robot 端动作执行链路。Host 侧的测试命令（`control_robot`、`test_actions`）只有一套；**切换控制器是在 Unitree 启动 service 时通过 `--executor` 完成的**。

### 4.1 启动 service 前检查

**电脑**：Unitree（`ssh unitree@192.168.1.50`）
**路径**：`/home/unitree/workspace/hkd/attack_vln_deployment`

```bash
source ~/unitree_ros2/setup.sh
cd /home/unitree/workspace/hkd/attack_vln_deployment

# 检查 CycloneDDS 是否绑定到机器人内部网卡 eth0
ros2 topic info /lf/sportmodestate
```

> - 期望看到 `Publisher count: 1`。
> - 若为 0 或 `Unknown topic`，检查 `CYCLONEDDS_URI` 里的 interface 必须是 `eth0`，而不是 `wlan0` 或 ZeroTier。必要时重启 ros2 daemon：
>   ```bash
>   ros2 daemon stop && ros2 daemon start
>   ```

### 4.2 选择控制器

service 支持两种执行器，启动时二选一。**默认是定时开环**，因为目前 `/lf/sportmodestate` 的里程计不够稳定。

#### 4.2.1 定时开环（默认）

不读取 `/lf/sportmodestate` 的位置/速度，直接按固定速度跑固定时间，然后停止。

```bash
python3 -m streamvln_framework.robot.service \
  --host 0.0.0.0 --port 5803
```

或显式指定：

```bash
python3 -m streamvln_framework.robot.service \
  --host 0.0.0.0 --port 5803 \
  --executor timed
```

#### 4.2.2 闭环 PD（里程计稳定后可选，当前 forward 有问题）

依赖 `/lf/sportmodestate` 的 `position` 和 `velocity`。forward 的目标是根据当前位姿前方 `0.25 m`，left/right 的目标是根据当前 yaw ±`15°`，由 10 Hz PD 控制直到进入容忍区。

```bash
python3 -m streamvln_framework.robot.service \
  --host 0.0.0.0 --port 5803 \
  --executor streamvln
```

> ⚠️ 当前闭环 PD 的 `forward` 存在问题：`/lf/sportmodestate` 里的 `position`/`velocity` 在 forward 过程中不更新（`gait_type` 始终为 0），导致 PD 算不到目标距离，会持续发速度命令直到超时。`left`/`right` 因为只依赖 IMU yaw，目前可以正常停止。在里程计/步态问题排查清楚前，建议用默认的 `--executor timed`。

定时参数：

| action      | 线速度 / 角速度       | 持续时间    | 理论结果     |
| ----------- | --------------------- | ----------- | ------------ |
| `forward` | `vx = 0.5 m/s`      | `0.5 s`   | 约`0.25 m` |
| `left`    | `vyaw = +0.5 rad/s` | `0.524 s` | 约`+15°`  |
| `right`   | `vyaw = -0.5 rad/s` | `0.524 s` | 约`-15°`  |
| `stop`    | 零速度 +`StopMove`  | -           | -            |

> 两种执行器下，Host 的 action 测试命令**完全一样**，只是 Unitree 上 service 的启动参数不同。

### 4.3 检查 service 状态

**电脑**：Host（本机）
**路径**：任意，或项目根目录 `/home/shu22/navigation/indoor_vln/attack_vln_deployment`

```bash
ROBOT_IP=192.168.1.50
curl -fsS "http://$ROBOT_IP:5803/health"
```

期望返回 `status=ready`。

- 使用 `--executor streamvln` 时，还应确认 `odometry_received=true`。
- 使用 `--executor timed` 时，`odometry_received` 可能为 `false`，这不影响动作测试。

### 4.4 测试 4 个 action

**电脑**：Host（本机）
**路径**：`/home/shu22/navigation/indoor_vln/attack_vln_deployment`

逐个测试（推荐）：

```bash
cd /home/shu22/navigation/indoor_vln/attack_vln_deployment
python3 -m streamvln_framework.examples.control_robot stop
python3 -m streamvln_framework.examples.control_robot forward
python3 -m streamvln_framework.examples.control_robot left
python3 -m streamvln_framework.examples.control_robot right
```

或一键顺序测试：

```bash
python3 -m streamvln_framework.examples.test_actions --yes
```

动作语义：

| action      | 目标              | 说明                     |
| ----------- | ----------------- | ------------------------ |
| `forward` | 前进约`0.25 m`  | 沿动作开始时机身朝向前进 |
| `left`    | yaw 增加`15°`  | 原地左转，不是向左横移   |
| `right`   | yaw 减少`15°`  | 原地右转                 |
| `stop`    | 零速度 + StopMove | 不产生新目标             |

### 4.5 （可选）获取单帧 RGB

**电脑**：Host（本机）
**路径**：`/home/shu22/navigation/indoor_vln/attack_vln_deployment`

```bash
python3 -m streamvln_framework.examples.fetch_robot_rgb \
  --server-url "http://$ROBOT_IP:5803" \
  --output robot_rgb.jpg
```

### 4.6 停止 service

**电脑**：Unitree 或 Host 均可

- 在 Unitree service 终端按 `Ctrl+C`；或
- 在 Host 上先发一次 `stop`：

  ```bash
  cd /home/shu22/navigation/indoor_vln/attack_vln_deployment
  python3 -m streamvln_framework.examples.control_robot stop
  ```

## 5. 与 `http_framework` 的边界

- 复现/继续使用 StreamVLN 原版 evaluator、action sequence 和累计目标控制：使用本目录。
- 接入你自己的 VLN，仅需实现 backend 接口：使用 `http_framework/`。
- 查看 RGB、手动发送单个动作：使用 `http_framework/examples/` 与本目录 examples。

本框架没有复制 checkpoint，也没有在无模型环境中验证真实推理数值；无硬件测试只覆盖 HTTP 协议、4-step evaluator 调度、action 累计和 PD 公式。
