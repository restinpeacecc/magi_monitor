#!/usr/bin/env python3
"""
MAGI SYSTEM Monitor — Textual Edition
MAGI 系统监控器 — Textual 版（异步事件循环、线程工作器、CSS 布局）
"""

import re
import subprocess
import time
import threading
from datetime import datetime

import psutil
import requests
from rich.panel import Panel
from rich.table import Table
from rich.box import HEAVY            # ← 新增：粗边框
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static, Label
from textual.screen import Screen

# ══════════════════════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════════════════════

BASE_POWER_OFFSET = 45.0            # 主板/风扇/SSD 等基础功耗 (W)

CPU_TEMP_CAUTION    = 50           # °C
CPU_TEMP_WARNING    = 60
CPU_TEMP_CRITICAL   = 70           # °C（保持一致，与1级警报阈值相同）

POWER_SAFE  = 100                  # W
POWER_WARN  = 180
POWER_CRIT  = 300

GPU_LOAD_HIGH   = 60               # %
VRAM_USED_HIGH  = 50               # %

CPU_FREQ_MIN = 3000.0              # MHz（Braille 曲线 Y 轴下限）
CPU_FREQ_MAX = 5000.0              # MHz（Braille 曲线 Y 轴上限）


# Ollama 日志路径
OLLAMA_LOG_PATH = r"C:\Users\kugim\AppData\Local\Ollama\server.log"

# ══════════════════════════════════════════════════════════════════════════════
#  状态
# ══════════════════════════════════════════════════════════════════════════════

