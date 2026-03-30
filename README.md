# yokuli_ble_notifier

通过蓝牙低功耗（BLE）读取船载电池监控仪和太阳能控制器的数据，并以 Signal K delta 格式通过 TCP 推送到 Signal K 服务器。

## 支持设备

| 设备类型 | 品牌/型号 | 配置 key |
|---------|---------|---------|
| 库仑计（电池监控仪） | Junctek KH-F 系列 | `coulometer` |
| MPPT 太阳能控制器 | Renogy MPPT | `mppt` |

## 工作原理

```
BLE 设备 ──蓝牙──▶ 树莓派（本程序）──TCP──▶ Signal K Server
  Junctek                port 9999 ──────────▶ electrical.batteries.house.*
  Renogy MPPT            port 9998 ──────────▶ electrical.solar.house.*

                         port 8080 ──────────▶ 状态监控网页
```

- 每个 BLE 设备独立运行一个 TCP 服务器，Signal K Server 主动连入
- 收到 BLE 通知后，将解析结果打包成 Signal K delta JSON（换行分隔）发送给所有连接的客户端
- 断线自动重连，支持 watchdog 超时检测
- 内置状态监控网页，可远程查看连接状态并执行控制操作

## 状态监控网页

启动后，在浏览器访问：

```
http://<树莓派IP>:8080
```

网页每 2 秒自动刷新，显示每个 BLE 设备的连接状态、最后数据时间、连接失败次数、Signal K 客户端数，以及蓝牙适配器配置。

顶部 header 显示当前运行的 **git commit hash**，点击可跳转到 GitHub 对应 commit 页面。拉取更新后 hash 自动刷新，方便确认版本一致性。

### 版本管理

| 按钮 | 作用 |
|-----|-----|
| **只拉取更新** | `git pull`，不重启进程；hash 更新后可看到新代码已到位 |
| **拉取并重启** | `git pull` + 重启 systemd 服务，一步完成更新 |
| **安装 / 升级 Signal K Web App** | 将 webapp 文件写入 Signal K 数据目录并重启容器（已安装时按钮显示"升级"） |
| **删除 Web App** | 提示前往 Signal K 管理界面删除 |

### 进程控制

| 按钮 | 作用 | 是否需要确认 |
|-----|-----|------|
| **断连重连**（每个设备） | 主动断开该设备，触发自动重连流程 | 无需 |
| **重启蓝牙适配器** | 完整清理 BlueZ 状态并重启适配器，约 10 秒后设备自动重连 | 需确认 |
| **重启进程** | 重启 `yokuli-ble-notifier` systemd 服务 | 需确认 |
| **重启树莓派** | 完全重启系统，所有服务启动后自动恢复 | 二次确认 |

### 日志面板

- **运行日志**：保留最近 180 条，滚动查看
- **原始报文**（勾选 DEBUG）：显示 BLE 原始帧（十六进制）
- **复制最近100条**：两个面板各有复制按钮，一键复制到剪贴板

## Signal K Web App

本程序可作为 Signal K Webapp 安装，使其出现在 Signal K 的应用列表中。

### 前提

Signal K 需运行在 Docker，且数据目录已挂载：

```yaml
# docker-compose.yml
volumes:
  - ./signalk-data:/home/node/.signalk
```

### 安装

在状态监控网页的**版本管理**区域点击 **安装 Signal K Web App**：

1. 将 `package.json` 和 `index.html` 写入：
   ```
   ~/signalk-server/signalk-data/node_modules/yokuli-ble-monitor/
   ```
2. 执行 `docker restart signalk` 使 Signal K 识别新 Webapp

安装完成后，Signal K 应用列表会出现 **yokuli-ble-monitor**，点击即跳转至 `http://<树莓派IP>:8080`。

> **关于浏览器 Private Network Access 限制**：Webapp 使用 `window.location.replace()` 做顶级导航跳转，绕过浏览器对跨源 subresource（iframe/fetch）的私有网络访问拦截。跳转后页面顶部会出现 **← 返回 Signal K 面板** 按钮。

### 升级

再次点击 **升级 Signal K Web App**（已安装时按钮自动切换文字），文件会被覆盖写入并重启容器。

### 删除

点击**删除 Web App** 按钮查看提示，然后前往 Signal K 管理界面（Appstore / Webapps）删除，或手动删除目录：

```bash
rm -rf ~/signalk-server/signalk-data/node_modules/yokuli-ble-monitor
docker restart signalk
```

## 发布的 Signal K 路径

### Junctek 库仑计（source: `pi-py-ble-junctek`）

> 库仑计是电池状态的权威来源，负责所有 `electrical.batteries.house.*` 路径

