# 自定义 VLN 无 ROS 真机 HTTP 部署

本目录提供模型无关的 `receding_horizon_single_step` 部署框架。GPU server 负责调用
用户提供的 VLN backend；D435i 图像采集、primitive motion、稳定等待和安全停止在
Unitree Go2-W 本地完成。仓库不包含具体 VLN 模型、checkpoint 加载或推理逻辑。

## 部署分工

| 机器 | 必需代码/资源 | 启动入口 | 不需要的资源 |
|---|---|---|---|
| GPU 服务器 | `http_framework/`、用户 VLN 代码和 checkpoint | `http_framework.server` | D435i、Unitree SDK2 |
| Unitree Go2-W | `http_framework/`、`action_runner`、D435i runtime | `http_framework.robot.client` | VLN 代码、checkpoint、CUDA 环境 |

Robot 入口只导入通用协议和 `robot/` 模块，不会加载用户模型。可以从仓库根目录同步：

```bash
rsync -av --exclude '__pycache__' \
  http_framework/ \
  <robot_user>@<robot_host>:<robot_project_root>/http_framework/
```

## VLN backend 接口

接口定义在 `http_framework.protocol.InferenceBackend`，默认实现模板位于
`http_framework/vln_backend.py`：

```python
class InferenceBackend(Protocol):
    def reset(self, instruction: str) -> None: ...
    def step(self, jpeg_bytes: bytes, instruction: str) -> BackendOutput: ...
    def close(self) -> None: ...
```

- `reset()`：开始新 episode，并清空模型的 temporal/memory state。
- `step()`：接收当前 JPEG bytes 和 instruction，执行一次推理。
- `close()`：清理 episode state；模型权重可以继续驻留显存。
- `BackendOutput`：一个 action，或 action sequence。支持字符串 `forward`、`left`、
  `right`、`stop`；也支持框架 ID `1`、`2`、`3`、`0`。

框架只执行第一个合法 action，然后请求一张新图像。空输出或完全非法的输出会安全地
归一化为 `stop`。模型若使用其他 action space，请在 backend 内完成映射。

最直接的接入方式是编辑 `http_framework/vln_backend.py`，在 `VLNBackend.__init__()`
加载模型，并实现三个方法。也可以把模型适配器放在自己的 Python package 中：

```python
# my_vln/deployment.py
import os


class MyVLNBackend:
    def __init__(self):
        checkpoint = os.environ["MY_VLN_CHECKPOINT"]
        # Load the model once here.

    def reset(self, instruction):
        # Reset model memory here.
        pass

    def step(self, jpeg_bytes, instruction):
        # Decode/preprocess jpeg_bytes, run inference, and map the output.
        return "stop"

    def close(self):
        # Clear episode state here.
        pass


def create_backend():
    return MyVLNBackend()
```

Factory 必须是无参数 callable，返回具有 `reset`、`step`、`close` 方法的对象。模型路径、
device 和自定义超参数由 factory 自己通过环境变量或项目配置读取，HTTP 框架不绑定模型配置。

## 闭环与动作语义

```text
robot StopMove -> settle -> capture one RGB JPEG
     -> POST /step -> receive exactly one action
     -> execute primitive -> StopMove -> repeat with a fresh image
```

| action | framework id | 本地 primitive |
|---|---:|---|
| `forward` | 1 | 前进，默认 `0.25 m` |
| `left` | 2 | 左转，默认 `+15°` (`turn_left`) |
| `right` | 3 | 右转，默认 `-15°` (`turn_right`) |
| `stop` | 0 | `StopMove` 并结束 episode |

## HTTP protocol

所有成功和错误响应都是 JSON。`step_id` 从 `0` 开始且必须严格递增。

- `GET /health`
- `POST /reset`，JSON：

  ```json
  {"instruction": "Walk out of the room.", "request_id": "uuid"}
  ```

  返回 `episode_id` 和 `next_step_id: 0`。相同 `request_id`、相同 instruction 的重试
  不会再次 reset backend。

- `POST /step`，`multipart/form-data`：`episode_id`、`step_id`、`request_id` 和 JPEG
  field `image`。返回：

  ```json
  {
    "episode_id": "uuid",
    "step_id": 0,
    "request_id": "uuid",
    "action": "forward",
    "deduplicated": false
  }
  ```

  Server 按 `(episode_id, step_id)` 缓存 action 和 JPEG SHA-256。相同步骤、相同图像的
  网络重试直接返回缓存结果；相同步骤但图像不同、复用 `request_id` 或跳号均返回
  HTTP 409。

- `POST /close`，JSON：`episode_id`、`request_id`。用于 best-effort backend 清理；
  机器人运动停止不依赖它，本地 `StopMove` 始终优先。

一个 server 实例串行调用一个 backend，同一时刻只有一个 active episode。新的
`/reset` 会替换旧 episode。

## 启动 GPU server

先实现 backend，然后从仓库根目录运行：

```bash
export MY_VLN_CHECKPOINT=/path/to/checkpoint
python3 -m http_framework.server \
  --backend-factory my_vln.deployment:create_backend \
  --host 0.0.0.0 \
  --port 5801
```

如果直接实现默认模板，可省略 `--backend-factory`：

```bash
python3 -m http_framework.server --host 0.0.0.0 --port 5801
```

默认模板在未实现时会明确抛出 `NotImplementedError`，不会伪装成可用服务。通用环境变量为
`VLN_BACKEND_FACTORY`、`VLN_HTTP_HOST`、`VLN_HTTP_PORT` 和 `VLN_MAX_IMAGE_BYTES`。

## 启动 Go2-W robot client

Robot 侧需要 `requests`、`pyrealsense2`、NumPy 和 OpenCV。`action_runner` 应支持：

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
  --camera-width 640 \
  --camera-height 480 \
  --action-runner /path/to/unitree_sdk2/action_runner
```

也可使用 `--instruction-file`。常用环境变量为 `VLN_SERVER_URL`、
`VLN_CONNECT_TIMEOUT`、`VLN_READ_TIMEOUT`、`VLN_SETTLE_TIME`、`VLN_CAMERA_WIDTH`、
`VLN_CAMERA_HEIGHT` 和 `UNITREE_ACTION_RUNNER`。完整参数见两个入口的 `--help`。

`ActionExecutor` 也是可替换接口。当前 `ActionRunnerExecutor` 根据距离/角度和速度计算
动作时长，每次动作后调用 `action_runner stop`。若已有 runner 的 CLI 或控制方式不同，
新增另一个 `ActionExecutor`，不要改变 HTTP protocol。

任何网络超时、非 JSON/非法 action、顺序错误、相机异常或动作异常都会触发机器人本地
best-effort `StopMove`。真机仍需保留遥控器急停，并先在空旷平地低速验证距离、转角符号
和停止行为。

## 无硬件测试

```bash
python3 -m compileall -q http_framework
python3 -m unittest discover -s http_framework/tests -v
```

测试只使用 fake backend/session/camera/executor，不加载 checkpoint，也不访问相机或机器人。
