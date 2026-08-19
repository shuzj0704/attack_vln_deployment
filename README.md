# attack_vln_deployment

Unitree Go2-W 的自定义 VLN 真机部署与基础运动控制项目。

本仓库提供两套互不覆盖的通信方案：

- `http_framework/`：模型无关的 VLN HTTP 部署框架，预留自定义 backend 接口。
- `tcp_framework/`：原有 TCP 视频与手动控制方案，保留作为回退。

本文记录本项目当前可直接使用的网络参数、部署命令和已知问题。Go2-W 的通用网络配置、
SDK2 开发和运动控制知识见
[`docs/Go2W_Development_Guide.md`](./docs/Go2W_Development_Guide.md)。

## 1. 连接开发板

Go2-W 算力板网络信息：

| 项目    | 值                  | 说明                                    |
| ------- | ------------------- | --------------------------------------- |
| 无线 IP | `192.168.1.50`    | 当前通过 Wi-Fi `HCAI_5G` 连接的静态 IP |
| 有线 IP | `192.168.123.18`  | 机身尾部网线直连时使用，拔线后不可达    |
| 用户名  | `unitree`         |                                         |
| 密码    | `<set-on-device>` | 不要提交真实密码                        |

> 当前推荐使用无线 IP `192.168.1.50`，无需插网线。该地址于 2026-08-19
> 从 `192.168.1.200` 迁移，以避开已确认的 IPv4 地址冲突。建议在路由器中为
> Unitree 无线 MAC `4c:b7:e0:e7:3e:5d` 保留 `.50`。只有需要稳定有线调试时
> 才接网线使用 `192.168.123.18`。

```bash
# 测试连通性（无线）
ping 192.168.1.50

# SSH 登录（无线）
ssh unitree@192.168.1.50
# 输入设备当前配置的密码
```

## 2. 自定义 VLN HTTP 部署

本仓库内容已通过 rsync 部署到 Unitree Go2-W：

```text
unitree@192.168.1.50:/home/unitree/workspace/hkd/attack_vln_deployment
```

其中 `http_framework/` 与 `tcp_framework/` 均已同步。

实际部署分工如下：

| 位置          | 运行程序                                   | 负责内容                                               |
| ------------- | ------------------------------------------ | ------------------------------------------------------ |
| GPU 服务器    | `python3 -m http_framework.server`       | 加载自定义 VLN、维护 model memory、返回一个离散 action |
| Unitree Go2-W | `python3 -m http_framework.robot.client` | D435i 拍照、HTTP 请求、执行动作和本地 StopMove         |

如果当前仓库就在 GPU 服务器上，server 可直接从当前目录运行。Unitree 侧不需要把
VLN checkpoint 复制到机器人。若后续需要重新同步整个仓库，可执行：

```bash
# 在本地 attack_vln_deployment 根目录执行
rsync -av --exclude '__pycache__' \
  . \
  unitree@192.168.1.50:/home/unitree/workspace/hkd/attack_vln_deployment/
```

然后分别启动：

```bash
# GPU 服务器：在 attack_vln_deployment 根目录
python3 -m http_framework.server \
  --backend-factory <python_module>:<factory_function> \
  --host 0.0.0.0 \
  --port 5801

# Unitree：/home/unitree/workspace/hkd/attack_vln_deployment
python3 -m http_framework.robot.client \
  --server-url http://<gpu_server_host>:5801 \
  --instruction "<navigation_instruction>" \
  --action-runner /home/unitree/unitree_sdk2/build/bin/action_runner
```

完整协议、环境依赖、环境变量和安全说明见
[`http_framework/README.md`](./http_framework/README.md)。首次运行前必须确认
`action_runner stop` 会在机器人本地调用 Unitree SDK2 `StopMove`。

默认接口模板位于 `http_framework/vln_backend.py`。需要实现 `reset()`、`step()`、
`close()`，或通过 `--backend-factory` 指向你自己的适配模块；仓库不包含具体 VLN
模型、checkpoint 加载逻辑或推理实现。

