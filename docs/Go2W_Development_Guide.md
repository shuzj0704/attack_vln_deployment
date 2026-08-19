# Unitree Go2-W 远程开发与基础运动控制指南

> 本 README 主要面向需要在本地 PC 上远程连接 Go2-W 机器狗开发板、并基于 Unitree 官方 SDK 进行基础运动控制开发的场景。内容整理自 [Unitree 官方文档](https://support.unitree.com/home/zh/Go2-W_developer/Basic_motion_control) 及社区常见实践。

---

## 目录

1. [硬件与网络准备](#1-硬件与网络准备)
2. [远程连接开发板](#2-远程连接开发板)
3. [开发环境准备](#3-开发环境准备)
4. [基础运动控制](#4-基础运动控制)
5. [常见问题](#5-常见问题)
6. [安全注意事项](#6-安全注意事项)
7. [附录：开发板实测硬件信息](#7-附录开发板实测硬件信息)

---

## 1. 硬件与网络准备

### 1.1 网络信息

Go2-W 开发板（算力板 / 拓展坞）当前实际网络配置如下：

| 项目            | 值                                                     | 说明                                 |
| --------------- | ------------------------------------------------------ | ------------------------------------ |
| 无线 IP         | `192.168.1.200`                                      | 连接 Wi-Fi`HCAI_5G` 后的静态 IP    |
| 有线 IP         | `192.168.123.18`                                     | 机身尾部网线直连时使用，拔线后不可达 |
| 用户名          | `unitree`                                            |                                      |
| 密码            | `<set-on-device>`                                    | 不要提交真实密码                     |
| 无线连接方式    | 通过 USB Wi-Fi 网卡连接路由器                          | 当前推荐方式，无需网线               |
| 有线连接方式    | 机身尾部网口（RJ45）                                   | 稳定性最好，适合首次调试             |
| 推荐 PC 静态 IP | `192.168.123.x`（x ≠ 18，例如 `192.168.123.222`） | 仅在有线直连时使用                   |
| 子网掩码        | `255.255.255.0`                                      |                                      |
| 网关            | `192.168.1.1`（无线）/ `192.168.123.1`（有线）     |                                      |

> 提示：当前仓库已默认使用无线 IP `192.168.1.200`。如果接回网线，请把相关命令中的 IP 替换为 `192.168.123.18`。

### 1.2 PC 端设置静态 IP

以 Ubuntu 为例：

```bash
# 查看本机网口名称（通常为 eth0 / enp* / usb* 等）
ip link show

# 临时设置静态 IP（以网口 eth0 为例，重启后失效）
sudo ip addr flush dev eth0
sudo ip addr add 192.168.123.222/24 dev eth0
sudo ip link set eth0 up
```

如需永久生效，可在 **网络设置 → 有线连接 → IPv4 → 手动** 中配置：

- 地址：`192.168.123.222`
- 子网掩码：`255.255.255.0`
- 网关：`192.168.123.1`

### 1.3 验证连通性

```bash
# 无线连接时
ping 192.168.1.200

# 有线连接时
ping 192.168.123.18
```

收到回复说明网络层已连通，可以进行 SSH 登录。

---

## 2. 远程连接开发板

### 2.1 SSH 登录

当前使用无线 IP：

```bash
ssh unitree@192.168.1.200
# 输入设备当前配置的密码
```

如果接回网线，则使用：

```bash
ssh unitree@192.168.123.18
```

登录成功后，即可在开发板上执行命令、查看日志、编译运行程序。

### 2.2 使用 VS Code 远程开发（推荐）

1. 在本地 PC 的 VS Code 中安装扩展 **Remote - SSH**。
2. 按 `Ctrl+Shift+P` → 选择 **Remote-SSH: Connect to Host...**
3. 输入 `unitree@192.168.1.200` 并连接（有线时使用 `unitree@192.168.123.18`）。
4. 输入设备当前配置的密码，即可像本地一样浏览、编辑和调试开发板上的代码。

### 2.3 文件传输

```bash
# 从 PC 上传文件/目录到开发板（无线）
scp -r ./your_project unitree@192.168.1.200:/home/unitree/

# 从开发板下载文件到 PC（无线）
scp -r unitree@192.168.1.200:/home/unitree/your_project ./
```

如果接回网线，将 `192.168.1.200` 替换为 `192.168.123.18`。

### 2.4 远程桌面（可选）

部分固件预装了 NoMachine 等远程桌面服务。开发板上执行：

```bash
bash ~/nomachine.sh
```

然后在 PC 端安装 [NoMachine 客户端](https://www.nomachine.com/)，通过 IP `192.168.1.200` 连接即可（有线时用 `192.168.123.18`）。

---

## 3. 开发环境准备

### 3.1 官方 SDK 与 ROS2（如需要）

Go2-W 二次开发通常依赖 Unitree SDK2 与 CycloneDDS。基础依赖安装示例（以 Ubuntu 20.04 为例）：

```bash
sudo apt-get update
sudo apt-get install -y \
  cmake g++ build-essential \
  libyaml-cpp-dev libeigen3-dev \
  libboost-all-dev libspdlog-dev libfmt-dev
```

克隆官方仓库（可在 PC 或开发板上进行）：

```bash
# Unitree SDK2
git clone https://github.com/unitreerobotics/unitree_sdk2.git

# ROS2 桥接（可选）
git clone https://github.com/unitreerobotics/unitree_ros2.git
```

> 由于 Go2-W 开发板为 ARM 架构，建议在开发板上直接编译运行，避免交叉编译的复杂度。

### 3.2 网络接口说明

使用 SDK 控制机器人时，程序需要绑定与机器人通信的网卡。常见命名：

- 开发板上：`eth0`
- PC 有线网卡：`enp2s0`、`enp3s0` 等

运行示例程序时通常以网卡名作为参数传入：

```bash
./your_program eth0
```

---

## 4. 基础运动控制

### 4.1 核心概念

Go2-W 的高层运动服务分为两部分：

- **高层控制接口**：通过 `SportClient` 发送速度、姿态、步态切换等运动指令。
- **高层状态接口**：通过订阅 `rt/sportmodestate` 话题获取位置、速度、姿态等状态。

### 4.2 基础控制代码示例

以下示例展示了如何初始化 `SportClient` 并发送简单运动指令：

```cpp
#include <unitree/robot/go2/sport/sport_client.hpp>
#include <unistd.h>

int main(int argc, char **argv)
{
  if (argc < 2)
  {
    std::cout << "Usage: " << argv[0] << " networkInterface" << std::endl;
    exit(-1);
  }

  // 初始化通信通道，argv[1] 为机器人连接的网卡名称
  unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);

  // 创建 SportClient
  unitree::robot::go2::SportClient sport_client;
  sport_client.SetTimeout(10.0f);
  sport_client.Init();

  // 坐下
  sport_client.Sit();
  sleep(3);

  // 站起
  sport_client.RiseSit();
  sleep(3);

  // 平衡站立
  sport_client.BalanceStand();
  sleep(2);

  // 前进：vx=0.5 m/s, vy=0, vyaw=0
  sport_client.Move(0.5f, 0.0f, 0.0f);
  sleep(3);

  // 停止移动
  sport_client.StopMove();

  return 0;
}
```

编译运行：

```bash
g++ -o basic_motion basic_motion.cpp \
  -I/path/to/unitree_sdk2/include \
  -L/path/to/unitree_sdk2/lib -lunitree_sdk2

# 在开发板上运行，eth0 为与机器人通信的网口
./basic_motion eth0
```

### 4.3 常用控制接口

| 接口                        | 功能                                                             |
| --------------------------- | ---------------------------------------------------------------- |
| `Damp()`                  | 阻尼/急停，所有电机停止并进入阻尼状态                            |
| `BalanceStand()`          | 解除锁定，进入平衡站立模式                                       |
| `StandUp()`               | 正常站高，关节锁定                                               |
| `StandDown()`             | 趴下，关节锁定                                                   |
| `RecoveryStand()`         | 从翻倒/趴下恢复站立                                              |
| `Move(vx, vy, vyaw)`      | 速度控制（机体坐标系）                                           |
| `MoveToPos(x, y, yaw)`    | 移动到里程计坐标系指定位置                                       |
| `Euler(roll, pitch, yaw)` | 设置机体姿态角                                                   |
| `BodyHeight(height)`      | 调节机身相对高度                                                 |
| `FootRaiseHeight(height)` | 调节抬腿高度                                                     |
| `SwitchGait(d)`           | 切换步态：0 idle, 1 trot, 2 trot running, 3 正向爬楼, 4 逆向爬楼 |
| `SpeedLevel(level)`       | 速度档位：-1 慢速, 0 正常, 1 快速                                |
| `SwitchJoystick(flag)`    | 是否响应原生遥控器                                               |
| `Sit() / RiseSit()`       | 坐下 / 从坐下站起                                                |
| `Hello() / Stretch()`     | 打招呼 / 伸懒腰                                                  |

### 4.4 获取运动状态

订阅 `rt/sportmodestate` 话题可获取机器人实时状态：

```cpp
#include <unitree/idl/go2/SportModeState_.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

#define TOPIC_HIGHSTATE "rt/sportmodestate"

void HighStateHandler(const void* message)
{
  auto state = *(unitree_go::msg::dds_::SportModeState_*)message;

  std::cout << "position: "
            << state.position()[0] << ", "
            << state.position()[1] << ", "
            << state.position()[2] << std::endl;

  std::cout << "quaternion: "
            << state.imu_state().quaternion()[0] << ", "
            << state.imu_state().quaternion()[1] << ", "
            << state.imu_state().quaternion()[2] << ", "
            << state.imu_state().quaternion()[3] << std::endl;
}

int main()
{
  std::string networkInterface = "eth0";
  unitree::robot::ChannelFactory::Instance()->Init(0, networkInterface);

  unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>
      suber(TOPIC_HIGHSTATE);
  suber.InitChannel(HighStateHandler);

  while (1)
  {
    usleep(20000);
  }

  return 0;
}
```

---

## 5. 常见问题

### Q1：SSH 连接失败或提示连接超时

- 确认当前使用正确的 IP：无线用 `192.168.1.200`，有线用 `192.168.123.18`。
- 无线连接时，确认机器狗和 PC 连入同一个 Wi-Fi（如 `HCAI_5G`）。
- 有线连接时，确认网线已插紧，PC 已设置为 `192.168.123.x/24` 网段（x ≠ 18）。
- 尝试关闭 PC 防火墙或允许该网段通信。
- 检查机器狗是否已正常开机并进入可开发状态。

### Q2：能 ping 通但 SSH 提示密码错误

- 使用设备当前配置的密码，注意大小写；不要在仓库中保存真实密码。
- 若密码被修改过，请联系设备管理员或重置开发板。

### Q3：SDK 程序运行后机器狗没有反应

- 确认传入的网卡名称正确（开发板上通常为 `eth0`）。
- 确认机器狗已完成初始化并处于可控制状态（非急停、非低电量保护）。
- 检查遥控器是否接管，可调用 `SwitchJoystick(false)` 关闭遥控器响应。

### Q4：运动指令异常或动作中断

- 特殊动作（如 Sit、Hello、Stretch 等）需要在上一个动作执行完毕后再执行。
- 出现 `4201` 错误码表示动作超时；`3104` 表示 DDS 超时，需检查网络或通信通道。

---

## 6. 安全注意事项

1. **首次测试请务必使用网线直连**，在开阔、平坦、无障碍的地面进行。
2. **随时准备急停**：确保遥控器在旁，或程序中保留 `Damp()` 调用路径。
3. **低电量勿强控**：电量过低时机器狗可能自动进入保护状态，此时应充电后再测试。
4. **循序渐进**：先测试 `BalanceStand()`、`Move()` 等基础指令，再尝试特殊动作。
5. **人身与设备安全**：空翻、跳跃、前扑等高风险动作请在专业人员指导下进行，并确保周围无人无贵重设备。

---

## 7. 附录：开发板实测硬件信息

通过 SSH 登录开发板后，使用以下命令可查看本机硬件与系统详情：

```bash
# 查看 Ubuntu 版本
lsb_release -a

# 查看设备型号与 JetPack 信息
jetson_release

# 查看 CPU 信息
lscpu

# 查看内存
free -h

# 查看内核版本
uname -a
```

某台 Go2-W 实测结果如下（仅供参考，不同批次可能存在差异）：

| 项目     | 实测值                           |
| -------- | -------------------------------- |
| 设备型号 | `NVIDIA Orin NX Developer Kit` |
| 算力模块 | NVIDIA Jetson Orin NX (16GB RAM) |
| P-Number | p3767-0000                       |
| 操作系统 | Ubuntu 20.04.5 LTS (focal)       |
| 内核     | Linux 5.10.104-tegra             |
| 架构     | arm64 / aarch64                  |
| CPU      | 8 核 ARM Cortex-A78AE            |
| 内存     | 16 GB                            |
| JetPack  | 5.1.1                            |
| L4T      | 35.3.1                           |
| CUDA     | 11.4.315                         |
| cuDNN    | 8.6.0.166                        |
| TensorRT | 8.5.2.2                          |
| VPI      | 2.2.7                            |
| Vulkan   | 1.3.204                          |
| OpenCV   | 4.5.4（未启用 CUDA）             |
| 电源模式 | MAXN                             |

> 说明：Go2-W 高配版本通常搭载 **Jetson Orin NX 16GB** 算力板，性能优于 Xavier NX，足以在本地运行推理、SLAM 和强化学习运控等任务。

---

## 参考链接

- [Unitree Go2-W 开发者文档](https://support.unitree.com/home/zh/Go2-W_developer/Basic_motion_control)
- [Unitree 高层运动服务接口文档](https://support.unitree.com/home/zh/developer/sports_services)
- [Unitree SDK2 GitHub](https://github.com/unitreerobotics/unitree_sdk2)
- [Unitree ROS2 GitHub](https://github.com/unitreerobotics/unitree_ros2)
