# 有线 ADB 调试自动检测与配置工具 — 技术设计文档

- 文档版本：v1.0
- 日期：2026-08-04
- 目标平台：Windows PC / macOS / Android（收银机）
- 关联文档：`有线ADB调试自动检测与配置工具-需求文档.md`

---

## 1. 总体架构

采用**单一入口 + 分层命令抽象 + 状态机诊断**的架构，跨平台能力通过"平台命令适配层"实现。

```
┌────────────────────────────────────────────────────────────┐
│                     CLI / TUI 入口                          │
│         (参数解析、交互提示、报告输出、退出码)                  │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│                     Orchestrator（编排器）                   │
│   按阶段调度：自检 → 拓扑 → 分层检测 → 配置 → 连接 → 验证     │
│   维护全局状态：selected_iface, default_route_iface,         │
│   register_ip, phase_results[]                              │
└───────────┬───────────────────────────┬────────────────────┘
            │                           │
┌───────────▼──────────┐   ┌────────────▼─────────────────────┐
│  检测器层 (Detectors) │   │  配置器层 (Configurators)          │
│  L1/L2/L3/L4/L7      │   │  PcConfigurator / AndroidConfig  │
│  每层返回 DetResult  │   │  遵守"默认路由保护"约束             │
└───────────┬──────────┘   └────────────┬─────────────────────┘
            │                           │
┌───────────▼───────────────────────────▼────────────────────┐
│              平台命令适配层 (PlatformAdapter)                 │
│   CommandRunner: 统一执行命令、解析输出、安全超时              │
│   WindowsAdapter / MacOSAdapter / AndroidAdapter            │
│   + 只读/写操作分离 + 提权提示                               │
└─────────────────────────────────────────────────────────────┘
```

### 1.1 模块职责
| 模块 | 职责 |
|---|---|
| Orchestrator | 阶段编排、状态机、回滚协调、报告汇总 |
| Detectors | 各 OSI 层检测，返回结构化结果（含证据：命令+输出） |
| Configurators | PC/Android 配置，含变更前备份与回滚 |
| PlatformAdapter | 平台差异封装：命令模板、管理员/提权、输出解析 |
| ADBClient | `adb` 封装：connect/devices/shell，状态机解析 |
| Reporter | 生成 Markdown/文本报告 |

---

## 2. 平台命令适配层设计

### 2.1 接口定义
```python
class PlatformAdapter(ABC):
    @abstractmethod
    def get_network_interfaces(self) -> list[NetIface]: ...
    def get_default_route_iface(self) -> str | None: ...
    def get_interface_state(self, name: str) -> IfaceState: ...      # link/media/ip/mask/gateway/arp
    def set_static_ip(self, iface: str, ip: str, mask: str) -> bool:  # 绝不设置默认网关
    def enable_iface(self, iface: str) -> bool: ...
    def get_arp_table(self) -> dict[str, str]: ...                   # ip -> mac
    def probe_port(self, host: str, port: int, timeout: float) -> bool: ...
```

### 2.2 macOS 实现要点（本次实测验证）
| 操作 | 命令 | 注意 |
|---|---|---|
| 枚举物理网卡 | `networksetup -listallhardwareports` + `ifconfig -l` | 过滤：Wi-Fi/en0 保留，虚拟接口(gif/stf/utun/bridge/anpi/ap)排除 |
| USB 网卡识别 | `ioreg -p IOUSB -l` grep `USB Product Name`="USB xxx LAN"；`system_profiler SPUSBDataType` grep VendorID 0x0bda(RTL)/0x0b95(ASIX) | 识别扩展坞网口芯片 |
| 接口链路状态 | `ifconfig enX` → `status:` / `media:` | `active`+`100baseTX` 表示 L1 通 |
| 默认路由 | `netstat -rn -f inet \| grep '^default'` | 取 `default` 行的 interface |
| 设静态 IP **不设网关** | `networksetup -setmanual "<服务名>" 192.168.100.1 255.255.255.0 0.0.0.0` | **网关 0.0.0.0 是关键**：不产生默认路由 |
| 启用服务 | `networksetup -setnetworkserviceenabled "<服务名>" on` | 先查 `-getnetworkserviceenabled` |
| ARP 表 | `arp -an` | 注意加 `-n` 避免 DNS 反解卡死 |
| 端口探测 | `nc -z -G 3 -w 3 IP 5555` | `-G` 连接超时 |

