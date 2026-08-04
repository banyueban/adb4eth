# adb4eth — 有线 ADB 调试自动检测与配置工具

通过**网线直连**（PC 网口 ↔ 安卓收银机网口）自动完成 ADB 网络调试链路的检测、配置与连接。核心约束：**绝不破坏 PC 自身联网**（默认路由保护）。

## 支持的平台

- **macOS**（实测验证：扩展坞 RTL8153 USB 网卡、networksetup/ifconfig/netstat/ioreg）
- **Windows**（PowerShell / netsh 适配）
- **Android**（通过已连 ADB 通道自动配置 eth0 与 adbd；无通道时人工指引）

## 快速开始

```bash
cd /Volumes/Data/adb4eth
# 完整流程（检测 + 自动配置 + 连接）
python3 -m adb4eth

# 只检测，不修改任何配置
python3 -m adb4eth --no-config

# 自定义网段 + 输出报告
python3 -m adb4eth --net 192.168.100 --pc-ip 192.168.100.1 --reg-ip 192.168.100.2 --report report.md
```

## 图形界面（GUI）

customtkinter 界面，后台线程运行核心流程，实时展示逐层检测结果与建议。

```bash
python3 -m adb4eth --gui          # 以图形界面启动
adb4eth-gui                       # 安装后也可直接运行
```

依赖：`pip install customtkinter`（打包成 app 后无需安装 Python）。

### 打包成免安装应用（PyInstaller）

目标机器无需预装 Python / customtkinter，产出独立可执行文件。

```bash
python3 -m pip install pyinstaller
# 可选：将 platform-tools/adb 放进仓库根目录，打包时随附（运行时优先使用）
python3 -m PyInstaller adb4eth.spec
```

产物：
- macOS：`dist/adb4eth.app`（双击运行；首次被 Gatekeeper 拦截时右键→打开）
- 命令行：`dist/adb4eth`

> 打包需在目标平台上各自进行（macOS 打 macOS 包，Windows 打 Windows 包）。

## 流程

1. **拓扑**：枚举物理网卡、识别默认路由网卡（联网保护基准）、识别 USB 网卡
2. **L1**：链路状态、协商速率
3. **配置**：PC 静态 IP（网关 0.0.0.0，不产生默认路由）+ 优先级保护 + 快照回滚；Android 端拉起 ethX、配 IP、开 adbd
4. **L3**：IP/掩码、对端可达、路由指向、默认路由保护回验
5. **L4**：收银机 5555 端口探测
6. **L7**：`adb connect` → device/offline/unauthorized 状态机
7. **报告**：控制台 + Markdown

## 安全设计

- 配置前快照，失败自动回滚
- 网关恒为 `0.0.0.0`（macOS）/ 不设 `-DefaultGateway`（Windows）—— 不参与默认路由竞争
- 每次变更后回验默认路由未被改动

## 文档

- [需求文档](有线ADB调试自动检测与配置工具-需求文档.md)
- [技术设计文档](有线ADB调试自动检测与配置工具-技术设计文档.md)
