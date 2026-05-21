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
| `x` | Launch `opencode` |

## Conventions

- All comments in Chinese
- No `.env` loaded programmatically; `.env` is gitignored
- `archive/` dir holds older iterations (GPT, Gemini, DeepSeek, Qwen versions)

## Design Notes

- **Alert thresholds are intentionally tiered**: `CPU_TEMP_CRITICAL` (70°C) controls panel border flash; `update_alert()` uses 75°C / 80°C for level 1 / 2 notifications. These serve different UI purposes and should not be unified.

## Completed Fixes

- `build_balthasar()` state mutation in `render()` — moved `state.add_net_dn()` to `_collect()`
- `MAGIScanner.get_val()` linear search — now uses pre-lowered `list[tuple]` cache
- `_refresh_all()` repeated `self.query()` — widget references cached in `on_mount()`
- `parse_n()` missing type annotation — added `v_str: str | None`
- `generate_braille_trend()` double `min/max` — replaced with single-pass loop
- Panel title/subtitle string rebuild — extracted as module constants
- Comment language unified to Chinese