class MagiState:
    
    CPU_FREQ_HISTORY_MAX = 1500   # 覆盖约 5 分钟（5 点/秒）
    GPU_FREQ_HISTORY_MAX = 1500   # 与 CPU 统一
    
    def __init__(self):
        # CPU
        self.cpu_load = 0.0
        self.cpu_freq_history: list[float] = []
        self.avg_volt = 0.0
        self.current_cpu_power = 0.0
        self.cpu_temp = 0.0
        self.cpu_fan = "0"

        # GPU
        self.gpu_load = 0.0
        self.gpu_freq_history: list[float] = []
        self.vram_used_pct = 0.0
        self.gpu_volt = 0.0
        self.current_gpu_power = 0.0
        self.gpu_temp = 0.0
        self.gpu_fan = "0"

        # 其他
        self.used_p = 0.0
        self.used_gb = "0 GB"
        self.avail_gb = "0 GB"
        self.net_dn_raw = "0 KB/s"
        self.max_net_dn_kbps: float = 0.0
        self.boot_time: float      = psutil.boot_time()
        self.weather: str          = "LOADING..."
        self.last_weather_update: float = 0.0

        self.alert_level = 0           # 0:正常, 1:警告, 2:危险
        self.last_alert_time = 0.0     # 防止重复通知

        self.tcp_established = 0
        self.tcp_timewait    = 0
        self._last_tcp_update = 0.0

        # 磁盘速度按周期缓存（与 render() 分离，避免多次调用）
        self._last_disk_io = psutil.disk_io_counters()
        self._last_time: float = time.time()
        self.disk_r: float = 0.0
        self.disk_w: float = 0.0
        self.ping_ms: float = 0.0
        
        # 新增：面板临界状态和闪烁状态
        self.fuse_crit: bool = False
        self.pstat_crit: bool = False
        self.comp_crit: bool = False
        self.fuse_blink_on: bool = False
        self.pstat_blink_on: bool = False
        self.comp_blink_on: bool = False
        
        # Ollama AI
        self.ai_family: str = "—"           # 模型家族 e.g. "gemma4"
        self.ai_quant: str = "—"            # 量化等级 e.g. "Q4_K_M"
        self.ai_offload_pct: float = 0.0    # GPU 卸载百分比
        self.ai_offload_gpu: int = 0        # 卸载到 GPU 的层数
        self.ai_offload_total: int = 0      # 模型总层数
        self.ai_req_count: int = 0          # 累计推理请求数（从 GIN 日志解析）
        self.ai_last_req_ts: float = 0.0    # 最近一次推理请求的时间戳
        self._prev_ai_family: str = ""      # 前一次 ai_family 值，用于检测转换
        self._ai_empty_count: int = 0       # 空响应计数器（用于消除 /api/ps 瞬时空响应）
        
        # 新增：保护历史列表的锁
        self._list_lock = threading.Lock()
        
    # ── 历史列表快照方法（线程安全）──────────────────────────────

    def get_cpu_freq_snapshot(self, maxlen: int = 0) -> list[float]:
        with self._list_lock:
            if maxlen:
                return list(self.cpu_freq_history[-maxlen:])
            return list(self.cpu_freq_history)

    def get_gpu_freq_snapshot(self, maxlen: int = 0) -> list[float]:
        with self._list_lock:
            if maxlen:
                return list(self.gpu_freq_history[-maxlen:])
            return list(self.gpu_freq_history)

    # ── 警报 ────────────────────────────────────────────────────────────────

    def update_alert(self, cpu_temp: float, gpu_temp: float):
        """根据温度确定警报等级，返回 (原等级, 新等级)"""
        old_level = self.alert_level
        if cpu_temp >= 80 and gpu_temp >= 80:     # 自定义危险阈值（2级警报）
            new_level = 2
        elif cpu_temp >= 75 and gpu_temp >= 75:   # 1级警报
            new_level = 1
        else:
            new_level = 0
        self.alert_level = new_level
        return old_level, new_level
    
    # ── 运行时间 ────────────────────────────────────────────────────────────────

    def get_uptime_str(self) -> str:
        sec = time.time() - self.boot_time
        d, r = divmod(sec, 86400)
        h, r = divmod(r, 3600)
        m, _ = divmod(r, 60)
        return f"{int(d)}d {int(h)}h" if d > 0 else f"{int(h)}h {int(m)}m"

    # ── TCP ────────────────────────────────────────────────────────────────

    def update_tcp_counts(self):
        now = time.time()
        if now - self._last_tcp_update < 5:
            return
        self._last_tcp_update = now
        
        try:
            established = 0
            timewait = 0
            
            # kind='tcp' 会同时获取 IPv4 和 IPv6 的 TCP 连接
            connections = psutil.net_connections(kind='tcp')
            
            for conn in connections:
                if conn.status == psutil.CONN_ESTABLISHED:
                    established += 1
                elif conn.status == psutil.CONN_TIME_WAIT:
                    timewait += 1
                    
            self.tcp_established = established
            self.tcp_timewait = timewait
            
        except psutil.AccessDenied:
            # 如果遇到权限问题，可以忽略，或者在这里加个标记
            pass
        except Exception:
            pass
        
    # ── 历史记录 ───────────────────────────────────────

    def add_cpu_freq(self, val: float):
        """由 worker 线程调用，追加频率并保持长度"""
        with self._list_lock:
            self.cpu_freq_history.append(val)
            if len(self.cpu_freq_history) > self.CPU_FREQ_HISTORY_MAX:
                self.cpu_freq_history = self.cpu_freq_history[-self.CPU_FREQ_HISTORY_MAX:]

    def add_gpu_freq(self, val: float):
        with self._list_lock:
            self.gpu_freq_history.append(val)
            if len(self.gpu_freq_history) > self.GPU_FREQ_HISTORY_MAX:
                self.gpu_freq_history = self.gpu_freq_history[-self.GPU_FREQ_HISTORY_MAX:]

    def add_net_dn(self, kbps: float):
        """记录下载速度并更新全局最大值"""
        if kbps > self.max_net_dn_kbps:
            self.max_net_dn_kbps = kbps
        
    def get_max_net_dn_kbps(self) -> float:
        return self.max_net_dn_kbps
    
    # ── 磁盘速度（仅由工作线程调用一次）────────────────────────

    def refresh_disk_speed(self):
        now_io   = psutil.disk_io_counters()
        now_time = time.time()
        dt = now_time - self._last_time
        if dt > 0:
            self.disk_r = (now_io.read_bytes  - self._last_disk_io.read_bytes)  / dt / 1024 / 1024
            self.disk_w = (now_io.write_bytes - self._last_disk_io.write_bytes) / dt / 1024 / 1024
        else:
            self.disk_r = 0.0
            self.disk_w = 0.0
        self._last_disk_io = now_io
        self._last_time    = now_time

    # ── 天气（每30分钟更新一次）──────────────────────────────────────────────────

    def update_weather(self):
        if time.time() - self.last_weather_update > 1800:
            try:
                res = requests.get("https://wttr.in/?format=3", timeout=1)
                self.weather = res.text.strip()
                self.last_weather_update = time.time()
            except Exception:
                self.weather = "OFFLINE"

    # ── PING──────────────────────────────────────────────────

    def update_ping(self, target: str = "8.8.8.8", timeout: int = 1):
        """执行一次 ping 并提取平均延迟（ms）"""
        try:
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), target]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 1)
            # 匹配 "平均 = 12ms" 或 "Average = 12ms"（中文/英文系统）
            match = re.search(r"(?:平均|Average)\s*=\s*(\d+)", result.stdout)
            if match:
                self.ping_ms = float(match.group(1))
            else:
                self.ping_ms = -1.0   # 解析失败
        except Exception:
            self.ping_ms = -2.0        # 网络错误或超时

# ══════════════════════════════════════════════════════════════════════════════
#  Scanner（OpenHardwareMonitor / LibreHardwareMonitor JSON API）
# ══════════════════════════════════════════════════════════════════════════════

