# yokuli_ble_notifier

通过蓝牙低功耗（BLE）读取船载电池监控仪和太阳能控制器的数据，并以 Signal K delta 格式通过 TCP 推送到 Signal K 服务器。

## 支持设备

| 设备类型 | 品牌/型号 | 配置 key |
|---------|---------|---------|
| 库仑计（电池监控仪） | Junctek KH-F 系列 | `coulometer` |
| MPPT 太阳能控制器 | Renogy MPPT | `mppt` |

## 工作原理

```
BLE 设备 ──蓝牙──▶ 树莓派 (本程序) ──TCP──▶ Signal K Server
  Junctek                port 9999 ─────────────────▶ electrical.batteries.house.*
  Renogy MPPT            port 9998 ─────────────────▶ electrical.batteries.house.*
                                                       electrical.solar.house.*
```

- 每个 BLE 设备独立运行一个 TCP 服务器，Signal K Server 主动连入
- 收到 BLE 通知后，将解析结果打包成 Signal K delta JSON（换行分隔）发送给所有连接的客户端
- 断线自动重连，支持 watchdog 超时检测

## 发布的 Signal K 路径

### Junctek 库仑计（source: `pi-py-ble-junctek`）

| Signal K 路径 | 单位 | 说明 |
|---|---|---|
| `electrical.batteries.house.voltage` | V | 电池电压 |
| `electrical.batteries.house.current` | A | 充放电电流（放电为负） |
| `electrical.batteries.house.power` | W | 功率（放电为负） |
| `electrical.batteries.house.capacity.remaining` | J | 剩余容量（焦耳） |
| `electrical.batteries.house.capacity.stateOfCharge` | 0–1 | 荷电状态 |

### Renogy MPPT（source: `pi-py-ble-renogy`）

| Signal K 路径 | 单位 | 说明 |
|---|---|---|
| `electrical.batteries.house.voltage` | V | 电池电压 |
| `electrical.batteries.house.current` | A | 充电电流 |
| `electrical.batteries.house.capacity.stateOfCharge` | 0–1 | 荷电状态 |
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
  enable_debug_log: false      # 设为 true 时打印所有原始 BLE 包（hex），用于调试解析问题

bluetooth:
  enable_adapter_restart: false          # 多次失败后是否重启蓝牙适配器
  adapter_restart_command: "sudo systemctl restart bluetooth"
  restart_cooldown_seconds: 60

devices:
  coulometer:
    enabled: true
    name: "Junctek"
    source_label: "pi-py-ble-junctek"   # Signal K 中显示的来源名称
    mac: "3C:AB:72:25:E6:68"            # 设备蓝牙 MAC 地址
    tcp_port: 9999                       # 本机监听端口，Signal K 连此端口
    notify_uuids:
      - "0000fff1-0000-1000-8000-00805f9b34fb"
    write_uuid: null
    battery_capacity_ah: 320.0           # 电池总容量（Ah），用于计算 SOC
    watchdog_timeout_seconds: 20         # 超过此时间无数据则断线重连
    reconnect_delay_seconds: 7           # 重连等待时间
    max_fail_before_restart: 2           # 连续失败多少次后重启蓝牙适配器

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
    reconnect_delay_seconds: 13
    max_fail_before_restart: 2
    poll_interval_seconds: 8             # 每隔多少秒主动轮询一次（Modbus 设备需要）
    commands:
      unlock: "0103000c0001"
      read_all: "01030100000f"
```

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

## Signal K Server 配置

在 Signal K 管理界面中添加两个 **TCP** 数据源：

1. 进入 **Server → Plugin Config → Signal K to NMEA** 或 **Connections → Add**
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

重启后终端（或 `./auto_launch log`）会打印每一个原始 BLE 通知包，格式如下：

```
[coulometer] [DEBUG:f9b34fb] bb00041800c154d800ee
[coulometer] Parsed: {'current_a': -4.18, 'voltage_v': 13.27, ...}
```

> 调试完毕后记得改回 `false`，否则日志会非常嘈杂。

## 常见问题

**Q：启动时报 `Operation already in progress`**
两个设备同时发起 BLE 连接会触发此错误。程序已通过 `asyncio.Lock` 串行化连接，通常等一会儿会自动重试成功。

**Q：连接后很快断开，无法重连**
BlueZ 可能保留了旧的连接状态。尝试：
```bash
sudo systemctl restart bluetooth
```
程序的 `enable_adapter_restart` 配置也可以在多次失败后自动执行此操作。

**Q：Ctrl+C 后进程卡住不退出**
`disconnect()` 有 5 秒超时保护，最多等待 5 秒后强制退出。如果仍然卡住，可以 `kill` 进程后重启蓝牙服务。

**Q：SOC 数值不准**
Junctek 的 SOC 基于 `battery_capacity_ah` 配置计算，请确认该值与你的实际电池容量匹配。
