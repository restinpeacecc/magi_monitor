#!/usr/bin/env python3
"""
MAGI SYSTEM Monitor — Textual Edition
rich.live + msvcrt → Textual (非同期イベントループ・スレッドワーカー・CSS レイアウト)
"""

import re
import subprocess
import time
from datetime import datetime

import psutil
import requests
from rich.panel import Panel
from rich.table import Table
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static, Label
from textual.screen import Screen


# ══════════════════════════════════════════════════════════════════════════════
#  State
# ══════════════════════════════════════════════════════════════════════════════

class MagiState:
    
    CPU_FREQ_HISTORY_MAX = 600
    GPU_FREQ_HISTORY_MAX = 600
    
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

        # ディスク速度は per-cycle でキャッシュ（render() から複数回呼ばれないよう分離）
        self._last_disk_io = psutil.disk_io_counters()
        self._last_time: float = time.time()
        self.disk_r: float = 0.0
        self.disk_w: float = 0.0
        self.ping_ms: float = 0.0
        
    # ── 警报 ────────────────────────────────────────────────────────────────

    def update_alert(self, cpu_temp: float, gpu_temp: float):
        """根据温度确定警报等级，返回 (原等级, 新等级)"""
        old_level = self.alert_level
        if cpu_temp >= 75 and gpu_temp >= 75:     # 自定义危险阈值
            new_level = 2
        elif cpu_temp >= 70 and gpu_temp >= 70:
            new_level = 1
        else:
            new_level = 0
        self.alert_level = new_level
        return old_level, new_level
    
    # ── Uptime ────────────────────────────────────────────────────────────────

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
        self.cpu_freq_history.append(val)
        if len(self.cpu_freq_history) > self.CPU_FREQ_HISTORY_MAX:
            self.cpu_freq_history = self.cpu_freq_history[-self.CPU_FREQ_HISTORY_MAX:]

    def add_gpu_freq(self, val: float):
        self.gpu_freq_history.append(val)
        if len(self.gpu_freq_history) > self.GPU_FREQ_HISTORY_MAX:
            self.gpu_freq_history = self.gpu_freq_history[-self.GPU_FREQ_HISTORY_MAX:]

    def add_net_dn(self, kbps: float):
        """记录下载速度并更新全局最大值"""
        if kbps > self.max_net_dn_kbps:
            self.max_net_dn_kbps = kbps
        
    def get_max_net_dn_kbps(self) -> float:
        return self.max_net_dn_kbps
    
    # ── Disk speed（ワーカースレッドから1回だけ呼ぶ）───────────────────────

    def refresh_disk_speed(self):
        now_io   = psutil.disk_io_counters()
        now_time = time.time()
        dt = now_time - self._last_time
        if dt > 0:
            self.disk_r = (now_io.read_bytes  - self._last_disk_io.read_bytes)  / dt / 1024 / 1024
            self.disk_w = (now_io.write_bytes - self._last_disk_io.write_bytes) / dt / 1024 / 1024
        self._last_disk_io = now_io
        self._last_time    = now_time

    # ── Weather（30分ごと）──────────────────────────────────────────────────

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
        self.sensors: list[dict] = []

    def update(self):
        try:
            raw = requests.get(self.url, timeout=0.3).json()
            self.sensors = []
            self._walk(raw)
        except Exception:
            self.sensors = []

    def _walk(self, node, hw: str = ""):
        if "HardwareId" in node:
            hw = node.get("Text", "")
        if node.get("Value"):
            self.sensors.append({"name": f"{hw} {node.get('Text', '')}", "val": node.get("Value", "")})
        for child in node.get("Children", []):
            self._walk(child, hw)

    def get_val(self, name_target: str, unit_target: str | None = None) -> str | None:
        nl = name_target.lower()
        for s in self.sensors:
            if nl in s["name"].lower():
                if unit_target is None or unit_target.lower() in s["val"].lower():
                    return s["val"]
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Module-level singletons
# ══════════════════════════════════════════════════════════════════════════════

state   = MagiState()
scanner = MAGIScanner()


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def parse_n(v_str) -> float:
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
    if val < 65:
        return "yellow"
    return "red1"

def get_status_theme(value, safe_limit, warn_limit, crit_limit) -> tuple:
    val = float(value)
    if val >= crit_limit:
        return "bold red1",      2.5,   "CRITICAL"
    if val >= warn_limit:
        return "bold gold1",     1,   "WARNING"
    if val >= safe_limit:
        return "bold green",  0.5, "CAUTION"
    return "cyan",               0,   "[reverse] STABLE [/reverse]"

