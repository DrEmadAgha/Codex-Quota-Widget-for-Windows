"""Small Windows overlay for remaining Codex quota and renewal times."""

from __future__ import annotations

import ctypes
from datetime import datetime
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import webbrowser
import winreg

from codex_usage import UsageSnapshot, UsageWindow, find_codex_executables, query_usage


BACKGROUND = "#222127"
FOREGROUND = "#FBF9FD"
SECONDARY = "#B8B4C2"
TRACK = "#424149"
GREEN = "#8FE3B2"
YELLOW = "#F7DB83"
RED = "#F49A9A"
WINDOW_WIDTH = 340
WINDOW_HEIGHT = 125
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ChatGPTUsageWidget"
RECOVERY_DELAYS = (15, 30, 60, 120, 300)


def format_renewal(timestamp: int | None) -> str:
    """Use the Windows system timezone; do not guess a missing reset date."""
    if timestamp is None:
        return "Resets: unavailable"
    try:
        value = datetime.fromtimestamp(timestamp)
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        hour = value.hour % 12 or 12
        am_pm = "AM" if value.hour < 12 else "PM"
        return (f"Resets: {value.day:02d} {months[value.month - 1]} {value.year}, "
                f"{hour:02d}:{value.minute:02d} {am_pm}")
    except (OverflowError, OSError, ValueError):
        return "Resets: unavailable"


def format_countdown(timestamp: int | None, now: float | None = None) -> str:
    """A human-readable countdown, rounded upward to avoid premature zero."""
    if timestamp is None:
        return ""
    seconds = timestamp - (time.time() if now is None else now)
    if seconds <= 0:
        return "Renewal due"
    total_minutes = max(1, math.ceil(seconds / 60))
    days, remaining = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining, 60)
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {minutes:02d}m"
    return f"in {minutes}m"


def quota_color(remaining: int) -> str:
    if remaining <= 10:
        return RED
    if remaining <= 25:
        return YELLOW
    return GREEN


def recovery_delay(failures: int) -> int:
    return RECOVERY_DELAYS[min(max(1, failures) - 1, len(RECOVERY_DELAYS) - 1)]


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", ctypes.c_ulong),
    ]