class MAGIScanner:
    def __init__(self):
        self.url = "http://localhost:8085/data.json"
        # 预计算的小写名称缓存，避免每次 get_val 重复调用 .lower()
        self._cache: list[tuple[str, str]] = []

    def update(self):
        try:
            raw = requests.get(self.url, timeout=0.3).json()
            self._cache = []
            self._walk(raw)
        except Exception:
            self._cache = []

    def _walk(self, node, hw: str = ""):
        if "HardwareId" in node:
            hw = node.get("Text", "")
        if node.get("Value"):
            name = f"{hw} {node.get('Text', '')}"
            val = str(node.get("Value", ""))
            self._cache.append((name.lower(), val))
        for child in node.get("Children", []):
            self._walk(child, hw)

    def get_val(self, name_target: str, unit_target: str | None = None) -> str | None:
        nl = name_target.lower()
        for name_lower, val in self._cache:
            if nl in name_lower:
                if unit_target is None or unit_target.lower() in val.lower():
                    return val
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  模块级单例
# ══════════════════════════════════════════════════════════════════════════════

state   = MagiState()
scanner = MAGIScanner()


# ══════════════════════════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def parse_n(v_str: str | None) -> float:
    if not v_str:
        return 0.0
    try:
        return float(str(v_str).replace(",", "").split()[0])
    except Exception:
        return 0.0


def generate_bar(percent, width: int = 15, color: str = "green") -> str:
    p      = max(0.0, min(100.0, float(percent)))
    filled = int(p / 100 * width)
    return f"[{color}]{'█' * filled}[/][dim]{'░' * (width - filled)}[/] {p:>5.1f}%"


def get_temp_color(temp_val) -> str:
    val = float(temp_val)
    if val < 45:
        return "spring_green1"
    if val < 68:
        return "yellow"
    return "red1"

def get_status_theme(value, safe_limit, warn_limit, crit_limit) -> tuple:
    val = float(value)
    if val >= crit_limit:
        return "bold red1",      2.5,   "CRITICAL"
    if val >= warn_limit:
        return "bold gold1",     1,   "WARNING"
    if val >= safe_limit:
        return "bold green",     0.5, "CAUTION"
    return "cyan",               0,   "[reverse] STABLE [/reverse]"

def get_power_theme(value_str, safe_limit, warn_limit, crit_limit) -> tuple:
    val = float(value_str)
    # 返回格式: (颜色, 闪烁频率, 显示文本)
    if val >= crit_limit:
        return "bold red1",      2.5,  "OVERDRIVE"
    if val >= warn_limit:
        return "bold gold1",     1,  "HIGH-LOAD"
    if val >= safe_limit:
        return "bold green",     0.5,  "ACTIVE"
    return "cyan",               0,    "[reverse] ECO [/reverse]"

def blink_markup(text: str, color: str, freq: float) -> str:
    """基于 time.time() 的点灭标记（每次 render() 调用时重新计算）"""
    if freq <= 0:
        return f"[{color}] {text} [/]"
    on = (time.time() * freq * 2) % 2 < 1
    return f"[{color} reverse] {text} [/]" if on else f"[{color}] {text} [/]"

def generate_braille_trend(values: list[float], width: int = 22, 
                           y_range: tuple[float, float] | None = None,
                           low_color: str = "cyan", mid_color: str = "yellow", high_color: str = "red1") -> str:
    if len(values) < 2:
        return "[dim]collecting...[/]"
    total_pts = width * 2
    if len(values) > total_pts:
        step = len(values) / total_pts
        data = [values[int(i * step)] for i in range(total_pts)]
    else:
        data = values[-total_pts:]
        
    if y_range:
        vmin, vmax = y_range
    else:
        vmin = vmax = data[0]
        for v in data[1:]:
            if v < vmin: vmin = v
            elif v > vmax: vmax = v
    if abs(vmax - vmin) < 1e-6:
        vmin, vmax = vmax - 1, vmax + 1
        
    segments = []
    for i in range(0, len(data) - 1, 2):
        v1, v2 = data[i], data[i+1]
        h1 = max(0, min(4, (v1 - vmin) / (vmax - vmin) * 4.0))
        h2 = max(0, min(4, (v2 - vmin) / (vmax - vmin) * 4.0))
        
        # ✅ 关键修改：初始化带左下基点 ⢀ (U+2880)，避免 0% 时渲染为透明空格
        code = 0x2880  
        for h, shift in [(h1, 0), (h2, 3)]:
            if h >= 1: code |= (0x01 << shift)
            if h >= 2: code |= (0x02 << shift)
            if h >= 3: code |= (0x04 << shift)
            
        ratio = ((v1 + v2) / 2 - vmin) / (vmax - vmin)
        color = high_color if ratio > 0.7 else (low_color if ratio < 0.3 else mid_color)
        segments.append(f"[{color}]{chr(code)}[/]")
    return "".join(segments)

def get_trend_arrow(values: list[float], threshold: float = 20) -> str:
    """根据最近25个点与前25个点的平均值比较，返回方向符号"""
    if len(values) < 25:
        return "[yellow]►[/]"
    recent = values[-25:]
    older  = values[-50:-25]
    avg_recent = sum(recent) / len(recent)
    avg_older  = sum(older) / len(older) if older else avg_recent
    if avg_recent > avg_older + threshold:
        return "[green]▲[/]"
    elif avg_recent < avg_older - threshold:
        return "[red]▼[/]"
    else:
        return "[yellow]►[/]"

