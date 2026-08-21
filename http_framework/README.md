# HTTP Framework

模型无关的 VLN 真机 HTTP 部署框架。GPU server 负责推理，Robot 端负责 D435i 采集、primitive 执行和安全停止。仓库不包含具体 VLN 模型、checkpoint 或推理逻辑。

## 目录结构

```text
http_framework/
├── host/                  # GPU/Host inference server
│   ├── server.py          # HTTP server
│   └── vln_backend.py     # backend 接口模板
├── robot/                 # Robot client / diagnostic service
│   ├── client.py          # 闭环 VLN client
│   ├── service.py         # RGB / 单动作 diagnostic service
│   ├── pd_executor.py     # StreamVLN 风格 PD executor
│   └── action_executor.py # action_runner fallback
├── examples/              # 手工真机入口
│   ├── smoke_backend.py   # 通信测试 backend
│   ├── view_d435i_rgb.py  # 查看 RGB
│   └── control_robot.py   # 发送单个动作
├── tests/                 # 无硬件自动化测试
└── protocol.py            # Host/Robot 共用协议
```

## 部署分工

| 位置 | 启动入口 | 必需资源 |
|---|---|---|
| GPU 服务器 | `python3 -m http_framework.host.server` | `http_framework/`、用户 VLN 代码与 checkpoint |
| Unitree Go2-W | `python3 -m http_framework.robot.client` | `http_framework/`、ROS2 Unitree messages、`action_runner`、D435i |

同步代码到 Robot：

```bash
rsync -av --exclude '__pycache__' \
  http_framework/ \
  unitree@192.168.1.50:/home/unitree/workspace/hkd/attack_vln_deployment/http_framework/
```

## Backend 接口

接口定义在 `http_framework.protocol.InferenceBackend`，默认模板在 `http_framework/host/vln_backend.py`：

```python
class InferenceBackend(Protocol):
    def reset(self, instruction: str) -> None: ...
    def step(self, jpeg_bytes: bytes, instruction: str) -> BackendOutput: ...
    def close(self) -> None: ...
```

- `reset()`：开始新 episode，清空 temporal/memory state。
- `step()`：接收当前 JPEG 和 instruction，返回一个 action。
- `close()`：清理 episode state。
- `BackendOutput`：字符串 `forward` / `left` / `right` / `stop`，或框架 ID `1` / `2` / `3` / `0`。

框架只执行第一个合法 action；非法输出安全归一化为 `stop`。自定义 action space 请在 backend 内完成映射。 factory 必须是无参数 callable。

## 动作语义

| action | ID | primitive |
|---:|---:|---|
| `forward` | 1 | 前进，默认目标 `0.25 m` |
| `left` | 2 | 左转，默认目标 yaw `+15°` |
| `right` | 3 | 右转，默认目标 yaw `-15°` |
| `stop` | 0 | `StopMove` 并结束 episode |

执行流程：

```text
StopMove -> settle -> capture JPEG -> POST /step -> execute action -> StopMove -> repeat
```

## HTTP 协议

所有响应均为 JSON。`step_id` 从 `0` 开始严格递增。

- `GET /health`
- `POST /reset`：`{"instruction": "...", "request_id": "uuid"}`，返回 `episode_id` 和 `next_step_id: 0`。
- `POST /step`：`multipart/form-data` 包含 `episode_id`、`step_id`、`request_id` 和 JPEG `image`。

  返回示例：

  ```json
  {
    "episode_id": "uuid",
    "step_id": 0,
    "request_id": "uuid",
    "action": "forward",
    "deduplicated": false
  }
  ```

  相同 `(episode_id, step_id)` 与图像 SHA-256 命中缓存时直接返回旧结果；跳号、复用 `request_id` 或图像不同返回 HTTP 409。

- `POST /close`：`{"episode_id": "...", "request_id": "uuid"}`，best-effort 清理 backend。

## 启动 GPU server

使用自定义 backend：

```bash
export MY_VLN_CHECKPOINT=/path/to/checkpoint
python3 -m http_framework.host.server \
  --backend-factory my_vln.deployment:create_backend \
  --host 0.0.0.0 \
  --port 5801
```

直接修改默认模板时可省略 `--backend-factory`：

```bash
python3 -m http_framework.host.server --host 0.0.0.0 --port 5801
```

通用环境变量：`VLN_BACKEND_FACTORY`、`VLN_HTTP_HOST`、`VLN_HTTP_PORT`、`VLN_MAX_IMAGE_BYTES`。

## 启动 Robot client