def get_power_theme(value_str, safe_limit, warn_limit, crit_limit) -> tuple:
    val = float(value_str)
    # 返回格式: (颜色, 闪烁频率, 显示文本)
    if val >= crit_limit:
        return "bold red1",      2.5,  "!! OVERDRIVE !!"
    if val >= warn_limit:
        return "bold gold1",     1,  "HIGH-WATTAGE"
    if val >= safe_limit:
        return "bold green",     0.5,  "ACTIVE"
    return "cyan",               0,    "[reverse] ECO [/reverse]"

def blink_markup(text: str, color: str, freq: float) -> str:
    """time.time() ベースの点滅マークアップ（render() 呼び出しのたびに評価される）"""
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
        
    vmin, vmax = y_range if y_range else (min(data), max(data))
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
          
# ══════════════════════════════════════════════════════════════════════════════
#  Panel builders（Rich レンダラブルを返す）
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

    spark = generate_braille_trend(
        state.cpu_freq_history, 
        width=22, 
        y_range=(3000.0, 5000.0),
        low_color="cyan", 
        mid_color="yellow", 
        high_color="red1"
    )

    history = state.cpu_freq_history[-600:]   # 最多 5 分钟
    if history:
        f_min = min(history)
        f_max = max(history)
        f_now = history[-1]
        # 方向判断（最近 5 秒 vs 之前 5 秒）
        recent = history[-25:] if len(history) >= 25 else history
        older  = history[-50:-25] if len(history) >= 50 else history[:len(recent)]
        avg_recent = sum(recent) / len(recent)
        avg_older  = sum(older) / len(older) if older else avg_recent
        if avg_recent > avg_older + 20:
            arrow = "[green]▲[/]"
        elif avg_recent < avg_older - 20:
            arrow = "[red]▼[/]"
        else:
            arrow = "[yellow]►[/]"
        # 组装文本
        freq_str = (
            f"[dim]{f_min:.0f}[/] "
            f"[bold gold1]{f_now:.0f} MHz {arrow}[/] "
            f"[dim]{f_max:.0f}[/]"
        )
    else:
        freq_str = "[dim]collecting...[/]"
        
    color, freq, status_text = get_status_theme(state.cpu_temp, 50, 60, 70)

    t = Table.grid(padding=0)
    t.add_column(width=10)
    t.add_row("LOAD",   generate_bar(state.cpu_load, color="orange3"))
    t.add_row("FREQ",  freq_str)
    t.add_row("TREND",  spark)
    t.add_row("V-AVG",  f"[cadet_blue]{state.avg_volt:.4f} V[/]")
    t.add_row("PKG-W",  f"[#4169E1]{state.current_cpu_power:.1f} W[/]")
    t.add_row("TEMP",   f"[bold {get_temp_color(state.cpu_temp)}]{state.cpu_temp:.0f} °C[/]")
    t.add_row("FAN ",   f"[indian_red1]{state.cpu_fan or 'OFFLINE'}[/]")
    t.add_row("FUSE",   blink_markup(status_text, color, freq))
    return Panel(
        t,
        title="[bold orange3]MAGI-01: MELCHIOR[/]",
        border_style="orange3",
        subtitle="AMD Ryzen 7 7800X3D",
    )


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

    state.add_net_dn(dn_kbps)          # 记录进历史
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

    # 计算整机估算功耗
    # 基础偏置功率（主板/风扇/SSD）建议设在 40-50W 左右
    offset = 45.0 
    total_pwr = state.current_cpu_power + state.current_gpu_power + offset
    
    # 使用定制的功耗状态灯
    p_color, p_freq, p_text = get_power_theme(total_pwr, 100, 180, 300)

    t = Table.grid(padding=0)
    t.add_column(width=10)
    t.add_row("MEMORY", generate_bar(state.used_p, color="bright_blue"))
    t.add_row("USED",   f"[bold red]{state.used_gb or 'N/A'}[/]")
    t.add_row("FREE",   f"[bold green]{state.avail_gb or 'N/A'}[/]")
    t.add_row("NET-DN", net_display) 
    t.add_row("PING",   ping_str)
    t.add_row("TCP",    tcp_str) 
    t.add_row("DISK",   f"[indian_red1]R:{state.disk_r:.1f} W:{state.disk_w:.1f} MB/s[/]")
    t.add_row("P-STAT",  blink_markup(p_text, p_color, p_freq))
    return Panel(
        t,
        title="[bold orange3]MAGI-02: BALTHASAR[/]",
        border_style="orange3",
        subtitle="SYSTEM",
    )