# ══════════════════════════════════════════════════════════════════════════════
#  面板构建器（返回 Rich 可渲染对象）
# ══════════════════════════════════════════════════════════════════════════════

def build_header() -> Panel:
    now = datetime.now().strftime("%H:%M:%S")
    uptime = state.get_uptime_str()
    # 将 Uptime 整合进 Header
    txt = (
        f"[bold green]MAGI SYSTEM[/] [dim]||[/] {now} "
        f"[dim]||[/] [bold red]UP: {uptime}[/] [dim]||[/] "
        f"{state.weather} [dim]||[/] [bold green]SYNC: 100%[/]"
    )
    return Panel(txt, border_style="orange3")


def build_melchior() -> Panel:

    cpu_snapshot = state.get_cpu_freq_snapshot(state.CPU_FREQ_HISTORY_MAX)

    spark = generate_braille_trend(
        cpu_snapshot, 
        width=22, 
        y_range=(CPU_FREQ_MIN, CPU_FREQ_MAX),
        low_color="cyan", 
        mid_color="yellow", 
        high_color="red1"
    )

    history = cpu_snapshot   # 改用线程安全的快照
    if history:
        f_min = min(history)
        f_max = max(history)
        f_now = history[-1]
        arrow = get_trend_arrow(history)
        # 组装文本
        freq_str = (
            f"[dim]{f_min:.0f}[/] "
            f"[bold gold1]{f_now:.0f} MHz {arrow}[/] "
            f"[dim]{f_max:.0f}[/]"
        )
    else:
        freq_str = "[dim]collecting...[/]"
        
    color, freq, status_text = get_status_theme(
        state.cpu_temp, CPU_TEMP_CAUTION, CPU_TEMP_WARNING, CPU_TEMP_CRITICAL
    )

    fuse_indicator = blink_markup(status_text, color, freq)

    t = Table.grid(padding=0)
    t.add_column(width=10)
    t.add_row("LOAD",   generate_bar(state.cpu_load, color="orange3"))
    t.add_row("FREQ",  freq_str)
    t.add_row("TREND",  spark)
    t.add_row("V-AVG",  f"[cadet_blue]{state.avg_volt:.4f} V[/]")
    t.add_row("PKG-W",  f"[#4169E1]{state.current_cpu_power:.1f} W[/]")
    t.add_row("TEMP",   f"[bold {get_temp_color(state.cpu_temp)}]{state.cpu_temp:.0f} °C[/]")
    t.add_row("FAN ",   f"[indian_red1]{state.cpu_fan or 'OFFLINE'}[/]")
    if state.ai_family == "OFFLINE":
        model_status = "[dim]OFFLINE[/]"
    elif state.ai_family == "STBY":
        model_status = "[#9c0f0f]STBY[/]"
    else:
        model_status = f"[bold green]{state.ai_family}  {state.ai_quant}[/]"
    mel_title = f"[bold orange3]MAGI-01[/] | {model_status}"
    
    # ── 边框逻辑直接在这里决定（不再在 Widget.render() 中后改） ──
    flash = state.fuse_crit and state.fuse_blink_on
    border = "bold red" if flash else "orange3"          # 加粗红色

    # 构建 Panel，仅在 flash 为 True 时传入 box=HEAVY
    panel_kwargs = dict(
        renderable=t,
        title=mel_title,
        border_style=border,
        subtitle=fuse_indicator,
    )
    if flash:
        panel_kwargs['box'] = HEAVY
    return Panel(**panel_kwargs)


