# MAGI Monitor — Agent Notes

## Run

### Normal (non-elevated, no event log)
```
python magi_monitor_MIX.py
```

### Elevated (event log monitoring via WT profile)
Use WT dropdown → **MAGI Monitor** profile (pre-configured with `elevate: true`).

## Architecture

- Single-file Textual TUI app (`magi_monitor_MIX.py`), entry point: `MAGIApp().run()`
- Reads hardware sensors from **HWiNFO shared memory** (`Global\HWiNFO_SENS_SM2`) via `MAGIScanner` (ctypes `MapViewOfFile`, no admin required, no JSON API)
- GPU status via **pynvml** (direct `nvml.dll` binding, no subprocess), polled every 1s
- Windows Event Log monitoring via **pywin32** (`win32evtlog`), polled in a background thread every 5s
- Requires `psutil`, `requests`, `nvidia-ml-py`, `pywin32` (no `pyproject.toml` / `requirements.txt` — install manually)
- No test suite, no lint/typecheck config

## Windows Event Log Monitoring

- Monitors the **System** log for Event IDs `{129, 136, 153, 7040}` (NVMe disk errors + service start type changes)
- Runs in a **background daemon thread** (`evtlog`), polling via `win32evtlog.ReadEventLog` every 5s
- Uses a **thread-safe queue** (`list` + `Lock`) — the poller appends alerts, the main thread's `_tick` drains them via `_flush_eventlog_alerts()`
- **Requires elevation** (admin) to open the System log with `OpenEventLog`
- **Launch method**: Windows Terminal profile with `"elevate": true` (created at `%LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_*`)
- On match: plays `winsound.MessageBeep(MB_ICONHAND)`, pushes a full-screen `EventLogAlertScreen` (dark red, red border, event details)
- Dismiss: any key or click → `pop_screen()` returns to the main TUI
- **Edge cases (fixed)**:
  - `time.localtime(event.TimeGenerated)` fails because `pywintypes.datetime` is not int — use `.timestamp()`
  - CSS color variables (`$error`, `$text`, `$surface`) cause native crash under elevated WT — use literal hex colors (`#ff4444`, `#cccccc`, `#666666`)
  - SplashScreen's auto-dismiss `pop_screen()` pops the alert (top screen) instead of itself — check `app.screen is self` and retry 0.5s later
  - `EventLogAlertScreen` with blink timer (`set_interval(0.66, ...)`) causes segfault-like crash (WT also dies) when CSS uses `$` color variables — root cause unknown but using hex colors + blink works fine

## Four-Tier Timer Model

| Timer | Period | Worker | Tasks |
|-------|--------|--------|-------|
| `_tick` | **0.2s** | `@work(thread, exclusive)` | HWiNFO sensor polling, psutil, freq history, alerts |
| `_collect_gpu` | **1s** | `@work(thread, exclusive)` | pynvml GPU status (Clocks Event Reasons) + diagnostics (decoder/encoder/mem util) + Ping (ICMP echo) + PCIe RX/TX throughput |
| `_log_tick` | **1s** | Main thread | CSV log append (file I/O < 1ms) |
| `_collect_slow_tasks` | **5s** | `@work(thread, exclusive)` | top CPU process, swap, weather, external IP, TCP, HWiNFO64 定时重启状态机 |

- **State (`MagiState`) is shared between threads without locks** on scalar fields. Only the freq history lists are protected by `_list_lock`. When modifying state fields, be aware of potential data races.

## Panel Titles

| Panel | Title Format | Example | Source |
|-------|-------------|---------|--------|
| MELCHIOR (CPU) | `MELCHIOR \| N/8 ACTV` | `MELCHIOR \| 6/8 ACTV` | Active core count (load>10% OR freq ratio>0.15), color-coded |
| BALTHASAR (System) | `BALTHASAR \| {name} {cpu}%` | `BALTHASAR \| chrome 23%` | Top CPU-consuming process (`.exe` stripped, 10 char trunc) |
| CASPER (GPU) | `CASPER \| {status}` | `CASPER \| STBY` | pynvml Clocks Event Reasons → IDLE/STBY/BOOST/PWR/HOT; P-state from NVML `Performance State` |

