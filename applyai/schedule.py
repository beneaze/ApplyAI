"""Daily unattended run: launchd on macOS, cron on Linux."""
from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path

LABEL = "com.applyai.daily"
MAC_PROTECTED = ("Desktop", "Documents", "Downloads")


def _launcher() -> Path:
    return Path(__file__).resolve().parent.parent / "bin" / "applyai"


def install(time: str, ws_root: Path) -> str:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", time)
    if not m:
        raise SystemExit("Time must look like 07:30")
    h, mi = int(m.group(1)), int(m.group(2))
    path = os.environ.get("PATH", "/usr/bin:/bin")
    warn = ""
    if platform.system() == "Darwin":
        top = ws_root.relative_to(Path.home()).parts[:1] if ws_root.is_relative_to(Path.home()) else ()
        if top and top[0] in MAC_PROTECTED:
            warn = ("\nWarning: macOS blocks background jobs from Desktop, Documents and Downloads. "
                    "Move the workspace (e.g. to ~/Job Applications) or the daily run will fail.")
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        logs = ws_root / ".applyai" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{_launcher()}</string><string>run</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{mi}</integer></dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/Library/TeX/texbin:/opt/homebrew/bin:/usr/local/bin:{path}</string>
    <key>APPLYAI_HOME</key><string>{ws_root}</string>
    <key>APPLYAI_NOTIFY</key><string>1</string>
  </dict>
  <key>StandardOutPath</key><string>{logs}/launchd.out</string>
  <key>StandardErrorPath</key><string>{logs}/launchd.err</string>
</dict></plist>
""")
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=True)
        return f"Daily run at {time} installed ({plist}). A Mac that was asleep runs it on wake.{warn}"
    if platform.system() == "Linux":
        line = (f"{mi} {h} * * * PATH={path} APPLYAI_HOME='{ws_root}' APPLYAI_NOTIFY=1 "
                f"'{_launcher()}' run >> '{ws_root}/.applyai/logs/cron.out' 2>&1 # {LABEL}")
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = "\n".join(l for l in cur.splitlines() if LABEL not in l) + "\n" + line + "\n"
        subprocess.run(["crontab", "-"], input=new.lstrip("\n"), text=True, check=True)
        return f"Daily run at {time} added to your crontab."
    raise SystemExit("Scheduling is supported on macOS and Linux. On Windows, run ApplyAI in WSL.")


def remove() -> str:
    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.unlink(missing_ok=True)
        return "Daily run removed."
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    subprocess.run(["crontab", "-"], input="\n".join(l for l in cur.splitlines() if LABEL not in l) + "\n",
                   text=True, check=True)
    return "Daily run removed."


def notify(title: str, msg: str):
    try:
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                           capture_output=True, timeout=10)
        elif platform.system() == "Linux":
            subprocess.run(["notify-send", title, msg], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