def build_balthasar() -> Panel:
    
    net_dn_raw = state.net_dn_raw
    
    dn_match = re.search(r"([0-9,.]+)\s*(KB|MB|GB)/s?", net_dn_raw, re.IGNORECASE)
    dn_kbps = 0.0
    if dn_match:
        num = float(dn_match.group(1).replace(",", ""))
        unit = dn_match.group(2).upper()
        if unit == "GB":
            dn_kbps = num * 1024 * 1024
        elif unit == "MB":
            dn_kbps = num * 1024
        else:
            dn_kbps = num

    # 当前速度显示（保持原始字符串）
    net_cur = net_dn_raw

    # 历史最大（格式化，单位统一）
    max_kbps = state.get_max_net_dn_kbps()
    if max_kbps > 0:
        if max_kbps >= 1024 * 1024:
            max_str = f"{max_kbps / 1024 / 1024:.1f} GB/s"
        elif max_kbps >= 1024:
            max_str = f"{max_kbps / 1024:.1f} MB/s"
        else:
            max_str = f"{max_kbps:.0f} KB/s"
        net_display = f"[yellow]▼{net_cur}[/]@[dim]{max_str}[/]"
    else:
        net_display = f"[yellow]▼{net_cur}[/]"
    
    p = state.ping_ms
    if p == -2.0:
        ping_str = "[bold yellow]TIMEOUT[/]"
    elif p == -1.0:
        ping_str = "[bold #DC143C]PARSE ERR[/]"
    else:
        color = "cyan" if p < 30 else "yellow" if p < 80 else "red"
        ping_str = f"[{color}]{p:.0f} ms[/]"

    tcp_str = f"[#7CFC00]EST:{state.tcp_established}[/] [dim]|[/] [cadet_blue]TW:{state.tcp_timewait}[/]"

    # 整机估算功耗
    total_pwr = state.current_cpu_power + state.current_gpu_power + BASE_POWER_OFFSET
    
    # 使用定制的功耗状态灯
    p_color, p_freq, p_text = get_power_theme(total_pwr, POWER_SAFE, POWER_WARN, POWER_CRIT)

    t = Table.grid(padding=0)
    t.add_column(width=10)
    t.add_row("MEMORY", generate_bar(state.used_p, color="bright_blue"))
    t.add_row("USED",   f"[bold red]{state.used_gb or 'N/A'}[/]")
    t.add_row("FREE",   f"[bold green]{state.avail_gb or 'N/A'}[/]")
    t.add_row("NET-DN", net_display) 
    t.add_row("PING",   ping_str)
    t.add_row("TCP",    tcp_str) 
    t.add_row("DISK",   f"[indian_red1]R:{state.disk_r:.1f} W:{state.disk_w:.1f} MB/s[/]")
    if state.ai_family == "OFFLINE":
        req_status = "[dim]OFFLINE[/]"
    elif state.ai_family == "STBY":
        req_status = "[#9c0f0f]STBY[/]"
    elif state.ai_req_count > 0:
        ago = time.time() - state.ai_last_req_ts
        ago_str = f"{ago:.0f}s ago" if ago < 120 else f"{ago/60:.0f}m ago"
        req_status = f"[bold green]{state.ai_req_count} req | last {ago_str}[/]"
    else:
        req_status = "[bold green]IDLE[/]"
    bal_title = f"[bold orange3]MAGI-02[/] | {req_status}"
    
    # ── 边框逻辑直接在这里决定 ──
    flash = state.pstat_crit and state.pstat_blink_on
    border = "bold red" if flash else "orange3"

    panel_kwargs = dict(
        renderable=t,
        title=bal_title,
        border_style=border,
        subtitle=blink_markup(p_text, p_color, p_freq),
    )
    if flash:
        panel_kwargs['box'] = HEAVY
    return Panel(**panel_kwargs)


def build_casper() -> Panel:
    
    gpu_snapshot = state.get_gpu_freq_snapshot(state.GPU_FREQ_HISTORY_MAX)

    if state.gpu_load >= GPU_LOAD_HIGH and state.vram_used_pct >= VRAM_USED_HIGH:                        # 最高优先级：满负荷
        on = (time.time() * 5) % 2 < 1                  # 2.5 Hz
        ai = "[bold red1][reverse] RTX-ON [/reverse][/]" if on else "[bold red1] RTX-ON [/]"
    elif state.gpu_load >= 30 and state.vram_used_pct >= 30:                      # 中负荷
        on = (time.time() * 2) % 2 < 1                  # 1 Hz
        ai = "[bold gold1][reverse] ENGAGED [/reverse][/]" if on else "[bold gold1] ENGAGED [/]"
    elif state.gpu_load >= 10:                                     # 低负荷（仅 GPU 核心有活动）
        on = (time.time() * 1) % 2 < 1                  # 0.5 Hz
        ai = "[bold green][reverse] INIT [/reverse][/]" if on else "[bold green] INIT [/]"
    else:
        ai = "[cyan][reverse] IDLE [/reverse][/]"       # 空闲

    history = gpu_snapshot   # 改用线程安全的快照
    if history and len(history) >= 2:
        f_min = min(history)
        f_max = max(history)
        f_now = history[-1]
        arrow = get_trend_arrow(history)
        freq_display = f"[dim]{f_min:.0f}[/] [bold gold1]{f_now:.0f} MHz {arrow}[/] [dim]{f_max:.0f}[/]"
    else:
        freq_display = f"[bold gold1]{'N/A'}[/]"   # 数据不足时回退原始显示
    
    t = Table.grid(padding=0)
    t.add_column(width=10)
    t.add_row("LOAD",   generate_bar(state.gpu_load, color="red"))
    t.add_row("FREQ",   freq_display)          # 改用趋势显示
    t.add_row("VRAM",   generate_bar(state.vram_used_pct, color="magenta"))
    t.add_row("VCORE",  f"[cadet_blue]{state.gpu_volt or 'N/A'}[/]")
    t.add_row("TGP",  f"[#4169E1]{state.current_gpu_power:.1f} W[/]")
    t.add_row("TEMP",   f"[bold {get_temp_color(state.gpu_temp)}]{state.gpu_temp:.0f} °C[/]")
    t.add_row("FAN ",   f"[indian_red1]{state.gpu_fan or 'N/A'}[/]")
    if state.ai_family == "OFFLINE":
        offload_status = "[dim]OFFLINE[/]"
    elif state.ai_family == "STBY":
        offload_status = "[#9c0f0f]STBY[/]"
    elif state.ai_offload_total > 0:
        offload_status = f"[bold green]{state.ai_offload_gpu}/{state.ai_offload_total} layers to GPU[/]"
    else:
        offload_status = "[bold green]LOADING...[/]"
    cas_title = f"[bold orange3]MAGI-03[/] | {offload_status}"
    
    # ── 边框逻辑直接在这里决定 ──
    flash = state.comp_crit and state.comp_blink_on
    border = "bold red" if flash else "orange3"

    panel_kwargs = dict(
        renderable=t,
        title=cas_title,
        border_style=border,
        subtitle=ai,
    )
    if flash:
        panel_kwargs['box'] = HEAVY
    return Panel(**panel_kwargs)


