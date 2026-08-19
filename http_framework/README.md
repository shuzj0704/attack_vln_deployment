# StreamVLN 无 ROS 真机 HTTP 部署

本目录是一套独立的 `receding_horizon_single_step` deployment mode。它不修改
StreamVLN 官方多步评测代码，也不修改现有 `tcp_framework/`。GPU server 只做
离散 action 推理；D435i 采集、primitive motion、稳定等待和所有安全停止都在
Go2-W 本地完成。

## 代码位置与部署分工

当前实现写在本地 `attack_vln_deployment/http_framework/`，本次修改没有通过 SSH、`scp` 或
`rsync` 写入 Unitree，也没有在任一机器上启动服务。部署时两端职责如下：

| 机器 | 必需代码/资源 | 启动入口 | 不需要的资源 |
|---|---|---|---|
| GPU 服务器 | `attack_vln_deployment/http_framework/`、StreamVLN repository、checkpoint | `http_framework.server` | D435i、Unitree SDK2 |
| Unitree Go2-W | `attack_vln_deployment/http_framework/`、`action_runner`、D435i runtime | `http_framework.robot.client` | StreamVLN repository、checkpoint、CUDA model environment |

两端复制同一个 `http_framework/` 目录最简单。Robot 入口只会导入 `protocol.py` 和
`robot/` 下的模块，不会加载 `streamvln_backend.py`、PyTorch 或 checkpoint。

如果当前开发机就是 GPU 服务器，只需把 robot 侧代码同步到 Unitree：

```bash
# 在本地 attack_vln_deployment 根目录运行
rsync -av --exclude '__pycache__' \
  http_framework/ \
  <robot_user>@<robot_host>:<robot_project_root>/http_framework/
```

如果 GPU 服务器是另一台机器，还应把 `attack_vln_deployment/http_framework/` 同步到 GPU
服务器的 `attack_vln_deployment` 根目录。不要把 checkpoint 复制到 Unitree。所有尖括号占位符
都必须按实际环境填写；代码没有内置机器 IP 或安装路径。

部署后的推荐检查顺序：

1. GPU 服务器启动 HTTP server。
2. 从 Unitree 执行 `curl http://<gpu_server_host>:5801/health`，确认返回
   `"status": "ready"`。
3. 单独确认 D435i 能采集 `640x480` color frame。
4. 在不上电运动或架空安全条件下确认 `action_runner stop` 可用。
5. 在空旷平地、保留遥控器急停的情况下启动 robot client。

## 闭环与动作语义

```text
robot StopMove -> settle -> capture one 640x480 RGB JPEG
     -> POST /step -> receive exactly one action
     -> execute primitive -> StopMove -> repeat with a fresh image
```

| action | StreamVLN id | 本地 primitive |
|---|---:|---|
| `forward` | 1 | 前进 `0.25 m` |
| `left` | 2 | 左转 `+15°` (`turn_left`) |
| `right` | 3 | 右转 `-15°` (`turn_right`) |
| `stop` | 0 | `StopMove` 并结束 episode |

模型可以生成 action sequence，但 server 只返回其中第一个合法 action。机器人执行
一个 primitive 后必须使用新 RGB 再请求。模型输出为空或完全非法时 server 按安全
策略返回 `stop`。

## HTTP protocol

所有成功和错误响应都是 JSON。`step_id` 从 `0` 开始且必须严格递增。

- `GET /health`
- `POST /reset`，JSON：

  ```json
  {"instruction": "Walk out of the room.", "request_id": "uuid"}
  ```

  返回 `episode_id` 和 `next_step_id: 0`。相同 `request_id`、相同 instruction 的
  重试不会再次 reset memory。

- `POST /step`，`multipart/form-data`：`episode_id`、`step_id`、`request_id` 和
  JPEG field `image`。返回：

  ```json
  {
    "episode_id": "uuid",
    "step_id": 0,
    "request_id": "uuid",
    "action": "forward",
    "deduplicated": false
  }
  ```

  Server 按 `(episode_id, step_id)` 缓存 action 和 JPEG SHA-256。相同步骤、相同
  图像的网络重试直接返回缓存结果，不会再次调用模型；相同步骤但图像不同会返回
  HTTP 409。复用 `request_id` 到其他步骤、跳号、过期 episode 也会被拒绝。

- `POST /close`，JSON：`episode_id`、`request_id`。用于 best-effort 清理；运动停止
  不依赖它，本地 `StopMove` 始终优先。

当前 StreamVLN adapter 对单个 model env 串行服务，因此同一时刻只有一个 active
episode；新的 `/reset` 会替换旧 episode。

## GPU server 启动

在 `attack_vln_deployment` 根目录运行（路径和监听地址均通过 CLI 或环境变量提供）：

```bash
python3 -m http_framework.server \
  --streamvln-root /path/to/StreamVLN \
  --model-path /path/to/checkpoint \
  --host 0.0.0.0 \
  --port 5801 \
  --device cuda:0
```

等价环境变量包括 `STREAMVLN_ROOT`、`STREAMVLN_MODEL_PATH`、
`STREAMVLN_HTTP_HOST`、`STREAMVLN_HTTP_PORT` 和 `STREAMVLN_DEVICE`。其余参数见：

```bash
python3 -m http_framework.server --help
```

Server 复用 StreamVLN 环境已有的 Flask、Pillow、NumPy、PyTorch 和 Transformers，
没有新增 Python package。

## Go2-W robot client 启动

Robot 侧需要已有的 `requests`、`pyrealsense2`、NumPy 和 OpenCV。现有
`action_runner` 应基于 Unitree SDK2，并支持：

```text
action_runner forward <duration_s> <speed_mps>
action_runner turn_left <duration_s> <speed_radps>
action_runner turn_right <duration_s> <speed_radps>
action_runner stop
```

启动示例：

```bash
python3 -m http_framework.robot.client \
  --server-url http://GPU_HOST:5801 \
  --instruction "Walk out of the room and stop." \
  --action-runner /path/to/unitree_sdk2/action_runner
```

也可用 `--instruction-file`，instruction 没有硬编码。常用环境变量为
`STREAMVLN_SERVER_URL`、`UNITREE_ACTION_RUNNER`、`STREAMVLN_CONNECT_TIMEOUT`、
`STREAMVLN_READ_TIMEOUT`、`STREAMVLN_SETTLE_TIME`。HTTP client 使用一个
`requests.Session` 复用连接；超时重试沿用相同 `request_id/step_id/JPEG`。

`ActionExecutor` 是可替换接口。当前 `ActionRunnerExecutor` 根据距离/角度和速度计算
动作时长，每次动作后调用 `action_runner stop`。如果已有 runner 的 CLI 或闭环方式
不同，应新增另一个 `ActionExecutor`，不要改变 HTTP protocol。

任何网络超时、非 JSON/非法 action、顺序错误、相机异常或动作异常都会使 robot
client 在本地 best-effort 调用 `StopMove`。真机仍需保留遥控器急停，并先在空旷平地
低速验证 runner 的距离、转角符号和停止行为。

## 无硬件测试

```bash
python3 -m compileall -q http_framework
python3 -m unittest discover -s http_framework/tests -v
```

测试只使用 fake backend/session/camera/executor，不加载 checkpoint，也不访问相机或
机器人。