## Panel Rows

| Panel | Row | Data Source | Notes |
|-------|-----|-------------|-------|
| MELCHIOR | TREND | iGPU `GPU D3D Usage` via HWiNFO `hw_contains="radeon"` | Braille trend, combined with decode usage, clamp 100 |
| MELCHIOR | iCORE | iGPU `GPU Utilization` via HWiNFO `hw_contains="radeon"` | >0% → green/yellow/red 三级着色, else `IDLE` |
| MELCHIOR | LIMIT | HWiNFO `CPU TDC Limit` / `CPU EDC Limit` (%) | `LIMIT TDCxx% EDCxx%`，<30 绿 / <70 黄 / ≥70 红 |
| MELCHIOR | border flash (fuse_crit) | CPU Package power + `cpu_freq_nom` | 50W+4700MHz → CRITICAL 闪烁（与 PROCHOT 警报独立） |
| BALTHASAR | MEMTMP | HWiNFO `SPD Hub Temperature` + `Drive Temperature` via `hw_contains="sn850x"/"spcc"/"sa510"` | `RM{val} WD{val} SP{val} ST{val} °C`, 白标签+色数值 |
| BALTHASAR | PCIe | HWiNFO `PCIe Link Speed` (GT/s) via `hw_contains="nvidia"` | PCIe 链路速率, ≥16GT/s green, ≥8GT/s yellow |
| BALTHASAR | DISK | `psutil.disk_io_counters` R+W 总和 | 方块 sparkline, y_range=(0,200), 绿/黄/红三色 |
| CASPER | CODEC | pynvml `gpu_decoder_util` / `gpu_encoder_util` | >1% → DECODING/ENCODING blink 5Hz |
| CASPER | FG | HWiNFO (pynvml) 帧生成逻辑 | >0% → FG ON (xx%) blink 3Hz, else FG OFF dim |

## Crash Recovery Log (`logs/crash_log.csv`)

- **Purpose**: Last 30 min of sensor data before abnormal shutdown (no BSOD dump)
- **Columns** (31 fields): `time,cpu_load,cpu_temp,cpu_pkg_w,cpu_eff_freq,cstate,cpu_fan,cpu_vddcr_v,mem_pct,mem1_temp,mem2_temp,nvme1_temp,nvme2_temp,sata_temp,gpu_load,gpu_temp,gpu_mem_junc_temp,gpu_pwr,gpu_core_freq,gpu_volt,vram_pct,gpu_status,gpu_pstate,psu_v,v3vc,top_proc,top_cpu,gpu_decoder_util,gpu_encoder_util,gpu_mem_util,gpu_clk_reasons`
- **Writing**: `_log_tick()` every 1s, simple `open+append` on main thread
- **Startup pruning**: `_init_log()` retains only rows within 1800s of current time (cross-midnight safe)
- **Size cap**: `LOG_MAX_BYTES = 512KB` → auto trims to half when exceeded
- **Silent failure**: All I/O exceptions caught, never crashes the app

## Key Bindings