> **macOS 服务名陷阱**：`networksetup` 用"网络服务名"（如 `USB 10/100/1000 LAN`），不是接口名 `en11`。需用 `-listnetworkserviceorder` 建立 服务名↔设备名 映射。

> **macOS 优先级陷阱**：USB 网卡若排第一，配网关会抢默认路由导致断网。故：(a) 配置顺序上先 `networksetup -ordernetworkservices` 把 Wi-Fi 提到前面；(b) 网关必须 0.0.0.0；(c) 每次配置后回验默认路由。

> **macOS 活动接口（nwi）陷阱**：`scutil --nwi` 显示 `Network interfaces: en0` 时，en11 不被网络框架视为活动接口，三层流量不处理。给 en11 设置**有效网关**（指向自身网段内）后可纳入 nwi（`en0 en11`），但网关有效会产生默认路由，需配合优先级与回滚。**推荐流程：先降优先级再设网关，最后把网关改 0.0.0.0 观察是否仍在 nwi 中；若不在则需有效网关+优先级保证**。见 §6 安全配置策略。

### 2.3 Windows 实现要点
| 操作 | 命令 | 注意 |
|---|---|---|
| 枚举物理网卡 | PowerShell `Get-NetAdapter \| Where PhysicalMediaType -match Ethernet\|Wireless` | 过滤虚拟/VPN（`Virtual`、`vEthernet`、`WSL`） |
| USB 网卡识别 | `Get-NetAdapter` 的 InterfaceDescription 匹配 `Realtek\|ASIX\|RTL8\|USB.*Ethernet\|AX88` | |
| 链路状态 | `Get-NetAdapter` 的 `Status`（Up/Down）、`LinkSpeed` | |
| 默认路由 | `Get-NetRoute -DestinationPrefix '0.0.0.0/0'` 的 `ifIndex` | 映射到网卡 |
| 设静态 IP **不设网关** | `New-NetIPAddress -InterfaceIndex X -IPAddress 192.168.100.1 -PrefixLength 24`（**不加 -DefaultGateway**） | 或 `netsh interface ip set address name="X" static 192.168.100.1 255.255.255.0` |
| 清除已有网关 | `Remove-NetRoute -DestinationPrefix 0.0.0.0/0 -InterfaceIndex X -Confirm:$false` | 仅当该网卡存在错误网关时 |
| ARP 表 | `arp -a` / `Get-NetNeighbor` | |
| 端口探测 | `Test-NetConnection IP -Port 5555` | |

> **Windows 关键**：`New-NetIPAddress` 只要不加 `-DefaultGateway`，就不会产生默认路由；且 Windows 对"多网卡带网关"会按接口 metric 选主网关，配置工具应**确保调试网卡无默认网关**，避免破坏主网卡上网。

### 2.4 Android 端命令（通过 adb shell 执行，需 root 或已授权）
| 操作 | 命令 |
|---|---|
| 查所有接口 | `ip addr show` |
| 查接口状态 | `ip link show ethX` / `cat /sys/class/net/ethX/operstate` |
| 查物理载波 | `cat /sys/class/net/ethX/carrier`（1=通） |
| 查协商速率 | `cat /sys/class/net/ethX/speed`（100）`duplex`（full） |
| **统计计数（单向链路关键）** | `cat /sys/class/net/ethX/statistics/rx_packets` / `tx_packets` / `rx_bytes` / `tx_bytes` |
| 拉起接口 | `ip link set ethX up` |
| 配 IP | `ip addr add 192.168.100.2/24 dev ethX` |
| 查路由 | `ip route show` |
| 查 adb 端口 | `getprop service.adb.tcp.port` |
| 开网络 ADB | `setprop service.adb.tcp.port 5555; stop adbd; start adbd` |
| 抓包 | `tcpdump -i ethX`（需 root/`persist.adb.tcp.port`） |
| 查 adbd 监听 | `netstat -tlnp \| grep 5555` / `/proc/net/tcp` |