| Signal K 路径 | 单位 | 说明 |
|---|---|---|
| `electrical.batteries.house.voltage` | V | 电池电压 |
| `electrical.batteries.house.current` | A | 充放电电流（充电为正，放电为负） |
| `electrical.batteries.house.power` | W | 功率（充电为正，放电为负） |
| `electrical.batteries.house.capacity.remaining` | J | 剩余容量（焦耳） |
| `electrical.batteries.house.capacity.stateOfCharge` | 0–1 | 荷电状态 |

**协议说明**：Junctek 充电帧（`0xC0`）与放电帧（`0xC1`）的字段顺序不同：
- 放电帧：`BB [电流×100] C1 [功率×100] D8 … EE`
- 充电帧：`BB [电压×100] C0 [功率×100] D8 … EE`

程序根据方向字节自动区分，充电电流计算为 `功率 / 电压`（正值）。

### Renogy MPPT（source: `pi-py-ble-renogy`）

> MPPT 只发太阳能和充电相关路径，不覆盖库仑计的电池电压/SOC

| Signal K 路径 | 单位 | 说明 |
|---|---|---|
| `electrical.batteries.house.current` | A | 充电电流 |
| `electrical.batteries.house.temperature` | K | 电池温度（开尔文） |
| `electrical.solar.house.voltage` | V | 光伏板电压 |
| `electrical.solar.house.current` | A | 光伏板电流 |
| `electrical.solar.house.panelPower` | W | 光伏板功率 |

## 依赖安装

```bash
pip3 install bleak pyyaml
```

BLE 访问权限（不需要每次 sudo）：

```bash
sudo usermod -aG bluetooth $USER
# 重新登录后生效
```

## 配置文件（config.yaml）

```yaml
app:
  vessel_id: "vessels.self"   # Signal K context，通常保持默认
  log_level: "INFO"
  enable_debug_log: false      # true 时打印所有原始 BLE 包（hex），用于调试
  status_port: 8080            # 状态网页端口

bluetooth:
  enable_adapter_restart: true    # 连续失败达到阈值后自动重启蓝牙适配器
  restart_cooldown_seconds: 60    # 两次自动重启之间的最短间隔（秒）

devices:
  coulometer:
    enabled: true
    name: "Junctek"
    source_label: "pi-py-ble-junctek"
    mac: "3C:AB:72:25:E6:68"
    tcp_port: 9999
    notify_uuids:
      - "0000fff1-0000-1000-8000-00805f9b34fb"
    write_uuid: null
    battery_capacity_ah: 320.0           # 电池总容量（Ah），用于计算 SOC
    watchdog_timeout_seconds: 20
    reconnect_delay_seconds: 3           # 较短延迟，优先快速重连
    max_fail_before_restart: 3
    adapter_restart_on_fail: true

  mppt:
    enabled: true
    name: "Renogy_MPPT"
    source_label: "pi-py-ble-renogy"
    mac: "DC:0D:30:0F:2A:81"
    tcp_port: 9998
    notify_uuids:
      - "0000fff1-0000-1000-8000-00805f9b34fb"
    write_uuid: "0000ffd1-0000-1000-8000-00805f9b34fb"
    watchdog_timeout_seconds: 30
    reconnect_delay_seconds: 5           # 稍长延迟，避免与库仑计争抢蓝牙射频
    max_fail_before_restart: 5
    adapter_restart_on_fail: false       # MPPT 失败不触发全局重启（不干扰库仑计）
    poll_interval_seconds: 8
    commands:
      unlock: "0103000c0001"
      read_all: "01030100000f"
```

### 蓝牙射频共存说明

树莓派只有一个蓝牙射频，两个设备同时连接时 MPPT 的 `HCI_LE_Create_Connection` 会短暂中断库仑计的通信（约 2 秒）。已针对此硬件限制调整重连延迟：库仑计使用较短延迟快速重连，MPPT 使用稍长延迟避免在库仑计建链期间发起连接。这是硬件层面的限制，无法在软件上完全消除。

### 蓝牙重启流程

1. 通知所有设备断开（Python 层 `BleakClient.disconnect()`）
2. `bluetoothctl disconnect <MAC>` —— 系统级断开每个设备
3. `bluetoothctl remove <MAC>` —— 清除 BlueZ 设备缓存（删除 `/var/lib/bluetooth/` 里的配对文件）
4. `sudo systemctl restart bluetooth` —— 重启整个 bluetoothd 进程，清空所有内存状态
5. 等 2 秒让 bluetoothd 完全启动
6. `bluetoothctl power on` —— 确保适配器上电
7. 等待 4 秒适配器重新枚举，设备自动重连

`restart_cooldown_seconds` 控制自动重启的最短间隔，防止连续触发。网页"重启蓝牙适配器"按钮不受 `enable_adapter_restart` 约束，随时可用，但会同步冷却计时。