| Key | Action |
|-----|--------|
| `m` | Launch `pstop` (via `self.suspend()`) |
| `n` | Launch `psnet` |
| `t` | Launch `yazi f:\` |
| `r` | 手动触发一轮 HWiNFO64 强杀重启（调试/恢复用）；未提权时仅提示不强杀 |

## Conventions

- All comments in Chinese
- No `.env` loaded programmatically; `.env` is gitignored
- `archive/` dir holds older iterations (GPT, Gemini, DeepSeek, Qwen versions)

## Design Notes

- **PROCHOT 过热降频警报（Toast）**：核心判定以 HWiNFO `thermal throttling (prochot cpu)` 为主（CPU 内部 DTS 触及 TjMax 89°C），`prochot ext` 为辅（主板 VRM/外围拉低）。`update_alert()` 判定：二者同时触发 → 2 级（CRITICAL OVERHEAT error 通知），任一触发 → 1 级（CPU THROTTLE / VRM THROTTLE warning 通知），30s 去重。边框闪烁（fuse_crit）仍由 CPU 功耗/频率驱动，与 PROCHOT 警报独立。
- **CPU 核心电压直读**：MELCHIOR `V-DDC` 行显示 HWiNFO 原生 SVI3 传感器 `CPU VDDCR_VDD Voltage (SVI3 TFN)`（`state.cpu_vddcr_v`），不再走 8 核 VID 平均（LHM 限制下的权宜之计，平均值无精确度且掩盖真实单核高 VID）。
- **MELCHIOR title `MELCHIOR | N/8 ACTV`**: Shows active core count `N/8` (7800X3D = 8 physical cores). "Active" = per-core load > 10% OR effective/nominal frequency ratio > 0.15, read from HWiNFO `Core {i} T0/T1 Usage`（SMT: 物理核 i 两线程取 max）和 `Core {i} Clock` / `Core {i} T0/T1 Effective Clock`. Color tiers: ≤1 cyan, 2~4 green, 5~6 yellow, 7~8 red1.
- **MELCHIOR TREND row**: iGPU `GPU D3D Usage` + `GPU Video Decode 0 Usage` combined load (%) braille trend. History window = 100 points (~20s at 0.2s interval), clamp 100.
- **MELCHIOR subtitle** uses `fuse_indicator` driven by CPU power + frequency, independent from iGPU state.
- **MELCHIOR PKG-W shows C-State**: `52.3 W | C0` format, matching CASPER's `TGP | P0` format. Uses original `cpu_cstate_level` from effective/nominal freq ratio.
- **CASPER TGP shows P-State**: `24.8 W | P0` format. P-State parsed from NVML `Performance State` field.
- **MAGIScanner matching is end-anchored**: `get_val()` uses regex `(?:^|\W)target$` instead of substring match to avoid `Cores (Average)` hitting `Cores (Average Effective)`, and `Core #1` hitting `Core #10`. Per-core lookup uses `get_core_freq()` with `startswith` for additional safety.
- **iGPU 共存时用 `hw_contains` 过滤**：`get_val()` 支持 `hw_contains` 参数，GPU 查询传入 `"nvidia"` 确保独显传感器不被 iGPU 同名传感器干扰。所有 GPU HWiNFO 查询统一使用此过滤。
- **Alert thresholds are intentionally tiered**: 高温 Toast 警报已完全移除，改为 PROCHOT 过热降频警报（见上）。边框闪烁（`fuse_crit`）由 CPU 功耗 + 频率驱动（50W+4700MHz → CRITICAL），与 Toast 警报独立，不应统一。
- **Panel titles are plain text**: After removing the unstable Ollama monitoring feature, panel titles show simple names (MELCHIOR / BALTHASAR / CASPER).

## Completed Fixes