---

## 3. 分层检测流程（Detectors）

### 3.1 检测结果结构
```python
@dataclass
class DetResult:
    layer: str          # "L1"..."L7"
    name: str           # 检查项名
    ok: bool            # 通过？
    evidence: str       # 命令 + 关键输出
    advice: str         # 失败时给操作者的建议
    status: str         # PASS / FAIL / WARN / SKIP
```

### 3.2 L1 物理层
- 目标：确认网卡存在、链路 up、协商速率正常。
- macOS：`ifconfig enX`（status=active, media=100baseTX）；`networksetup -getmedia`。
- Windows：`Get-NetAdapter` Status=Up, LinkSpeed=100 Mbps / 1 Gbps。
- 失败建议：检查网线插入、扩展坞/USB 网卡是否被系统识别（`system_profiler SPUSBDataType` / 设备管理器）、换 USB-C 口。

### 3.3 L2 数据链路层
- 目标：确认网线对端可达（ARP 能解析到对端 MAC）、无单向故障。
- 探测：ping 对端候选 IP → `arp -an` 看是否出现对端 MAC；Android 已连则读 `/sys/class/net/ethX/statistics/rx_packets`。
- **单向故障判定**（核心）：
  - 本端 `Opkts` 增长但对端 `rx_packets`=0 → **PC→Android 发送方向物理不通**。
  - 建议：换 USB-C 口 → 换扩展坞/网卡 → 换网线 → 查供电。

### 3.4 L3 网络层
- 目标：确认本端已配 IP/掩码、对端同网段、路由正确、**默认路由保护**生效。
- 检测：
  - 本端 `ip addr`/`ifconfig enX`：IP=192.168.100.1/24。
  - 对端可达：`ping -S 192.168.100.1 192.168.100.2`。
  - 路由：`route -n get 192.168.100.2` / `Get-NetRoute` 确认走调试网卡。
  - **默认路由保护**：`netstat -rn -f inet | grep '^default'` 确认仍是原网卡。

### 3.5 L4 传输层
- 目标：收银机 5555 端口监听。
- `nc -z -G 3 -w 3 192.168.100.2 5555` / `Test-NetConnection -Port 5555`。
- 失败建议：见诊断树（开网络调试 / adbd 重启 / 以太网接口 UP）。

### 3.6 L7 应用层（ADB）
- 目标：`adb connect` 达到 `device` 状态。
- 状态机：`unauthorized` → 收银机屏幕点授权；`offline` → 重启 adbd 或重试；`failed to connect` → 回到 L4。

---

## 4. 配置器设计

### 4.1 PcConfigurator（默认路由保护策略）
```
输入：selected_iface, wanted_ip(默认192.168.100.1), mask(255.255.255.0)
流程：
1. 记录变更前状态快照：iface IP/网关/服务启停, 默认路由 iface。
2. 若 iface 未启用 → 启用（macOS networksetup on / Windows Enable-NetAdapter）。
3. macOS：确保 Wi-Fi(或上网网卡) 在服务优先级前面（ordernetworkservices 放前面）。
4. 设置 IP + 网关0.0.0.0（或 Windows 不设 DefaultGateway）。
5. 回验：
   a. 默认路由仍指向原网卡（关键保护）。
   b. 目标网段直连路由已出现（192.168.100.0/24 via iface）。
   c. iface 仍 active。
   任一项失败 → 用快照回滚 → 报错。
```