## 3. TCP 无线控制框架测试

本仓库包含一个基于 TCP 的远程控制框架（`tcp_framework/`），主机通过 Wi-Fi 向机器狗发送控制命令并接收视频流。

### 3.1 启动机器狗端服务

在机器狗上确认 server 已启动：

```bash
ssh unitree@192.168.1.50
ss -tlnp | grep -E '5000|6000'
```

应看到 `0.0.0.0:5000`（视频）和 `0.0.0.0:6000`（命令）处于 `LISTEN` 状态。若未启动，执行：

```bash
cd /home/unitree/workspace/hkd/attack_vln_deployment/tcp_framework
nohup python3 robot/robot_main.py > /tmp/tcp_framework.log 2>&1 &
```

### 3.2 主机端测试步骤

在主机上逐条执行：

```bash
# 1. 网络连通性
ping 192.168.1.50

# 2. SSH 登录
ssh unitree@192.168.1.50

# 3. TCP 端口测试
nc -zv 192.168.1.50 5000
nc -zv 192.168.1.50 6000

# 4. 发送单次控制命令
cd /home/shu22/navigation/indoor_vln/attack_vln_deployment/tcp_framework
python3 host/cmd_client.py forward
python3 host/cmd_client.py left
python3 host/cmd_client.py right
python3 host/cmd_client.py stop

# 5. 键盘实时控制
python3 host/host_main.py

# 6. 视频流测试（可选）
python3 host/video_client.py
```

### 3.3 通过标准

| 测试项              | 通过标志                 |
| ------------------- | ------------------------ |
| ping                | 0% packet loss           |
| SSH                 | 登录成功                 |
| nc 5000/6000        | `succeeded!`           |
| `cmd_client.py`   | 收到`OK:` 响应         |
| `host_main.py`    | 按键无报错，命令发送成功 |
| `video_client.py` | 能显示画面或持续收到数据 |

> 当前 `cmd_client.py` 返回 `moved 0m` 表示**通信链路已通**，但机器狗尚未真正移动。这是 Unitree SDK2 运动控制层的问题，需进一步排查（如确认机器狗已站立、电机已解锁、遥控器未接管等）。

## 4. 开发板系统

实测为 **NVIDIA Jetson Orin NX (16GB)**：

- OS：Ubuntu 20.04.5 LTS
- 内核：Linux 5.10.104-tegra
- 架构：arm64
- JetPack：5.1.1 / L4T 35.3.1
- CUDA：11.4

## 5. 基础运动控制示例

```cpp
#include <unitree/robot/go2/sport/sport_client.hpp>
#include <unistd.h>

int main(int argc, char **argv)
{
  unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);

  unitree::robot::go2::SportClient client;
  client.SetTimeout(10.0f);
  client.Init();

  client.BalanceStand();      // 平衡站立
  sleep(2);
  client.Move(0.5f, 0, 0);    // 前进 0.5 m/s
  sleep(3);
  client.StopMove();          // 停止

  return 0;
}
```

运行：

```bash
./basic_motion eth0
```

## 6. 常用指令速查

| 指令                      | 说明           |
| ------------------------- | -------------- |
| `BalanceStand()`        | 平衡站立       |
| `Move(vx, vy, vyaw)`    | 速度控制       |
| `StopMove()`            | 停止当前运动   |
| `Damp()`                | 急停/阻尼      |
| `Sit() / RiseSit()`     | 坐下 / 站起    |
| `SwitchGait(d)`         | 切换步态       |
| `SwitchJoystick(false)` | 关闭遥控器响应 |

## 7. 注意事项

- 首次测试请使用网线直连，在开阔平地操作。
- 随时准备好遥控器急停，或程序中保留 `Damp()` 调用路径。
- 低电量时机器狗会进入保护状态，请先充电。

---

更详细的网络配置、SDK 安装、状态订阅、故障排查等内容，请参考 **[`docs/Go2W_Development_Guide.md`](./docs/Go2W_Development_Guide.md)**。