# ══════════════════════════════════════════════════════════════════════════════
#  Textual 组件（render() 返回 Rich Panel）
# ══════════════════════════════════════════════════════════════════════════════

class MAGIHeader(Static):
    def render(self) -> Panel:
        return build_header()


class MelchiorPanel(Static):
    def render(self) -> Panel:
        return build_melchior()


class BalthasarPanel(Static):
    def render(self) -> Panel:
        return build_balthasar()


class CasperPanel(Static):
    def render(self) -> Panel:
        return build_casper()


# ══════════════════════════════════════════════════════════════════════════════
#  启动画面
# ══════════════════════════════════════════════════════════════════════════════

class SplashScreen(Screen):
    """逐行显示的启动动画"""

    CSS = """
    SplashScreen {
        align: center middle;
        background: #0a0a0a;
    }

    #splash-container {
        width: auto;
        height: auto;
    }

    .splash-line {
        width: auto;
        height: auto;
        content-align: center middle;
        text-style: bold;
        opacity: 0;                     /* 初始全部隐藏 */
        margin: 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="splash-container"):
            yield Label("[bold orange]MAGI-01: MELCHIOR ... [bold green]ONLINE",        classes="splash-line")
            yield Label("[bold orange]MAGI-02: BALTHASAR .. [bold green]ONLINE",        classes="splash-line")
            yield Label("[bold orange]MAGI-03: CASPER ..... [bold green]ONLINE",        classes="splash-line")
            yield Label("[bold green]ALL SYSTEMS NOMINAL — INITIALIZING SYNCHRONIZATION", classes="splash-line")

    def on_mount(self) -> None:
        self.lines = list(self.query(".splash-line"))   # 按顺序拿到四个 Label
        self._show_next_line(0)

    def _show_next_line(self, index: int) -> None:
        """每隔 0.6 秒显示一行，最后一行显示后再等 1.5 秒关闭"""
        if index < len(self.lines):
            # 当前行淡入
            self.lines[index].styles.opacity = 1.0
            # 安排下一行
            self.set_timer(0.6, lambda: self._show_next_line(index + 1))
        else:
            # 所有行显示完毕，等待 1.5 秒后关闭
            self.set_timer(1.5, self.dismiss_splash)

    def dismiss_splash(self) -> None:
        self.app.pop_screen()
        
# ══════════════════════════════════════════════════════════════════════════════
#  主应用
# ══════════════════════════════════════════════════════════════════════════════

class MAGIApp(App):

    CSS = """
    Screen {
        background: transparent;
    }

    MAGIHeader {
        height: 3;
        dock: top;
        background: transparent;
    }

    #panels {
        layout: horizontal;
        height: 1fr;
        background: transparent;
    }

    MelchiorPanel,
    BalthasarPanel,
    CasperPanel {
        width: 1fr;
        height: 100%;
        background: transparent;
    }
    """
    
    theme = "ansi-dark"
    
    BINDINGS = [
        Binding("m", "launch_pstop", "pstop", show=True),
        Binding("n", "launch_psnet", "psnet", show=True),
        Binding("t", "launch_yazi", "yazi", show=True),
        Binding("x", "launch_opencode", "opencode", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield MAGIHeader()
        with Horizontal(id="panels"):
            yield MelchiorPanel(id="melchior_panel")
            yield BalthasarPanel(id="balthasar_panel")
            yield CasperPanel(id="casper_panel")
        yield Footer()

    def on_mount(self) -> None:
        # 先显示启动动画屏幕
        self.push_screen(SplashScreen())
        # 缓存面板引用，避免 _refresh_all 中重复 query
        self._refresh_widgets = [
            self.query_one(MAGIHeader),
            self.query_one(MelchiorPanel),
            self.query_one(BalthasarPanel),
            self.query_one(CasperPanel),
        ]
        self.set_interval(0.2, self._tick)     # 高频：传感器
        self.set_interval(5.0, self._collect_slow_tasks) # 低频：Ping/TCP
        self._last_ollama_ps_time = 0.0
        self._last_log_scan_time = 0.0
        self._log_last_pos = 0  # 日志增量读取位置

    # ── 更新循环 ──────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """事件循环中频繁调用的轻量级调度器。实际处理在worker线程中。"""
        self._collect()

        # 在主线程中处理 alert 和刷新（替代 worker 中的 call_from_thread）
        self._check_alert(state.cpu_temp, state.gpu_temp)
        self._refresh_all()

        # 处理面板边框闪烁逻辑 (2.5 Hz)
        current_time = time.time()
        blink_state = (int(current_time * 5) % 2 == 0)

        state.fuse_blink_on = blink_state if state.fuse_crit else False
        state.pstat_blink_on = blink_state if state.pstat_crit else False
        state.comp_blink_on = blink_state if state.comp_crit else False

    @work(thread=True, exclusive=True)
    def _collect(self) -> None:
        """在后台线程中执行阻塞 I/O 操作。
        exclusive=True 确保上次工作器未完成时跳过新调用。
        """
        scanner.update()           # HTTP (OHM JSON API)

        # CPU数据采样
        load_val = scanner.get_val("CPU Total", "%")
        if load_val is not None: state.cpu_load = parse_n(load_val)
        
        freq_str = scanner.get_val("Cores (Average)", "MHz")
        if freq_str:
            state.add_cpu_freq(parse_n(freq_str))

        v_list   = [parse_n(scanner.get_val(f"Core #{i} VID", "V")) for i in range(1, 9)]
        v_list   = [v for v in v_list if v > 0]
        state.avg_volt = sum(v_list) / len(v_list) if v_list else 0.0
    
        cpu_pwr = scanner.get_val("Package", "W")
        if cpu_pwr is not None:
            state.current_cpu_power = parse_n(cpu_pwr)

        cpu_temp_val = scanner.get_val("Core (Tctl/Tdie)", "°C")
        if cpu_temp_val is not None:
            state.cpu_temp = parse_n(cpu_temp_val)

        fan_cpu = scanner.get_val("Fan #2", "RPM")
        if fan_cpu: state.cpu_fan = fan_cpu

        # GPU数据采样
        gpu_load_val = scanner.get_val("GPU Core", "%")
        if gpu_load_val is not None: state.gpu_load = parse_n(gpu_load_val)
        
        gpu_freq_str = scanner.get_val("GPU Core", "MHz")
        if gpu_freq_str:
            state.add_gpu_freq(parse_n(gpu_freq_str))

        v_used = parse_n(scanner.get_val("GPU Memory Used", "MB"))
        v_total = parse_n(scanner.get_val("GPU Memory Total", "MB"))
        state.vram_used_pct = (v_used / v_total * 100) if v_total > 0 else 0.0

        gpu_volt_val = scanner.get_val("GPU Core Voltage", "V")
        if gpu_volt_val: state.gpu_volt = gpu_volt_val

        gpu_p_raw = scanner.get_val("GPU Package", "W")
        if gpu_p_raw is not None:
            state.current_gpu_power = parse_n(gpu_p_raw)
            
        gpu_temp_val = scanner.get_val("GPU Core", "°C")
        if gpu_temp_val is not None:
            state.gpu_temp = parse_n(gpu_temp_val)

        fan_gpu = scanner.get_val("GPU Fan", "RPM")
        if fan_gpu: state.gpu_fan = fan_gpu

        # 其他数据
        mem_p = scanner.get_val("Total Memory Memory", "%")
        if mem_p is not None: state.used_p = parse_n(mem_p)
        
        used_gb = scanner.get_val("Total Memory Memory Used", "GB")
        if used_gb: state.used_gb = used_gb
        
        avail_gb = scanner.get_val("Total Memory Memory Available", "GB")
        if avail_gb: state.avail_gb = avail_gb
        
        net_val = scanner.get_val("イーサネット Download Speed")
        if net_val: state.net_dn_raw = net_val

        # 解析下载速度并记录最大值（从 render 中移至此，避免重复调用）
        dn_match = re.search(r"([0-9,.]+)\s*(KB|MB|GB)/s?", state.net_dn_raw, re.IGNORECASE)
        dn_kbps = 0.0
        if dn_match:
            num = float(dn_match.group(1).replace(",", ""))
            unit = dn_match.group(2).upper()
            if unit == "GB":
                dn_kbps = num * 1024 * 1024
            elif unit == "MB":
                dn_kbps = num * 1024
            else:
                dn_kbps = num
        state.add_net_dn(dn_kbps)

        # 磁盘速度放在传感器采样后，减小时间偏差
        state.refresh_disk_speed()

        # 新增：更新面板临界状态标志
        # FUSE (Melchior) 的临界判断基于 CPU 温度
        state.fuse_crit = (state.cpu_temp >= CPU_TEMP_CRITICAL)
        # P-STAT (Balthasar) 的临界判断基于总功耗 (与 build_balthasar 中的逻辑一致)
        total_pwr = state.current_cpu_power + state.current_gpu_power + BASE_POWER_OFFSET
        state.pstat_crit = (total_pwr >= POWER_CRIT)
        # COMP (Casper) 的临界判断基于 GPU 负载和 VRAM 使用率 (与 build_casper 中的最高等级逻辑一致)
        state.comp_crit = (state.gpu_load >= GPU_LOAD_HIGH and state.vram_used_pct >= VRAM_USED_HIGH)

        # ── Ollama 数据采集（按时间守卫）────────────────────────────────
        now = time.time()
        if now - self._last_ollama_ps_time > 1.0:
            self._last_ollama_ps_time = now
            try:
                r = requests.get("http://localhost:11434/api/ps", timeout=1)
                models = r.json().get("models", [])
                if models:
                    state._ai_empty_count = 0
                    details = models[0].get("details", {})
                    state.ai_family = details.get("family", "?")
                    state.ai_quant = details.get("quantization_level", "?")
                else:
                    state._ai_empty_count += 1
                    if state._ai_empty_count >= 2 and state._prev_ai_family != "STBY":
                        state.ai_family = "STBY"
                        state.ai_quant = "STBY"
            except Exception:
                state.ai_family = "OFFLINE"
                state.ai_quant = ""

        # Ollama 日志扫描：GPU 卸载比 + GIN 推理请求
        if now - self._last_log_scan_time > 1.0:
            self._last_log_scan_time = now
            try:
                with open(OLLAMA_LOG_PATH, "r", encoding="utf-8") as f:
                    if self._log_last_pos > 0:
                        try:
                            f.seek(self._log_last_pos)
                        except OSError:
                            self._log_last_pos = 0  # 日志轮转，从头开始
                    for line in f:
                        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line)
                        # GPU 卸载比
                        m = re.search(r'offloaded (\d+)/(\d+) layers to GPU', clean)
                        if m:
                            state.ai_offload_gpu = int(m.group(1))
                            state.ai_offload_total = int(m.group(2))
                            state.ai_offload_pct = state.ai_offload_gpu / state.ai_offload_total * 100
                        # GIN 推理请求
                        m = re.match(r'\[GIN\].*?\|.*?\|.*?\|.*?\| \w+\s+"(/api/generate|/api/chat|/v1/chat/completions|/v1/completions)"', clean)
                        if m:
                            state.ai_req_count += 1
                            state.ai_last_req_ts = time.time()
                    self._log_last_pos = f.tell()
            except Exception:
                state.ai_offload_pct = 0.0

        # 检测 ai_family 转换：从已加载 → STBY/OFFLINE 时清除派生数据
        if state._prev_ai_family not in ("STBY", "OFFLINE") and state.ai_family in ("STBY", "OFFLINE"):
            state.ai_offload_gpu = 0
            state.ai_offload_total = 0
            state.ai_req_count = 0
            state.ai_last_req_ts = 0.0
        state._prev_ai_family = state.ai_family

        # 不再在此处调用 _check_alert 和 _refresh_all，改由 _tick 处理

    @work(thread=True, exclusive=True)
    def _collect_slow_tasks(self) -> None:
        """独立运行的慢速任务，不影响传感器刷新率"""
        state.update_ping()
        state.update_weather()  # HTTP (wttr.in) 每30分钟更新
        state.update_tcp_counts()

    def _refresh_all(self) -> None:
        # 使用缓存的面板引用，避免每次刷新都执行 CSS 选择器查询
        for widget in self._refresh_widgets:
            widget.refresh()

    # ── 快捷键操作 ───────────────────────────────────────────────────────────

    def action_launch_pstop(self) -> None:
        """M 键：暂停 Textual 并启动 pstop，退出后恢复。"""
        with self.suspend():
            subprocess.run(["pstop"])

    def action_launch_yazi(self) -> None:
        with self.suspend():
            subprocess.run(["yazi", "f:\\"])

    def action_launch_psnet(self) -> None:
        with self.suspend():
            subprocess.run(["psnet"])

    def action_launch_opencode(self) -> None:
        with self.suspend():
            subprocess.run(["opencode", "D:\\tools"])

    # ── 警报 ───────────────────────────────────────────────────────────
        
    def _check_alert(self, cpu_temp: float, gpu_temp: float) -> None:
        old_level, new_level = state.update_alert(cpu_temp, gpu_temp)

        # 只在新等级比旧等级高，或距离上次通知超过 30 秒时通知
        now = time.time()
        if new_level > 0 and (new_level > old_level or now - state.last_alert_time > 30):
            state.last_alert_time = now
            if new_level == 2:
                self.notify("[bold][red][ !! ANGEL DETECTED !! ]  MAGI SYSTEM — PATTERN BLUE CONFIRMED", severity="error", timeout=10)
            else:
                self.notify("[bold][#FFD700]⚠️  HIGH TEMPERATURE DETECTED", severity="warning", timeout=5)

# ══════════════════════════════════════════════════════════════════════════════
#  入口点
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    MAGIApp().run()