> **Windows metric 说明**：Windows 中同优先级多默认网关会按 metric 选主。策略是让调试网卡**没有默认网关**，从而完全不参与默认路由竞争。

### 4.2 AndroidConfigurator
```
输入：已连 ADB（Wi-Fi 或任何可用通道）
流程：
1. 读 getprop service.adb.tcp.port；非5555 → setprop 5555 + stop/start adbd。
2. ip addr show 找以太网接口 ethX（非 wlan/lo）。
3. 若 ethX DOWN → ip link set ethX up；确认 carrier=1。
4. 若 ethX 无 192.168.100.2 → ip addr add 192.168.100.2/24 dev ethX。
5. 回验：ip addr show ethX / ip route（含 192.168.100.0/24）/ carrier=1。
注意：这些是运行时配置，重启会丢。持久化需引导用户在系统设置→以太网配静态IP。
```

---

## 5. ADB 状态机

```
         +-----------------+
         |  adb connect    |
         +--------+--------+
                  |
        +---------+---------+
        |  failed to connect |
        |  (端口/链路不通)    |
        +---------+---------+
                  | → 回到 L2/L4 检测
        +---------+---------+
        |      offline       |
        +---------+---------+
                  | → 重启 adbd / 重试
        +---------+---------+
        |   unauthorized     |
        +---------+---------+
                  | → 收银机屏幕授权 → 重新 connect
        +---------+---------+
        |       device       |
        +-------------------+
                  | → 验证 shell: adb shell getprop ro.product.model
                  ↓
             成功 / 失败
```

---

## 6. 安全配置策略（macOS 深入：默认路由保护的完整解法）

本次实测确认的三个陷阱及其解法：

### 6.1 服务优先级陷阱
- 现象：USB 网卡服务排第一，配网关 → 默认路由切到 en11 → Wi-Fi 断网。
- 解法：配置前 `networksetup -ordernetworkservices "Wi-Fi" "<USB服务>" ...`，把上网网卡排前面。**改配置前先识别上网网卡并保证其优先级**。

### 6.2 网关 0.0.0.0
- `networksetup -setmanual "服务" IP 掩码 0.0.0.0` → 只产生直连网段路由，不产生默认路由。**这是首选方式，无需有效网关**。
- 副作用：`scutil --nwi` 可能只认 en0（en11 不进活动接口），导致三层仍不通（本次实测现象）。
- 结合 6.3 处理。

### 6.3 nwi 活动接口与有效网关的权衡
- 现象：网关 0.0.0.0 时 nwi 不认 en11，三层流量不通；设有效网关（192.168.100.1）后 nwi 认 en11，三层通，但产生第二条默认路由。
- 解法（推荐顺序）：
  1. `ordernetworkservices` 把上网网卡排前（保证其默认路由优先）。
  2. 给调试网卡设 `setmanual ... 192.168.100.1 掩码 192.168.100.1`（有效网关，让 nwi 认它）。
  3. 回验默认路由首选仍是上网网卡（`netstat -rn` 中 `UGScg` 带 `g` 标志的优先）。
  4. 若需彻底消除 en11 默认路由且保留三层可用：观察回验结果；若三层通则保持，若三层不通则需保留有效网关。**最终目标：三层可达 + 默认路由首选上网网卡**，二者通过"优先级排序"同时满足。
- 建议实现为**可配置策略**：`strategy = "no_default_route" | "priority_protected"`，默认 `priority_protected`。

### 6.4 回滚
- 每次写操作前记录快照（IP/掩码/网关/服务启停/服务顺序）。
- 回验失败或用户中止 → 恢复快照（`setdhcp`/`setmanual` 恢复原值、`ordernetworkservices` 恢复原顺序、`setnetworkserviceenabled` 恢复原启停）。

---

## 7. 诊断树实现（映射到 Detectors）

