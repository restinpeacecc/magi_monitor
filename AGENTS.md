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
- Reads hardware sensors from **OpenHardwareMonitor / LibreHardwareMonitor** JSON API at `http://localhost:8085/data.json` via `MAGIScanner`
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
| `_tick` | **0.2s** | `@work(thread, exclusive)` | OHM sensor polling, psutil, freq history, alerts |
| `_collect_gpu` | **1s** | `@work(thread, exclusive)` | pynvml GPU status (Clocks Event Reasons) + diagnostics (decoder/encoder/mem util) |
| `_log_tick` | **1s** | Main thread | CSV log append (file I/O < 1ms) |
| `_collect_slow_tasks` | **5s** | `@work(thread, exclusive)` | top CPU process, ping, weather, TCP, swap |

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
| MELCHIOR | TREND | iGPU `D3D 3D + Copy + Video Codec` via OHM `hw_contains="radeon"` | Braille trend, y_range=(0,25), combined sum clamp 100 |
| MELCHIOR | VCODEC | iGPU `D3D Video Codec 0` via OHM `hw_contains="radeon"` | >1% → `CODEC` cyan bold, else `IDLE` dim |
| MELCHIOR | border flash (fuse_crit) | CPU Package power + `cpu_freq_nom` | Independent from subtitle; driven by CPU boost state |
| BALTHASAR | PCIe | OHM `GPU PCIe Rx/Tx` via `hw_contains="nvidia"` | Moved from CASPER, MB/s |
| CASPER | CODEC | pynvml `gpu_decoder_util` / `gpu_encoder_util` | >1% → DECODING/ENCODING blink 5Hz |
| CASPER | FG | OHM `D3D Optical Flow Accelerator 0` via `hw_contains="nvidia"` | >0% → FG ON (xx%) blink 3Hz, else FG OFF dim |

## Crash Recovery Log (`logs/crash_log.csv`)

- **Purpose**: Last 30 min of sensor data before abnormal shutdown (no BSOD dump)
- **Columns** (36 fields): `time,cpu_load,cpu_temp,cpu_pkg_w,cpu_eff_freq,cstate,cpu_fan,cpu_vid1~8,mem_pct,mem_temp,gpu_load,gpu_temp,gpu_mem_junc_temp,gpu_pwr,gpu_core_freq,gpu_volt,vram_pct,gpu_status,gpu_pstate,pcie_rx,pcie_tx,v3v3,vcore_v,top_proc,top_cpu,gpu_decoder_util,gpu_encoder_util,gpu_mem_util,gpu_clk_reasons`
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

## Conventions

- All comments in Chinese
- No `.env` loaded programmatically; `.env` is gitignored
- `archive/` dir holds older iterations (GPT, Gemini, DeepSeek, Qwen versions)

## Design Notes

- **MELCHIOR title `MELCHIOR | N/8 ACTV`**: Shows active core count `N/8` (7800X3D = 8 physical cores). "Active" = per-core load > 10% OR effective/nominal frequency ratio > 0.15, read from OHM `Load/CPU Core #i` (with SMT: max of thread 1+9, 2+10, ...) and `Core #i (Effective)` / `Core #i`. Color tiers: ≤1 cyan, 2~4 green, 5~6 yellow, 7~8 red1.
- **MELCHIOR TREND row**: Replaced CPU frequency braille trend with iGPU `D3D 3D + Copy + Video Codec` combined load (%) braille trend. `y_range=(0,25)` for responsive display. History window = 100 points (~20s at 0.2s interval).
- **MELCHIOR subtitle** uses `fuse_indicator` driven by CPU power + frequency, independent from iGPU state.
- **MELCHIOR PKG-W shows C-State**: `52.3 W | C0` format, matching CASPER's `TGP | P0` format. Uses original `cpu_cstate_level` from effective/nominal freq ratio.
- **CASPER TGP shows P-State**: `24.8 W | P0` format. P-State parsed from NVML `Performance State` field.
- **MAGIScanner matching is end-anchored**: `get_val()` uses regex `(?:^|\W)target$` instead of substring match to avoid `Cores (Average)` hitting `Cores (Average Effective)`, and `Core #1` hitting `Core #10`. Per-core lookup uses `get_core_freq()` with `endswith` for additional safety.
- **iGPU 共存时用 `hw_contains` 过滤**：`get_val()` 支持 `hw_contains` 参数，GPU 查询传入 `"nvidia"` 确保独显传感器不被 iGPU 同名传感器干扰。所有 GPU OHM 查询统一使用此过滤。
- **Alert thresholds are intentionally tiered**: `CPU_TEMP_CRITICAL` (70°C) controls the panel border flash (`fuse_crit`). `update_alert()` uses 75°C / 80°C for level 1 / 2 notifications (Toast notify). These serve different UI purposes and should not be unified.
- **Panel titles are plain text**: After removing the unstable Ollama monitoring feature, panel titles show simple names (MELCHIOR / BALTHASAR / CASPER).

## Completed Fixes

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
- **nvidia-smi 子进程 → pynvml 直调** — 移除两个 `subprocess.run(["nvidia-smi", ...])`，改用 pynvml 直接绑定 `nvml.dll`（无子进程，无 stdout 解析）；OHM(NVAPI) 的 GPU 传感器不动，保留崩溃时数据存活能力；`gpu_recovery_action` 替换为 `gpu_clk_reasons`（Clocks Event Reasons 原始 bitmask）
- **iGPU 共存 OHM 传感器过滤** — CPU 启用集成显卡后，OHM 同时报告 iGPU + dGPU 同名传感器（GPU Core/MHz/°C 等），`get_val()` 新增 `hw_contains` 参数，所有 GPU 查询传入 `"nvidia"` 确保读取独显数据
- **iGPU 监控（7800X3D 核显副屏）** — MELCHIOR 面板 TREND 行改用 iGPU `D3D 3D` 负载点阵（`hw_contains="radeon"`，y_range=0~70），subtitle 显示 `D3D Video Codec 0` 四档（40/25/10%）；border flash 保留 CPU 功耗/频率触发逻辑不变
- **iGPU TREND 合并三引擎** — D3D 3D + Copy + Video Codec 三合一负载点阵，y_range 降至 (0,25) 提高敏感度
- **MELCHIOR VCODEC 行** — 新增 D3D Video Codec 0 活动指示（>1% 亮 CODEC，否则 IDLE）
- **CASPER VCODEC 行** — 新增 pynvml 解码器/编码器利用率活动指示（5Hz 闪烁）
- **CASPER FG 行** — 新增 D3D Optical Flow Accelerator 0 帧生成活动指示（>0% 3Hz 闪烁带百分比）
- **PCIe 行移至 BALTHASAR** — 原 CASPER PCIe 行移至 BALTHASAR DISK 行下方，修正单位从 G → MB/s