def clamp_position(x: int, y: int, width: int, height: int,
                   area: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = area
    return (max(left, min(x, right - width)), max(top, min(y, bottom - height)))


def monitor_work_area(x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
    if os.name != "nt":
        return (0, 0, max(width, x + width), max(height, y + height))
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromRect.argtypes = [ctypes.POINTER(_Rect), ctypes.c_ulong]
        user32.MonitorFromRect.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
        user32.GetMonitorInfoW.restype = ctypes.c_int
        bounds = _Rect(x, y, x + width, y + height)
        monitor = user32.MonitorFromRect(ctypes.byref(bounds), 2)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return (rect.left, rect.top, rect.right, rect.bottom)
    except (AttributeError, OSError):
        pass
    user32 = ctypes.windll.user32
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def startup_command() -> str:
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    pythonw = executable.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else executable
    return f'"{runner}" "{Path(__file__).resolve()}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return value == startup_command()
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool) -> None:
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass


class QuotaWidget:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Codex Quota Mini")
        self.root.configure(bg=BACKGROUND)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.resizable(False, False)
        if os.environ.get("CODEX_QUOTA_WIDGET_VISUAL_QA") != "1":
            self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.98)

        self.snapshot: UsageSnapshot | None = None
        self.results: queue.Queue[tuple[int, str, object]] = queue.Queue()
        self.generation = 0
        self.refreshing = False
        self.failures = 0
        self.last_error = ""
        self.retry_timer: str | None = None
        self.drag_origin = (0, 0)
        self.always_on_top = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=autostart_enabled())

        self._build()
        self._position()
        self._round_corners()
        self.refresh()
        self.root.after(500, self._drain_results)
        self.root.after(30_000, self._update_countdowns)
        self.root.after(300_000, self._scheduled_refresh)

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BACKGROUND, highlightbackground="#56525F",
                         highlightthickness=1)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg=BACKGROUND, height=23, cursor="fleur")
        header.pack(fill="x", padx=10, pady=(3, 1))
        header.pack_propagate(False)
        tk.Label(header, text="✦", bg=BACKGROUND, fg=GREEN,
                 font=("Segoe UI", 9)).pack(side="left")
        title = tk.Label(header, text="Quota Mini", bg=BACKGROUND, fg=FOREGROUND,
                         font=("Segoe UI Semibold", 9), cursor="fleur")
        title.pack(side="left", padx=(5, 0))

        close = tk.Button(header, text="×", command=self.root.destroy,
                          bg=BACKGROUND, fg=SECONDARY, bd=0, width=2,
                          activebackground=TRACK, activeforeground=FOREGROUND)
        close.pack(side="right")
        self.refresh_button = tk.Button(header, text="↻", command=self.refresh,
                                         bg=BACKGROUND, fg=SECONDARY, bd=0, width=2,
                                         activebackground=TRACK, activeforeground=FOREGROUND)
        self.refresh_button.pack(side="right", padx=(0, 2))
        self.status = tk.Label(header, text="...", bg=BACKGROUND, fg=SECONDARY,
                               font=("Segoe UI", 8))
        self.status.pack(side="right", padx=(0, 6))
        self.status.bind("<Button-1>", lambda _event: self._show_status())

        self.five_value, self.five_date, self.five_countdown, self.five_meter = (
            self._quota_row(outer, "5-hour limit")
        )
        self.week_value, self.week_date, self.week_countdown, self.week_meter = (
            self._quota_row(outer, "Weekly limit")
        )

        self.menu = tk.Menu(self.root, tearoff=False, bg=BACKGROUND, fg=FOREGROUND,
                            activebackground=TRACK, activeforeground=FOREGROUND)
        self.menu.add_command(label="Refresh now", command=self.refresh)
        self.menu.add_command(label="Connection status", command=self._show_status)
        self.menu.add_checkbutton(label="Always on top", variable=self.always_on_top,
                                  command=self._toggle_topmost)
        self.menu.add_checkbutton(label="Start with Windows", variable=self.autostart,
                                  command=self._toggle_autostart)
        self.menu.add_command(label="Open Codex", command=self._open_codex)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.root.destroy)

        for item in (header, title, outer):
            item.bind("<ButtonPress-1>", self._drag_start)
            item.bind("<B1-Motion>", self._drag_move)
        self.root.bind_all("<Button-3>", self._show_menu)
        self.root.bind("<Escape>", lambda _event: self.root.destroy())

    def _quota_row(self, parent: tk.Frame, title: str):
        wrapper = tk.Frame(parent, bg=BACKGROUND)
        wrapper.pack(fill="x", padx=12, pady=(0, 2))
        title_line = tk.Frame(wrapper, bg=BACKGROUND)
        title_line.pack(fill="x")
        tk.Label(title_line, text=title, bg=BACKGROUND, fg=FOREGROUND,
                 font=("Segoe UI", 8)).pack(side="left")
        value = tk.Label(title_line, text="—% left", bg=BACKGROUND, fg=SECONDARY,
                         font=("Segoe UI Semibold", 9))
        value.pack(side="right")

        reset_line = tk.Frame(wrapper, bg=BACKGROUND)
        reset_line.pack(fill="x")
        date = tk.Label(reset_line, text="Resets: unavailable", bg=BACKGROUND, fg=SECONDARY,
                        anchor="w", font=("Segoe UI", 7))
        date.pack(side="left")
        countdown = tk.Label(reset_line, text="", bg=BACKGROUND, fg=GREEN,
                             anchor="e", font=("Segoe UI Semibold", 7))
        countdown.pack(side="right")
        meter = tk.Canvas(wrapper, height=5, bg=TRACK, bd=0, highlightthickness=0)
        meter.pack(fill="x", pady=(3, 0))
        meter.bind("<Configure>", lambda _event, canvas=meter: self._redraw_meter(canvas))
        return value, date, countdown, meter

    def _redraw_meter(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        remaining = getattr(canvas, "quota_remaining", None)
        canvas.create_rectangle(0, 0, width, height, fill=TRACK, outline="")
        if remaining is not None:
            filled = max(0, min(width, round(width * remaining / 100)))
            if filled:
                canvas.create_rectangle(0, 0, filled, height,
                                        fill=quota_color(remaining), outline="")

    def _position(self) -> None:
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        area = monitor_work_area(screen_width - 1, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        x, y = clamp_position(area[2] - WINDOW_WIDTH - 18, area[1] + 18,
                              WINDOW_WIDTH, WINDOW_HEIGHT, area)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}{x:+d}{y:+d}")

    def _round_corners(self) -> None:
        try:
            preference = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self.root.winfo_id(), 33, ctypes.byref(preference), ctypes.sizeof(preference)
            )
        except (AttributeError, OSError):
            pass

    def _drag_start(self, event: tk.Event) -> None:
        self.drag_origin = (event.x_root - self.root.winfo_x(),
                            event.y_root - self.root.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        x = event.x_root - self.drag_origin[0]
        y = event.y_root - self.drag_origin[1]
        area = monitor_work_area(x, y, WINDOW_WIDTH, WINDOW_HEIGHT)
        x, y = clamp_position(x, y, WINDOW_WIDTH, WINDOW_HEIGHT, area)
        self.root.geometry(f"{x:+d}{y:+d}")

    def _show_menu(self, event: tk.Event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.always_on_top.get())

    def _toggle_autostart(self) -> None:
        try:
            set_autostart(self.autostart.get())
        except OSError:
            self.autostart.set(autostart_enabled())
            messagebox.showerror("Codex Quota Mini", "Unable to update Windows startup settings.")

    def _open_codex(self) -> None:
        try:
            executables = find_codex_executables()
            if not executables:
                raise OSError("Codex executable not found")
            subprocess.Popen([executables[0], "app"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            webbrowser.open("https://chatgpt.com/")

    def _show_status(self) -> None:
        status = "Connected" if not self.last_error else f"Connection error:\n{self.last_error}"
        messagebox.showinfo("Codex Quota Mini", status)

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.generation += 1
        generation = self.generation
        self.refresh_button.configure(state="disabled", fg="#56605D")
        if self.snapshot is None:
            self.status.configure(text="...")
        threading.Thread(target=self._fetch, args=(generation,), daemon=True).start()
        self.root.after(50_000, lambda: self._watchdog(generation))

    def _fetch(self, generation: int) -> None:
        last_error: Exception | None = None
        for delay in (0.0, 2.0):
            if delay:
                time.sleep(delay)
            try:
                self.results.put((generation, "ok", query_usage(timeout=18.0)))
                return
            except Exception as exc:
                last_error = exc
        self.results.put((generation, "error", last_error))

    def _watchdog(self, generation: int) -> None:
        if not self.refreshing or generation != self.generation:
            return
        self.generation += 1
        self.refreshing = False
        self.refresh_button.configure(state="normal", fg=SECONDARY)
        self._connection_error("Codex refresh exceeded 50 seconds.")

    def _drain_results(self) -> None:
        while True:
            try:
                generation, kind, payload = self.results.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            self.refreshing = False
            self.refresh_button.configure(state="normal", fg=SECONDARY)
            if kind == "ok" and isinstance(payload, UsageSnapshot):
                if self.retry_timer is not None:
                    self.root.after_cancel(self.retry_timer)
                    self.retry_timer = None
                self.snapshot = payload
                self.failures = 0
                self.last_error = ""
                self.status.configure(text="LIVE", fg=GREEN)
                self._render(payload)
            else:
                self._connection_error(str(payload) if payload else "Unknown Codex error.")
        self.root.after(500, self._drain_results)

    def _connection_error(self, message: str) -> None:
        self.last_error = message
        self.failures += 1
        self.status.configure(text="RETRY" if self.failures < 3 else "OFFLINE", fg=YELLOW)
        if self.retry_timer is None:
            self.retry_timer = self.root.after(
                recovery_delay(self.failures) * 1000, self._retry
            )

    def _retry(self) -> None:
        self.retry_timer = None
        self.refresh()

    def _render_window(self, window: UsageWindow | None,
                       value: tk.Label, date: tk.Label,
                       countdown: tk.Label, meter: tk.Canvas) -> None:
        if window is None:
            value.configure(text="—% left", fg=SECONDARY)
            date.configure(text="Resets: unavailable")
            countdown.configure(text="")
            meter.quota_remaining = None
        else:
            remaining = window.remaining_percent
            value.configure(text=f"{remaining}% left", fg=quota_color(remaining))
            date.configure(text=format_renewal(window.resets_at))
            countdown.configure(text=format_countdown(window.resets_at))
            meter.quota_remaining = remaining
        self._redraw_meter(meter)

    def _render(self, snapshot: UsageSnapshot) -> None:
        self._render_window(snapshot.primary, self.five_value,
                            self.five_date, self.five_countdown, self.five_meter)
        self._render_window(snapshot.secondary, self.week_value,
                            self.week_date, self.week_countdown, self.week_meter)

    def _update_countdowns(self) -> None:
        if self.snapshot is not None:
            self.five_countdown.configure(
                text=format_countdown(self.snapshot.primary.resets_at)
                if self.snapshot.primary else ""
            )
            self.week_countdown.configure(
                text=format_countdown(self.snapshot.secondary.resets_at)
                if self.snapshot.secondary else ""
            )
        self.root.after(30_000, self._update_countdowns)

    def _scheduled_refresh(self) -> None:
        self.refresh()
        self.root.after(300_000, self._scheduled_refresh)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("This widget requires Windows 10 or Windows 11.")
    QuotaWidget().run()