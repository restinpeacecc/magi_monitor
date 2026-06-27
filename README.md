# MAGI Monitor — Textual Edition

MAGI 系统监控器 — Textual 版（异步事件循环、线程工作器、CSS 布局）

## 📖 简介

基于 Python Textual 框架的终端系统监控工具，灵感来源于《新世纪福音战士》中的 MAGI 超级计算机。实时监控 CPU、GPU、内存、网络、磁盘等硬件指标，配备崩溃恢复日志系统。

## ✨ 主要特性

- **三贤者面板**：
- **MELCHIOR**: CPU 监控，标题栏显示活跃核心数 `N/8 ACTV` + 功率/频率四级热余量指示灯（CRITICAL/WARN/ATTN/STBL）
- **BALTHASAR**: 系统状态，标题栏显示最高 CPU 占用进程 + 功耗状态灯
- **CASPER**: GPU 监控，标题栏显示 pynvml Clocks Event Reasons + 性能状态 P-State

- **实时监控**：CPU/GPU 负载、频率、温度、电压、功耗、C-State、GPU 电压/显存结温、PCIe 带宽、VRAM 使用率、+3.3V/Vcore 电压轨
- **每核 C-State 追踪**：基于有效频率/标称频率比推断 8 核独立 C-State
- **活跃核心数**：复合判定（每核负载 > 10% OR 频比 > 0.15），SMT 双线程取最大值
- **警报系统**：1 级 ≥75°C / 2 级 (ANGEL DETECTED) ≥80°C，面板边框临界闪烁
- **可视化**：Braille 频率趋势图、动态进度条、颜色编码状态、闪动指示灯
- **崩溃恢复日志**：1s 间隔写入 `logs/crash_log.csv`，30min 滚动窗口，512KB 封顶

## 🚀 安装依赖

```bash
pip install textual rich psutil requests nvidia-ml-py
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

## 🔧 配置说明

### 温度阈值（面板边框闪烁触发）

```python
CPU_TEMP_CAUTION    = 50   # °C
CPU_TEMP_WARNING    = 60   # °C
CPU_TEMP_CRITICAL   = 70   # °C（面板边框闪烁）
```

1 级警报 75°C，2 级警报 (ANGEL DETECTED) 80°C。

### 活跃核心判定（复合阈值）

```python
# 负载 > 10% OR 有效频率/标称频率比 > 0.15
if ml > 10.0 or (nom > 0 and eff / nom > 0.15):
    combined_active += 1
```

### 外部依赖

- **LibreHardwareMonitor / OpenHardwareMonitor**: 本地 8085 端口 JSON API
- **pynvml (nvidia-ml-py)**: GPU 状态查询（Clocks Event Reasons，直调 nvml.dll）
- **wttr.in**: 天气（可选，离线显示 OFFLINE）

## 🏗️ 架构概览

### 四阶线程模型

| 定时器 | 周期 | 执行者 | 任务 |
|--------|------|--------|------|
| `_tick` | **0.2s** | `@work(thread, exclusive)` | OHM 轮询、psutil、频率历史、警报 |
| `_collect_gpu` | **1s** | `@work(thread, exclusive)` | pynvml GPU 状态 + 诊断（解码器/编码器/显存利用率） |
| `_log_tick` | **1s** | 主线程 | CSV 日志追加 (文件 I/O <1ms) |
| `_collect_slow_tasks` | **5s** | `@work(thread, exclusive)` | top 进程、ping、天气、TCP、swap |

### 核心类

- **`MagiState`**: 线程安全的共享状态（标量无锁，历史列表由 `_list_lock` 保护）
- **`MAGIScanner`**: OHM JSON API 传感器数据采集
- **`MAGIApp`**: Textual 主应用

## 📊 面板说明

### MELCHIOR (CPU)
- **标题**: `MELCHIOR | N/8 ACTV`，活跃核心数颜色编码（0~1 青色、2~4 绿色、5~6 黄色、7~8 红色）
- **副标题**: 功率+频率四级指示灯（CRITICAL 红闪 2.5Hz / WARN 金闪 1Hz / ATTN 绿闪 0.5Hz / STBL 青 reverse）
- LOAD: CPU 使用率进度条
- FREQ: 频率 + 趋势箭头 + 最小/最大值
- TREND: Braille 频率曲线
- V-AVG: 平均 VID 电压
- CORES: 8 核热点图（每核 1 字符，`█` >50% / `░` ≤50%，颜色四级）
- PKG-W: CPU 封装功耗 + C-State
- TEMP: CPU 温度（颜色编码）
- FAN: CPU 风扇转速

### BALTHASAR (SYSTEM)
- **标题**: `BALTHASAR | {进程名} {cpu}%`，按 CPU% 着色（cyan <10% → green <50% → yellow <80% → red1）
- **副标题**: 整机功耗状态灯（ECO/HPC/OVERDRIVE）
- MEMORY: 内存使用率 + 进度条
- USED / FREE: 内存用量
- NET-DN: 当前下载速度 @ 历史最大
- PING: 网络延迟（颜色编码）
- TCP: EST/TW 连接数
- MEMTMP: 内存温度（颜色编码，与 CPU/GPU 温度行对齐）
- DISK: 磁盘读写速度
- POWER: 整机估算功耗（CPU+GPU+基础偏移）

### CASPER (GPU)
- **标题**: `CASPER | {status}`，状态映射：STBY 青、NORM 绿、BOOST 金、PWR 黄、THR 红
- **副标题**: 负载状态灯（IDLE/TRG/LCK/RTX-ON）
- LOAD: GPU 使用率进度条
- FREQ: 核心频率 + 趋势箭头
- VRAM: 显存使用率进度条
- VCORE: GPU 电压
- TGP: GPU 封装功耗 + P-State
- TEMP: GPU 温度
- PCIe: PCIe 接收/发送速率
- FAN: 风扇转速

## 🎨 视觉风格

- **边框颜色**: orange3（正常）/ bold red（临界闪烁，HEAVY 加粗边框）
- **面板标题**: 各面板独立颜色编码指示灯
- **温度颜色**: spring_green1 (<45°C) → yellow (45-68°C) → red1 (>68°C)
- **趋势箭头**: ▲ 上升 / ▼ 下降 / ► 稳定
- **Header**: 居中显示，时间/运行时间/天气/主机名/日期

## 📝 崩溃恢复日志

- **文件**: `logs/crash_log.csv`（36 列）
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
- NVIDIA NVML / pynvml 库

---

**MAGI SYSTEM ONLINE — SYNC RATE 100%**