def build_casper() -> Panel:
    
    if state.gpu_load > 50 and state.vram_used_pct > 50:                        # 最高优先级：满负荷
        on = (time.time() * 5) % 2 < 1                  # 2.5 Hz
        ai = "[bold red1][reverse] RTX-ON [/reverse][/]" if on else "[bold red1] AI-ACTIVE [/]"
    elif state.gpu_load > 20 and state.vram_used_pct > 20:                      # 中负荷
        on = (time.time() * 2) % 2 < 1                  # 1 Hz
        ai = "[bold gold1][reverse] AI-ACTIVE [/reverse][/]" if on else "[bold red1] AI-ACTIVE [/]"
    elif state.gpu_load > 10:                                     # 低负荷（仅 GPU 核心有活动）
        on = (time.time() * 1) % 2 < 1                  # 0.5 Hz
        ai = "[bold green][reverse] INIT [/reverse][/]" if on else "[bold green] INIT [/]"
    else:
        ai = "[cyan][reverse] IDLE [/reverse][/]"       # 空闲

    history = state.gpu_freq_history[-600:]   # 最多 1 分钟
    if history and len(history) >= 2:
        f_min = min(history)
        f_max = max(history)
        f_now = history[-1]
        # 方向判断（最近 5 秒 vs 之前 5 秒）
        recent = history[-25:] if len(history) >= 25 else history
        older  = history[-50:-25] if len(history) >= 50 else []
        avg_recent = sum(recent) / len(recent)
        avg_older  = sum(older) / len(older) if older else avg_recent
        if avg_recent > avg_older + 20:
            arrow = "[green]▲[/]"
        elif avg_recent < avg_older - 20:
            arrow = "[red]▼[/]"
        else:
            arrow = "[yellow]►[/]"
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
    t.add_row("COMP",   ai)
    return Panel(
        t,
        title="[bold orange3]MAGI-03: CASPER[/]",
        border_style="orange3",
        subtitle="NVIDIA RTX 5070",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Textual Widgets（render() が Rich Panel を返す）
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
#  SplashScreen 
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
#  App
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
    ]

    def compose(self) -> ComposeResult:
        yield MAGIHeader()
        with Horizontal(id="panels"):
            yield MelchiorPanel()
            yield BalthasarPanel()
            yield CasperPanel()
        yield Footer()

    def on_mount(self) -> None:
        # 先显示启动动画屏幕
        self.push_screen(SplashScreen())
        self.set_interval(0.2, self._tick)     # 高频：传感器
        self.set_interval(5.0, self._collect_slow_tasks) # 低频：Ping/TCP

    # ── Update cycle ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """イベントループから呼ばれる軽量ディスパッチャ。実処理はワーカーへ。"""
        self._collect()

    @work(thread=True, exclusive=True)
    def _collect(self) -> None:
        """
        ブロッキング I/O をバックグラウンドスレッドで実行。
        exclusive=True により前回のワーカーが終わっていなければ新規呼び出しはスキップ。
        """
        scanner.update()           # HTTP (OHM JSON API)
        state.refresh_disk_speed() # psutil（状態更新を含むため1回だけ呼ぶ）

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

        v_used = parse_n(scanner.get_val("D3D Dedicated Memory Used", "MB"))
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

        # 通过 call_from_thread 安全地回到主线程触发通知
        self.call_from_thread(self._check_alert, state.cpu_temp, state.gpu_temp)

        self.call_from_thread(self._refresh_all)

    @work(thread=True, exclusive=True)
    def _collect_slow_tasks(self) -> None:
        """独立运行的慢速任务，不影响传感器刷新率"""
        state.update_ping()
        state.update_weather()  # HTTP (wttr.in) ※30分ごと
        state.update_tcp_counts()

    def _refresh_all(self) -> None:
        # 一次性刷新所有继承自 Static 的面板，减少 query_one 开销
        self.query("MAGIHeader, MelchiorPanel, BalthasarPanel, CasperPanel").refresh()

    # ── Key actions ───────────────────────────────────────────────────────────

    def action_launch_pstop(self) -> None:
        """M キー: Textual を一時停止して pstop を起動し、終了後に再開する。"""
        with self.suspend():
            subprocess.run(["pstop"])

    def action_launch_yazi(self) -> None:
        with self.suspend():
            subprocess.run(["yazi", "f:\\"])

    def action_launch_psnet(self) -> None:
        with self.suspend():
            subprocess.run(["psnet"])
            
    # ── alert ───────────────────────────────────────────────────────────
        
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
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    MAGIApp().run()