- **PROCHOT 降频警报替代高温警报** — 移除 75/80°C 温度 Toast 警报，改为 PROCHOT 信号（`thermal throttling (prochot cpu/ext)`）驱动：双触发 2 级 / 任一 1 级，30s 去重；MELCHIOR 面板原 THRM 行改为 LIMIT 行显示 `CPU TDC/EDC Limit` 百分比
- **HWiNFO64 周期性重启（规避 12h 共享内存停更）** — `_collect_slow_tasks` 内三阶段状态机：首次触发锚定 **HWiNFO 自身启动时间 + 11h40m**（`psutil process create_time`，而非系统开机时间——避免每次打开脚本都无差别强杀一轮），`taskkill /F` 强杀 → 5s → **`ShellExecuteW("open")` 重启（勿用 `subprocess.Popen`！）** → 轮询验证（1s×10s）；之后每 11h40m 周期执行；scanner 每帧重开映射句柄，重启后自动恢复；强杀失败（进程仍在运行）提示一次并顺延
- **HWiNFO 启动必须用 `ShellExecuteW`** — requireAdministrator 目标用 `subprocess.Popen`（CreateProcess）启动，**即使调用进程已提权（High Integrity）也返回 WinError 740「请求的操作需要提升」**（2026-08-04 实测复现：提权 WT 中直接 `Popen` 报 740，`ShellExecuteW` rc=42 成功）。`_launch_hwinfo()` 是唯一启动路径（ShellExecuteW 不返回 pid，阶段 2 按进程名验证）
- **启动提权自检 + 恢复巡检** — `on_mount` 记录 `state.is_admin`（`IsUserAnAdmin`）进 hwinfo_restart.log；header 显示 `[ADM]`(绿)/`[USR]`(红) 角标；启动 30s 后若提权且 HWiNFO 未运行则 `_launch_hwinfo(auto=True)` 自动拉起，非提权仅记日志不动作
- **hwinfo_restart.log 自动裁剪** — 上限 `HWiNFO_RESTART_LOG_MAX_BYTES = 256KB`，超限时保留后半行数（与 crash_log 同款策略，静默失败不影响主程序）
- **PCIe 吞吐单位修正** — `nvmlDeviceGetPcieThroughput` 原生返回 KB/s，原代码误当 bytes 再除 1000（显示缩小 1000 倍）；`pcie_link_gts`（GT/s 链路速率）意义不大已删除（含 crash_log 列）
- **CPU 核心电压改用 HWiNFO 原生 SVI3 传感器** — 移除原 8 核 VID 采集与平均（LHM 限制），切换为 `CPU VDDCR_VDD Voltage (SVI3 TFN)`，MELCHIOR `V-AVG` 行改 `V-DDC` 显示 `cpu_vddcr_v`；crash_log 的 `cpu_vid1~8` 列替换为该单列
- **LHM JSON API → HWiNFO 共享内存** — 移除 `http://localhost:8085/data.json` 轮询，`MAGIScanner` 改为 ctypes `MapViewOfFile` 读取 `Global\HWiNFO_SENS_SM2`（魔数 `0x53695748`），固定偏移解析 (rev2+ reading 元素勿用 `ctypes.sizeof`)；无需管理员权限；HWiNFO label 全部小写且单位独立于 label 字段；PCIe 行改显示链路速率 `GT/s`；CPU 风扇改用 `CPUFANIN0`；iGPU TREND 改用 `GPU D3D Usage`；iCORE 改用 `GPU Utilization`；网络速率改用 HWiNFO `Current DL Rate`（格式化 `NN KB/s` 兼容下游正则）；crash_log 的 `pcie_rx/pcie_tx` 列替换为 `pcie_link_gts`
- `build_balthasar()` state mutation in `render()` — moved `state.add_net_dn()` to `_collect()`
- `MAGIScanner.get_val()` linear search — now uses pre-lowered `list[tuple]` cache
- `_refresh_all()` repeated `self.query()` — widget references cached in `on_mount()`
- `parse_n()` missing type annotation — added `v_str: str | None`
- `generate_braille_trend()` double `min/max` — replaced with single-pass loop
- Panel title/subtitle string rebuild — extracted as module constants
- Comment language unified to Chinese
- `build_casper()` / `build_balthasar()` — added state guard for panel display
- **Ollama monitoring removed**: The entire Ollama data collection (`/api/ps` polling + log scanning) and its AI state display fields were removed due to suspected system instability (freeze/black screen/reboot). Panel titles reverted to plain text.
- `parse_n()` not stripping units ("MHz", "°C", etc.) — added `.split()[0]` to handle OHM's string-embedded unit values
- C-State inference added — reads `Cores (Average Effective)` / `Cores (Average)` ratio → maps to C0~C7
- GPU status replaces P-State — parses `nvidia-smi` Clocks Event Reasons for IDLE/PWR/HOT/BOOST
- Top CPU process in BALTHASAR title — `psutil.process_iter(["name", "cpu_percent"])`, filters System Idle Process
- Crash recovery log (`logs/crash_log.csv`) — 1s interval, 30min rolling window, 512KB cap, silent failure
- GPU P-State added — parses `Performance State` from nvidia-smi output, displayed in CASPER TGP row as `W | P0`
- MELCHIOR subtitle changed — from C-State groups to power+freq tier (CRITICAL/WARN/ATTN/STBL), computed at render time in `build_melchior()`
- PKG-W row format unified with TGP — shows `W | C0` matching `W | P0` on CASPER
- `cpu_freq_nom` field added — uses "Cores (Average)" sensor for tier threshold (avoids effective freq averaging issue with idle cores)
- `build_core_heatmap()` added — per-core load heatmap in MELCHIOR panel, 8 chars, 3-level threshold per core
- Memory Temp added to BALTHASAR — `state.mem_temp` displayed with dynamic color matching other TEMP rows
- PCIe Rx/Tx added to CASPER — `state.pcie_rx_mbs` / `state.pcie_tx_mbs` shown in a new row
- Dead code removed — orphaned `try/return float` after `ratio_to_cstate()` cleaned up
- FREE 行颜色编码 — 可用内存 >15G green, >10G yellow, ≤10G red1，使用 `parse_n()` 安全解析含单位的字符串
- GPU polling 从 5s 改为 1s — 新增 `_collect_gpu` 定时器，与原 `_collect_slow_tasks` 拆离，避免 ping/TCP/进程枚举等被连带加速
- **nvidia-smi 子进程 → pynvml 直调** — 移除两个 `subprocess.run(["nvidia-smi", ...])`，改用 pynvml 直接绑定 `nvml.dll`（无子进程，无 stdout 解析）；GPU 电源/温度来自 HWiNFO（NVAPI via 共享内存）；`gpu_recovery_action` 替换为 `gpu_clk_reasons`（Clocks Event Reasons 原始 bitmask）
- **iGPU 共存 HWiNFO 传感器过滤** — CPU 启用集成显卡后，HWiNFO 同时报告 iGPU + dGPU 同名传感器（GPU Clock/MHz/°C 等），`get_val()` 新增 `hw_contains` 参数，所有 GPU 查询传入 `"nvidia"` 确保读取独显数据
- **iGPU 监控（7800X3D 核显副屏）** — MELCHIOR 面板 TREND 行改用 iGPU `D3D 3D` 负载点阵（`hw_contains="radeon"`，y_range=0~70），subtitle 显示 `D3D Video Codec 0` 四档（40/25/10%）；border flash 保留 CPU 功耗/频率触发逻辑不变
- **iGPU TREND 双引擎** — D3D 3D + D3D Copy 双引擎负载点阵，移除因 bug 不可用的 Video Codec；GPU Core % 独立显示于 iCORE 行
- **MELCHIOR iCORE 行** — 原 VCODEC 行替换为 GPU Core 利用率数值显示（>0% 三级着色，=0% 绿色 IDLE）
- **CASPER VCODEC 行** — 新增 pynvml 解码器/编码器利用率活动指示（5Hz 闪烁）
- **CASPER FG 行** — 新增 D3D Optical Flow Accelerator 0 帧生成活动指示（>0% 3Hz 闪烁带百分比）
- **PCIe 行移至 BALTHASAR** — 原 CASPER PCIe 行移至 BALTHASAR DISK 行下方，修正单位从 G → MB/s
- **DISK 行改为 sparkline** — 替换数字 `R:xx W:xx MB/s` 为 `generate_sparkline()` 方块 sparkline，`y_range=(0, 200)` 适配 NVMe + HDD 混合场景，新增 `disk_history` 100 点历史缓冲
- **Drive Temperature Monitoring** — MEMTMP 行新增 WD_BLACK SN850X(NVMe) + SPCC M.2(NVMe) + WD Blue SA510(SATA) 温度监控，`RM{val} WD{val} SP{val} ST{val} °C` 紧凑格式白标签+色数值，三字段写入 crash_log.csv