默认 motion executor 为 `streamvln-pd`，需要 ROS2 `rclpy`、`unitree_api.msg`、`unitree_go.msg`，并保证 `/sportmodestate` 与 `/api/sport/request` 可用。每个动作结束和异常时都会调用 `action_runner stop`。

```bash
python3 -m http_framework.robot.client \
  --server-url http://<gpu_host>:5801 \
  --instruction "Walk out of the room and stop." \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

如需切回定时 `action_runner` 实现：

```bash
--executor action-runner
```

PD 参数可通过 `--pd-*` 系列参数覆盖；首次真机建议先用 `--pd-max-linear-velocity` / `--pd-max-yaw-rate` 降低速度上限。

## HTTP 通信 smoke test

使用 `http_framework.examples.smoke_backend` 区分 HTTP 链路与推理问题。

### 1. 启动安全 backend

```bash
python3 -m http_framework.host.server \
  --backend-factory http_framework.examples.smoke_backend:create_backend \
  --host 0.0.0.0 \
  --port 5801
```

本机检查：

```bash
curl -fsS http://127.0.0.1:5801/health
```

### 2. Robot 侧验证端口与协议

```bash
GPU_SERVER_IP=192.168.1.X

ping -c 3 "$GPU_SERVER_IP"
nc -zv "$GPU_SERVER_IP" 5801
curl -fsS "http://$GPU_SERVER_IP:5801/health"
```

测试 `/reset`、`/step`、`/close`：

```bash
RESET_REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
STEP_REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
CLOSE_REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')

RESET_RESPONSE=$(curl -fsS -X POST \
  "http://$GPU_SERVER_IP:5801/reset" \
  -H 'Content-Type: application/json' \
  -d "{\"instruction\":\"communication smoke test\",\"request_id\":\"$RESET_REQUEST_ID\"}")
echo "$RESET_RESPONSE"

EPISODE_ID=$(printf '%s' "$RESET_RESPONSE" | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["episode_id"])')

curl -fsS -X POST "http://$GPU_SERVER_IP:5801/step" \
  -F "episode_id=$EPISODE_ID" \
  -F 'step_id=0' \
  -F "request_id=$STEP_REQUEST_ID" \
  -F 'image=@/etc/hostname;filename=smoke.jpg;type=image/jpeg'

curl -fsS -X POST "http://$GPU_SERVER_IP:5801/close" \
  -H 'Content-Type: application/json' \
  -d "{\"episode_id\":\"$EPISODE_ID\",\"request_id\":\"$CLOSE_REQUEST_ID\"}"
```

通过标志：

- `/health` 返回 `status: ready`。
- `/reset` 返回 `episode_id` 和 `next_step_id: 0`。
- `/step` 返回 `action: stop`，server 日志 HTTP 200。
- `/close` 返回 `closed: true`。

### 3. D435i + client 完整链路

确认 `action_runner stop` 有效后：

```bash
python3 -m http_framework.robot.client \
  --server-url "http://$GPU_SERVER_IP:5801" \
  --instruction "communication smoke test" \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner \
  --max-steps 1
```

预期输出包含 `step_id=0 action=stop` 和 `episode completed`。

## Robot diagnostic service 与 examples

`http_framework.robot.service` 提供独立的 RGB 与单动作检查，默认监听 `5802`。D435i 同一时刻只能被一个进程占用，启动前请停止 `robot.client` 或 TCP video server。

启动 service：

```bash
python3 -m http_framework.robot.service \
  --host 0.0.0.0 \
  --port 5802 \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

Host 端示例：

```bash
# 检查 health
curl -fsS http://192.168.1.50:5802/health

# 查看 RGB
python3 -m http_framework.examples.view_d435i_rgb \
  --server-url http://192.168.1.50:5802

# 发送单个动作（先 --dry-run 检查）
python3 -m http_framework.examples.control_robot forward \
  --server-url http://192.168.1.50:5802 --dry-run
python3 -m http_framework.examples.control_robot stop \
  --server-url http://192.168.1.50:5802
python3 -m http_framework.examples.control_robot forward \
  --server-url http://192.168.1.50:5802
```

移动前请确认机器狗站稳、周围空旷、无台阶且遥控器急停可用；每次只发送一个动作，等待完全停止后再继续。`left` / `right` 为原地转向，不是横移。

## 无硬件测试

```bash
python3 -m compileall -q http_framework
python3 -m unittest discover -s http_framework/tests -v
```

测试使用 fake backend / camera / executor，不加载 checkpoint、不访问真机。
