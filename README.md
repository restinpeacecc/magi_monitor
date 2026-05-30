# MAGI SYSTEM Monitor — Textual Edition

MAGI 系统监控器 — Textual 版（异步事件循环、线程工作器、CSS 布局）

## 📖 简介

这是一个基于 Python Textual 框架构建的终端系统监控工具，灵感来源于《新世纪福音战士》中的 MAGI 超级计算机系统。它提供实时的 CPU、GPU、内存、网络、磁盘等硬件监控功能，并采用复古科幻风格的界面设计。

## ✨ 主要特性

- **三贤者面板设计**：
  - **MELCHIOR (01)**: CPU 监控（AMD Ryzen 7 7800X3D）+ Ollama 模型信息
  - **BALTHASAR (02)**: 系统状态（内存、网络、磁盘、TCP 连接）+ Ollama 请求计数
  - **CASPER (03)**: GPU 监控（NVIDIA RTX 5070）+ Ollama GPU 卸载状态

- **实时监控指标**：
  - CPU/GPU 负载、频率、温度、电压、功耗
  - 内存使用量
  - 网络下载/上传速度
  - 磁盘读/写速度
  - TCP 连接统计
  - 系统运行时间
  - 天气信息（通过 wttr.in）
  - PING 延迟

- **警报系统**：
  - 1 级警报：CPU/GPU 温度 ≥ 75°C
  - 2 级警报（ANGEL DETECTED）：CPU/GPU 温度 ≥ 80°C

- **可视化元素**：
  - Braille 字符频率趋势图
  - 动态进度条
  - 闪烁状态指示器
  - 颜色编码的温度/功耗状态

## 🚀 安装依赖

```bash
pip install textual rich psutil requests
```

## ▶️ 运行

```bash
python magi_monitor_MIX.py
```

## ⌨️ 快捷键

| 按键 | 功能 |
|------|------|
| `q` | 退出应用 |
| `m` | 暂停并启动 pstop |
| `n` | 暂停并启动 psnet |
| `t` | 暂停并启动 yazi f:\ |
| `x` | 暂停并启动 opencode D:\tools |

## 🤖 Ollama 集成

通过监听本地 Ollama 进程数据，在面板上实时显示：

- **MELCHIOR QUANT**: 当前加载的模型家族与量化等级
- **BALTHASAR REQ**: 累计推理请求数及距上次请求时间
- **CASPER OFFLOAD**: 模型层在 GPU/CPU 间的卸载比例

数据来源：
- `http://localhost:11434/api/ps` → 模型元数据（家族、量化）
- `C:\Users\kugim\AppData\Local\Ollama\server.log` → GPU 卸载信息、GIN 请求日志

三种状态：
| 状态 | 颜色 | 含义 |
|------|------|------|
| 模型信息 | `bold #BA55D3` | 有模型加载 / 有请求记录 / 正在卸载 |
| STBY | `green` | Ollama 在线但无模型 |
| OFFLINE | `dim` | Ollama 服务不可达 |

## 🔧 配置说明

### 温度阈值（可调整）

```python
CPU_TEMP_CAUTION    = 50   # °C - 注意
CPU_TEMP_WARNING    = 60   # °C - 警告
CPU_TEMP_CRITICAL   = 70   # °C - 临界（1 级警报）
```

### 功耗阈值（可调整）

```python
POWER_SAFE  = 100  # W
POWER_WARN  = 180
POWER_CRIT  = 300
```

### 外部依赖

- **LibreHardwareMonitor / OpenHardwareMonitor**: 需要运行在本地 8085 端口提供 JSON API
- **wttr.in**: 天气信息服务（可选，离线时显示 "OFFLINE"）

## 🏗️ 架构概览

### 核心类

- **`MagiState`**: 维护所有监控状态数据，线程安全的历史记录管理
- **`MAGIScanner`**: 从硬件监控服务获取传感器数据
- **`MAGIApp`**: Textual 主应用，处理 UI 渲染和事件循环

### 工作线程

- **`_worker_cpu`**: 高频 CPU 数据采集（约 200Hz）
- **`_worker_slow`**: 低频任务（PING、天气、TCP 统计）
- **`_refresh_all`**: UI 刷新调度

### 线程安全设计

- 使用 `threading.Lock` 保护历史列表
- 提供快照方法避免渲染时数据竞争
- 分离快慢任务保证传感器刷新率

## 📊 面板说明

### MELCHIOR (CPU)
- LOAD: CPU 使用率进度条
- FREQ: 当前频率 + 最小/最大值 + 趋势箭头
- TREND: Braille 频率曲线（5 分钟历史）
- V-AVG: 平均电压
- PKG-W: 封装功耗
- TEMP: 温度（颜色编码）
- FAN: 风扇转速
- MODEL: Ollama 模型信息（模型名+量化等级 / STBY / OFFLINE）
- FUSE: 状态指示器（ECO/ACTIVE/HIGH-LOAD/OVERDRIVE）

### BALTHASAR (SYSTEM)
- MEMORY: 内存使用量和进度条
- USED / FREE: 内存用量
- NET-DN: 网络下载速度 + 峰值
- DISK: 磁盘读写速度
- TCP: EST/TW 连接数
- PING: 网络延迟
- REQ: Ollama API 请求计数（累计 + 距上次时间 / STBY / OFFLINE）
- P-STAT: 功耗状态指示器

### CASPER (GPU)
- LOAD: GPU 使用率进度条
- FREQ: 当前频率 + 最小/最大值 + 趋势箭头
- VRAM: 显存使用量和进度条
- VCORE: GPU 电压
- TGP: 整板功耗
- TEMP: 温度（颜色编码）
- FAN: 风扇转速
- OFFLOAD: Ollama GPU 卸载层数（已卸载/总层数 / STBY / OFFLINE）
- COMP: GPU 计算状态（IDLE / INIT / ENGAGED / RTX-ON）

## 🎨 视觉风格

- **边框颜色**: orange3（正常）/ bold red（临界闪烁）
- **温度颜色**: spring_green1 (<45°C) → yellow (45-68°C) → red1 (>68°C)
- **趋势箭头**: ▲ (上升) / ▼ (下降) / ► (稳定)
- **状态文本**: 带反转效果和闪烁动画
- **AI 状态颜色**:
  - `[bold #00ff00]` (绿色) — 模型已加载、有推理请求、GPU 卸载中
  - `[green]` — STBY（Ollama 在线无模型）
  - `[dim]` — OFFLINE（Ollama 不可达）

## ⚠️ 注意事项

1. 需要管理员权限读取部分硬件传感器数据
2. TCP 连接统计可能需要提升权限
3. 天气服务依赖网络连接
4. 硬件监控服务需预先配置并运行

## 📝 技术栈

- **Python 3.8+**
- **Textual**: TUI 框架
- **Rich**: 终端富文本渲染
- **psutil**: 系统监控
- **requests**: HTTP 请求

## 🙏 致谢

- Evangelion 系列作品启发
- Textual 社区支持
- LibreHardwareMonitor 项目

---

**MAGI SYSTEM ONLINE — SYNC RATE 100%**
