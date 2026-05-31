# MAGI Monitor — Agent Notes

## Run

```
python magi_monitor_MIX.py
```

## Architecture

- Single-file Textual TUI app (`magi_monitor_MIX.py`), entry point: `MAGIApp().run()`
- Reads hardware sensors from **OpenHardwareMonitor / LibreHardwareMonitor** JSON API at `http://localhost:8085/data.json` via `MAGIScanner`
- Requires `psutil` and `requests` (no `pyproject.toml` / `requirements.txt` — install manually)
- No test suite, no lint/typecheck config

## Threading Model

- `_collect()` runs in a background thread (`@work(thread=True, exclusive=True)`) every 0.2s — does blocking HTTP to OHM + psutil calls
- `_collect_slow_tasks()` runs every 5s in a separate thread — ping, weather (wttr.in), TCP counts
- `_tick()` runs on the main event loop — handles alerts, blink state, and calls `_refresh_all()`
- **State (`MagiState`) is shared between threads without locks** on scalar fields. Only the freq history lists are protected by `_list_lock`. When modifying state fields, be aware of potential data races.

## Known Issues to Fix

1. **`refresh_disk_speed()` crashes if `psutil.disk_io_counters()` returns `None`** — add a guard.
2. **`update_tcp_counts()` swallows all exceptions silently** — add logging.

## Key Bindings

| Key | Action |
|-----|--------|
| `m` | Launch `pstop` (via `self.suspend()`) |
| `n` | Launch `psnet` |
| `t` | Launch `yazi f:\` |
| `x` | Launch `opencode D:\tools` |

## Conventions

- All comments in Chinese
- No `.env` loaded programmatically; `.env` is gitignored
- `archive/` dir holds older iterations (GPT, Gemini, DeepSeek, Qwen versions)

## Design Notes

- **Alert thresholds are intentionally tiered**: `CPU_TEMP_CRITICAL` (70°C) controls panel border flash; `update_alert()` uses 75°C / 80°C for level 1 / 2 notifications. These serve different UI purposes and should not be unified.
- **`/api/ps` debounce**: Empty responses are debounced (2 consecutive needed) before transitioning to STBY, preventing transient Ollama pauses from resetting derived data. A `_prev_ai_family` tracker ensures derived data is only cleared on real loaded→STBY/OFFLINE transitions.
- **MODEL/REQ/OFFLOAD are now in panel titles**: Status is embedded in `title=` (top border), replacing former table rows. `subtitle=` (bottom border) unchanged (fuse/power/GPU indicators).
- **Fallback states in title**: When `ai_family` indicates a loaded model but log-scan-derived data hasn't arrived yet, Casper shows `LOADING...` and Balthasar shows `IDLE` instead of `STBY`, avoiding false idle display.

## AI State Display

- **MELCHIOR** `MODEL` (panel title): three-state — `[bold #00ff00]family quant[/]` (model loaded), `[#9c0f0f]STBY[/]` (idle), `[dim]OFFLINE[/]` (Ollama unreachable)
- **BALTHASAR** `REQ` (panel title): four-state — `[bold #00ff00]N req | last XX ago[/]` (requests recorded), `[bold green]IDLE[/]` (model loaded, no requests yet), `[#9c0f0f]STBY[/]` (no model), `[dim]OFFLINE[/]` (Ollama unreachable)
- **CASPER** `OFFLOAD` (panel title): four-state — `[bold #00ff00]N/M layers to GPU[/]` (offloading), `[bold green]LOADING...[/]` (model loaded, waiting for log data), `[#9c0f0f]STBY[/]` (no model), `[dim]OFFLINE[/]` (Ollama unreachable)

## Completed Fixes

- `build_balthasar()` state mutation in `render()` — moved `state.add_net_dn()` to `_collect()`
- `MAGIScanner.get_val()` linear search — now uses pre-lowered `list[tuple]` cache
- `_refresh_all()` repeated `self.query()` — widget references cached in `on_mount()`
- `parse_n()` missing type annotation — added `v_str: str | None`
- `generate_braille_trend()` double `min/max` — replaced with single-pass loop
- Panel title/subtitle string rebuild — extracted as module constants
- Comment language unified to Chinese
- `build_casper()` / `build_balthasar()` — added `ai_family == "OFFLINE"` guard before other conditions in OFFLOAD / REQ items
- Cleanup block — moved `ai_req_count` reset out of per-cycle cleanup to STBY/OFFLINE assignment sites, preventing count loss during transient `/api/ps` empty results
- MODEL / REQ / OFFLOAD loaded-state color — `#00EEEE` → `bold #00ff00` for better visibility
- **AI state display bug**: When `/api/ps` transiently returns empty (Ollama brief pause), Balthasar and Casper now correctly show loaded state instead of STBY. The derived data (`ai_offload_gpu/total`, `ai_req_count`) is no longer reset on transient empty responses. Fallback states added for model loaded but log scanner not yet caught up (LOADING... / IDLE).
- **MODEL/REQ/OFFLOAD → panel title**: Moved from table row to `title=` (top border) of each Panel, using format `MAGI-0N | [status]`. Removed the MODEL/REQ/OFFLOAD rows from table body, freeing vertical space.
- **`/api/ps` debounce + transition tracking**: Added `_ai_empty_count` counter (requires 2 consecutive empty responses before STBY) and `_prev_ai_family` tracker to only reset derived data on real loaded→unloaded transitions.
