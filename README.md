# MAGI Monitor — Textual Edition

MAGI 系统监控器 — Textual 版（异步事件循环、线程工作器、CSS 布局）

## 📖 简介

基于 Python Textual 框架的终端系统监控工具，灵感来源于《新世纪福音战士》中的 MAGI 超级计算机。实时监控 CPU、GPU、内存、网络、磁盘等硬件指标，配备崩溃恢复日志系统。

## ✨ 主要特性

- **三贤者面板**：
  - **MELCHIOR**: CPU 监控，标题栏显示 C-State (C0~C7)
  - **BALTHASAR**: 系统状态，标题栏显示最高 CPU 占用进程
  - **CASPER**: GPU 监控，标题栏显示 nvidia-smi Clocks Event Reasons

- **实时监控**：CPU/GPU 负载、频率、温度、电压、功耗、C-State、GPU 电压/显存结温、PCIe 带宽、VRAM 使用率、+3.3V/Vcore 电压轨
- **警报系统**：1 级 ≥75°C / 2 级 (ANGEL DETECTED) ≥80°C
- **可视化**：Braille 频率趋势图、动态进度条、颜色编码状态
- **崩溃恢复日志**：1s 间隔写入 `logs/crash_log.csv`，30min 滚动窗口，512KB 封顶

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
| `m` | 暂停并启动 pstop |
| `n` | 暂停并启动 psnet |
| `t` | 暂停并启动 yazi f:\ |
| `x` | 暂停并启动 opencode D:\tools |

## 🔧 配置说明

### 温度阈值

```python
CPU_TEMP_CAUTION    = 50   # °C
CPU_TEMP_WARNING    = 60   # °C
CPU_TEMP_CRITICAL   = 70   # °C（面板边框闪烁）
```

1 级警报 75°C，2 级警报 (ANGEL DETECTED) 80°C。

### 外部依赖

- **LibreHardwareMonitor / OpenHardwareMonitor**: 本地 8085 端口 JSON API
- **nvidia-smi**: GPU 状态查询（Clocks Event Reasons）
- **wttr.in**: 天气（可选，离线显示 OFFLINE）

## 🏗️ 架构概览

### 三阶线程模型

| 定时器 | 周期 | 执行者 | 任务 |
|--------|------|--------|------|
| `_tick` | **0.2s** | `@work(thread, exclusive)` | OHM 轮询、psutil、频率历史、警报 |
| `_log_tick` | **1s** | 主线程 | CSV 日志追加 (文件 I/O <1ms) |
| `_collect_slow_tasks` | **5s** | `@work(thread, exclusive)` | nvidia-smi、top 进程、ping、天气、TCP |

### 核心类

- **`MagiState`**: 线程安全的共享状态（标量无锁，历史列表由 `_list_lock` 保护）
- **`MAGIScanner`**: OHM JSON API 传感器数据采集
- **`MAGIApp`**: Textual 主应用

## 📊 面板说明

### MELCHIOR (CPU)
- **标题**: `MELCHIOR | C{n}`，C-State 等级颜色编码（C7/C6 青色、C5/C4 绿色、C3/C2 黄色、C1/C0 红色）
- LOAD: CPU 使用率进度条
- FREQ: 当前/有效频率 + 趋势箭头
- TREND: Braille 频率曲线
- V-AVG: 平均电压
- PKG-W: 封装功耗 + PROCHOT 状态
- TEMP: CPU 封装温度（颜色编码）
- VID1~8: 各核心 VID 电压
- FAN: CPU 风扇转速 + 分压比

### BALTHASAR (SYSTEM)
- **标题**: `BALTHASAR | {进程名} {cpu}%`，按 CPU% 着色（<10% 绿色、10-30% 黄色、>30% 红色）
- MEMORY: 内存使用率 + 进度条
- TEMP: 内存温度
- NET-DN / NET-UP: 网络下载/上传速度
- PCIe RX / TX: PCIe 带宽
- PING: 网络延迟
- TCP: EST/TW 连接数
- DISK: 磁盘读写速度
- +3.3V / Vcore: 电压轨
- P-STAT: 功耗状态

### CASPER (GPU)
- **标题**: `CASPER | {status}`，状态映射：IDLE 绿色、STBY 青色、BOOST 黄色、PWR 红色、HOT 深红
- LOAD: GPU 使用率进度条
- FREQ: 核心频率 + 趋势箭头
- VRAM: 显存使用率 + 进度条
- VCORE: GPU 电压
- TGP: 整板功耗
- TEMP: GPU 温度
- MEM JUNCTION: 显存结温
- FAN: 风扇转速

## 🎨 视觉风格

- **边框颜色**: orange3（正常）/ bold red（临界闪烁）
- **温度颜色**: spring_green1 (<45°C) → yellow (45-68°C) → red1 (>68°C)
- **趋势箭头**: ▲ 上升 / ▼ 下降 / ► 稳定

## 📝 崩溃恢复日志

- **文件**: `logs/crash_log.csv`（31 列）
- **周期**: 每秒追加
- **窗口**: 启动时裁剪到最近 30 分钟
- **封顶**: 512KB，超出时保留前半行数
- **静默失败**: 所有 I/O 异常被捕获，不干扰 UI

## ⚠️ 注意事项

1. 需要管理员权限读取部分硬件传感器
2. LibreHardwareMonitor 需预先运行并开启 Web Server（端口 8085）
3. weather 服务依赖网络连接
4. 首次调用 `psutil.cpu_percent()` 返回 0.0，有效数据在第二次 5s 后出现

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
- NVIDIA nvidia-smi 工具

---

**MAGI SYSTEM ONLINE — SYNC RATE 100%**