### 各设备触发重启策略

| 设备 | `adapter_restart_on_fail` | `max_fail_before_restart` | 说明 |
|-----|--------------------------|--------------------------|------|
| coulometer (Junctek) | `true` | 3 | 连续失败 3 次触发全局重启 |
| mppt (Renogy) | `false` | 5 | 失败不触发重启，只记录次数 |

## 手动运行

```bash
git clone https://github.com/ohkuku/yokuli_ble_notifier.git
cd yokuli_ble_notifier
pip3 install bleak pyyaml
python3 main.py
```

## 开机自启（systemd）

使用 `auto_launch` 脚本管理 systemd 服务：

```bash
# 首次安装（生成服务文件 + 启用自启 + 立即启动）
./auto_launch install

# 查看运行状态
./auto_launch status

# 实时查看日志
./auto_launch log

# 启动 / 停止 / 重启
./auto_launch start
./auto_launch stop
./auto_launch restart

# git pull 之后更新并重启
git pull
./auto_launch update

# 彻底卸载自启
./auto_launch uninstall
```

> `install` 会自动检测当前目录和 Python 路径，生成对应的 systemd 服务文件并写入 `/etc/systemd/system/`。

### 赋予重启权限

蓝牙重启、进程重启、系统重启均需要 sudo 权限，一次性配置免密规则：

```bash
sudo visudo -f /etc/sudoers.d/yokuli-ble
```

写入以下内容（将 `pi` 替换为实际用户名）：

```
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bluetooth
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart yokuli-ble-notifier
pi ALL=(ALL) NOPASSWD: /sbin/reboot
```

若使用 Signal K Web App 安装功能（`docker restart signalk`），还需要：

```
pi ALL=(ALL) NOPASSWD: /usr/bin/docker restart signalk
```

## Signal K Server 配置

在 Signal K 管理界面中添加两个 **TCP** 数据源：

1. 进入 **Server → Connections → Add**
2. 选择类型 **TCP**，填写：
   - Junctek 库仑计：主机 `localhost`，端口 `9999`
   - Renogy MPPT：主机 `localhost`，端口 `9998`
3. 保存后 Signal K 会主动连接本程序并接收数据

## 调试 BLE 包

遇到数据解析异常时，在 `config.yaml` 中开启：

```yaml
app:
  enable_debug_log: true
```

重启后终端（或 `./auto_launch log`）会打印每一个原始 BLE 通知包：

```
[coulometer] [DEBUG:f9b34fb] bb00041800c154d800ee
[coulometer] Parsed: {'current_a': -4.18, 'voltage_v': 13.27, ...}
```

状态网页也可勾选 **显示原始报文（DEBUG）** 在浏览器中查看，并通过**复制最近100条**按钮导出。

> 调试完毕后记得改回 `false`，否则日志会非常嘈杂。

## 常见问题

**Q：启动时报 `Operation already in progress`**
两个设备同时发起 BLE 连接会触发此错误。程序已通过 `asyncio.Lock` 串行化连接，通常等一会儿会自动重试成功。

**Q：Junctek 连接后很快断开，无法重连**
BlueZ 保留了旧的连接缓存。程序在 Junctek 连续失败 3 次后会自动执行完整的蓝牙重启（包括 `bluetoothctl remove` 清除缓存）。也可以直接点击网页"重启蓝牙适配器"按钮手动触发。

**Q：MPPT 连接时库仑计断开**
属于硬件限制（单射频），见[蓝牙射频共存说明](#蓝牙射频共存说明)。已通过调整 `reconnect_delay_seconds` 缓解。

**Q：库仑计充电时显示负电流**
确认使用的是最新版本代码。旧版 parser 未区分充电帧（`0xC0`）和放电帧（`0xC1`）的字段顺序，已在当前版本修复。

**Q：容量显示异常（远超电池实际容量）**
确认 `battery_capacity_ah` 配置正确。程序会过滤超过容量 110% 的异常帧。若仍异常，开启 debug 日志查看原始帧。

**Q：网页显示"无法连接"**
程序可能已停止。登录树莓派运行：
```bash
./auto_launch status
./auto_launch log
```

**Q：端口被占用（`Address already in use`）**
有残留进程占用了 TCP 端口，运行：
```bash
pkill -f "python.*main.py"
# 或
./auto_launch restart
```

**Q：Ctrl+C 后进程卡住不退出**
`disconnect()` 有 5 秒超时保护，最多等待 5 秒后强制退出。

**Q：SOC 数值不准**
Junctek 的 SOC 基于 `battery_capacity_ah` 配置计算，请确认该值与实际电池容量匹配。
