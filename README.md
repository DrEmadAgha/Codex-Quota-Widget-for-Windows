# Codex Quota Mini for Windows

A tiny, always-on-top Windows widget showing **remaining Codex quota** and **renewal time** for both the rolling **5-hour** and **weekly** limits.

## At a glance

- **5-hour limit:** remaining percentage, local renewal date/time, and live countdown.
- **Weekly limit:** remaining percentage, local renewal date/time, and live countdown.
- Example: **81% left · Resets: 17 Nov 2026, 06:30 AM · in 4h 00m**.
- Compact, draggable 340 × 125 px window with always-on-top toggle.
- Manual refresh, automatic refresh every five minutes, countdown updates every 30 seconds, and automatic retry when offline.
- Optional **Start with Windows** via the current user's Windows Run registry key. No administrator rights required.
- No separate API key, third-party quota server, application telemetry, usage history, or disk logs.

**Important:** Percentages indicate **remaining quota**, not usage already consumed. Renewal time comes from Codex's `resetsAt` response and is shown in your **Windows local timezone**. The countdown is computed from your system clock, and does not mean the quota has already refreshed. If the server does not provide a renewal timestamp, the widget shows **Resets: unavailable**.

## Requirements

- Windows 10/11.
- Python 3.10+ with Tkinter.
- Codex Desktop or Codex CLI installed and signed in with a ChatGPT account.

## Run from source

1. Clone the repository and open its directory.
2. Double-click `launch_widget.bat`, or run:

```powershell
py .\widget.py
```

Drag the title to move it. Right-click anywhere for **Refresh now**, **Connection status**, **Always on top**, **Start with Windows**, **Open Codex**, and **Exit**. Press **Esc** to close the widget.

The widget starts your installed Codex client in **app-server** mode over standard input/output and reads `account/rateLimits/read`. Codex manages authentication. On an authentication error the local client may be asked to refresh its session before retrying; the widget does not directly read or persist Codex credentials. The installed Codex client itself may communicate with OpenAI.

If **Start with Windows** was enabled for a previous copy of the widget, re-enable it from this copy after upgrading so the registry points to the new directory.

## Troubleshooting

- **Offline / no data:** Check `codex --version`, open Codex and confirm you are signed in, then right-click → **Refresh now**.
- **Wrong renewal clock:** Check Windows date, time, and timezone settings.
- **Missing renewal time:** Codex may not have returned a `resetsAt` value for that window.
- **Widget missing:** Ensure Python and Tkinter are available; run `py .\widget.py` in PowerShell to see any error.

## Tests

```powershell
py -m unittest discover -s tests -v
```

The tests use offline fixtures: they do **not** require your Codex login or consume any API quota. GitHub Actions runs the tests on Windows.

## Security and privacy

This project ships Python source and a small Windows batch launcher. There are **no prebuilt executable downloads** in this repository. Verify your installed `codex.exe` comes from a trusted source; the widget launches that local executable, without shell command interpolation. Keep Windows and Codex updated. The project does not collect, upload, or store your quota or credentials, and does not require elevated privileges.

## Credits and license

Adapted from [Wannatep/codex-usage-mini-widget](https://github.com/Wannatep/codex-usage-mini-widget), based on upstream commit `759aca9fe702973ea47ff02af29a6a0d125319b0`. The original project was released under [The Unlicense](UNLICENSE), retained here.

Independent community project, **not affiliated with or endorsed by OpenAI**. ChatGPT, Codex, and OpenAI are trademarks of their respective owners.