```
fail(adb connect)
  ├─ register_ip unknown → TopologyDetector: 网段扫描/ARP/读Android ip addr
  ├─ fail(L4 port 5555)
  │   ├─ android netdebug off → Advice: 开网络调试 + USB调试
  │   ├─ adbd not listening → AndroidConfig: setprop+restart
  │   ├─ ethX DOWN → AndroidConfig: ip link set up
  │   └─ one-way TX fail(RX=0) → L2Detector: 换USB-C/换扩展坞/换网线
  ├─ offline → restart adbd → retry
  ├─ unauthorized → 收银机屏点授权 → retry
  └─ ip netmask mismatch → 重配掩码
fail(PC config)
  └─ default route changed → rollback + report
```

---

## 8. 关键数据结构

```python
@dataclass
class NetIface:
    name: str            # en11 / "以太网" / ifIndex
    service: str | None  # macOS 网络服务名
    type: str            # ethernet / usb_ethernet / wifi
    ip: str | None
    mask: str | None
    gateway: str | None
    link_up: bool
    media: str | None    # 100baseTX / 1 Gbps
    vendor: str | None   # RTL8153 / ASIX / Realtek

@dataclass
class Snapshot:
    iface_cfg: dict      # 变更前 ip/mask/gateway/启停
    service_order: list  # macOS 服务顺序
    default_route_iface: str

@dataclass
class AdbState:
    host: str
    port: int
    status: str          # device/offline/unauthorized/not_connected
```

---

## 9. 目录结构与依赖

```
wired_adb_tool/
├── cli.py                # 入口：argparse，--platform/--iface/--ip/--report
├── orchestrator.py       # 阶段编排 + 状态机
├── platform/
│   ├── base.py           # PlatformAdapter ABC + CommandRunner
│   ├── macos.py          # macOS 适配
│   └── windows.py        # Windows 适配
├── detectors/
│   ├── topology.py       # 网卡枚举/默认路由/USB网卡识别
│   ├── physical.py       # L1
│   ├── datalink.py       # L2 + 单向故障
│   ├── network.py        # L3 + 路由 + 保护回验
│   ├── transport.py      # L4
│   └── adb.py            # L7 ADBClient
├── configurators/
│   ├── pc_config.py      # PcConfigurator + 回滚
│   └── android_config.py # AndroidConfigurator
├── reporter.py           # Markdown 报告
└── requirements.txt      # 依赖（标准库为主，可选 psutil）
```

依赖：**标准库优先**（subprocess / ipaddress / socket / dataclasses）；Windows 部分可选 `psutil` 或直接用 PowerShell/WMI。

---

## 10. 验收用例（对应需求文档 §9）

| 用例 | 设计验证点 |
|---|---|
| 正常直连 | 分层全部 PASS，adb 达 device，shell 可执行 |
| 网线未插 | L1 FAIL，提示物理检查，不配置 |
| 默认路由走 Wi-Fi | 配置后回验默认路由不变（§4.1 回验 a） |
| RTL8153 单向故障 | L2 RX=0 判定，建议换 USB-C 口 |
| 收银机未开网络调试 | L4 FAIL，提示开网络调试 |
| unauthorized | ADB 状态机 → 指引收银机屏授权 |
| 重复运行 | 幂等：检测到已配好则跳过配置 |
| 配置失败 | 快照回滚，默认路由恢复 |

---

## 11. 风险与限制

| 风险 | 缓解 |
|---|---|
| macOS 版本差异导致命令变化 | 命令执行失败时输出原始输出，供人工判断；保留 `--diagnostic` 模式 |
| Windows 管理员权限 | 检测阶段无权限；配置阶段检测非管理员则提示以管理员重跑 |
| Android 无 root | 运行时配置（ip/setprop）需 root 或 shell 权限；无权限时退化为纯人工指引 |
| 重启后 Android 配置丢失 | 提示用户在系统设置→以太网持久化静态 IP |
| 第三方安全软件拦截 | 若检测到 VPN/过滤器影响，提示临时关闭或走其他链路 |
