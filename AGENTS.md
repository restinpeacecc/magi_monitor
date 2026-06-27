# MAGI Monitor — Agent Notes

## Run

```
python magi_monitor_MIX.py
```

## Architecture

- Single-file Textual TUI app (`magi_monitor_MIX.py`), entry point: `MAGIApp().run()`
- Reads hardware sensors from **OpenHardwareMonitor / LibreHardwareMonitor** JSON API at `http://localhost:8085/data.json` via `MAGIScanner`
- GPU status via `nvidia-smi -q -d PERFORMANCE` (Clocks Event Reasons → IDLE/PWR/HOT/BOOST), polled every 1s
- Requires `psutil` and `requests` (no `pyproject.toml` / `requirements.txt` — install manually)
- No test suite, no lint/typecheck config

## Four-Tier Timer Model

| Timer | Period | Worker | Tasks |
|-------|--------|--------|-------|
| `_tick` | **0.2s** | `@work(thread, exclusive)` | OHM sensor polling, psutil, freq history, alerts |
| `_collect_gpu` | **1s** | `@work(thread, exclusive)` | nvidia-smi GPU status (`-q -d PERFORMANCE`) + diagnostics (decoder/encoder/mem util) |
| `_log_tick` | **1s** | Main thread | CSV log append (file I/O < 1ms) |
| `_collect_slow_tasks` | **5s** | `@work(thread, exclusive)` | top CPU process, ping, weather, TCP, swap |

- **State (`MagiState`) is shared between threads without locks** on scalar fields. Only the freq history lists are protected by `_list_lock`. When modifying state fields, be aware of potential data races.

## Panel Titles

| Panel | Title Format | Example | Source |
|-------|-------------|---------|--------|
| MELCHIOR (CPU) | `MELCHIOR \| N/8 ACTV` | `MELCHIOR \| 6/8 ACTV` | Active core count (load>10% OR freq ratio>0.15), color-coded |
| BALTHASAR (System) | `BALTHASAR \| {name} {cpu}%` | `BALTHASAR \| chrome 23%` | Top CPU-consuming process (`.exe` stripped, 10 char trunc) |
| CASPER (GPU) | `CASPER \| {status}` | `CASPER \| STBY` | nvidia-smi Clocks Event Reasons → IDLE/STBY/BOOST/PWR/HOT; P-state from `Performance State` field |

## Crash Recovery Log (`logs/crash_log.csv`)

- **Purpose**: Last 30 min of sensor data before abnormal shutdown (no BSOD dump)
- **Columns** (32 fields): `time,cpu_load,cpu_temp,cpu_pkg_w,cpu_eff_freq,cstate,cpu_fan,cpu_vid1~8,mem_pct,mem_temp,gpu_load,gpu_temp,gpu_mem_junc_temp,gpu_pwr,gpu_core_freq,gpu_volt,vram_pct,gpu_status,pcie_rx,pcie_tx,v3v3,vcore_v,top_proc,top_cpu,gpu_decoder_util,gpu_encoder_util,gpu_mem_util,gpu_recovery_action`
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
- **MELCHIOR subtitle shows power+freq tier**: `CRITICAL` (red flash 2.5Hz, triggers border flash), `WARN` (gold, 1Hz), `ATTN` (green, 0.5Hz), `STBL` (cyan, reverse, no blink). Derived from `current_cpu_power` (Package) and `cpu_freq_nom` (Cores Average). Replaced original C-State group indicator.
- **MELCHIOR PKG-W shows C-State**: `52.3 W | C0` format, matching CASPER's `TGP | P0` format. Uses original `cpu_cstate_level` from effective/nominal freq ratio.
- **CASPER TGP shows P-State**: `24.8 W | P0` format. P-State parsed from nvidia-smi `Performance State` field.
- **MAGIScanner matching is end-anchored**: `get_val()` uses regex `(?:^|\W)target$` instead of substring match to avoid `Cores (Average)` hitting `Cores (Average Effective)`, and `Core #1` hitting `Core #10`. Per-core lookup uses `get_core_freq()` with `endswith` for additional safety.
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
