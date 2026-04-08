"""Platform-aware scheduling for sticky.

Writes crontab entries (Linux/Mac) or Task Scheduler tasks (Windows)
to run sticky commands at configured times.
"""

from __future__ import annotations

import platform
import subprocess
import sys


def get_sticky_command() -> str:
    """Return the command to run sticky."""
    return f'"{sys.executable}" -m sticky.cli.app'


def schedule_digest(time_str: str = "09:00") -> dict:
    """Schedule daily digest at the given time (HH:MM format)."""
    parts = time_str.split(":")
    hour, minute = parts[0], parts[1] if len(parts) > 1 else "00"
    cmd = f"{get_sticky_command()} digest --period day"

    if platform.system() == "Windows":
        return _schedule_windows(cmd, hour, minute)
    else:
        return _schedule_crontab(cmd, hour, minute)


def _schedule_crontab(cmd: str, hour: str, minute: str) -> dict:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        return {"status": "error", "message": "crontab not found"}

    lines = [l for l in existing.splitlines() if "# sticky-digest" not in l]
    lines.append(f"{minute} {hour} * * * {cmd} > /dev/null 2>&1  # sticky-digest")

    new_crontab = "\n".join(lines) + "\n"
    result = subprocess.run(
        ["crontab", "-"], input=new_crontab, capture_output=True, text=True
    )

    if result.returncode != 0:
        return {"status": "error", "message": result.stderr}

    return {
        "status": "scheduled",
        "method": "crontab",
        "time": f"{hour}:{minute}",
        "command": cmd,
    }


def _schedule_windows(cmd: str, hour: str, minute: str) -> dict:
    task_name = "StickyDigest"
    result = subprocess.run(
        [
            "schtasks",
            "/create",
            "/tn",
            task_name,
            "/tr",
            cmd,
            "/sc",
            "daily",
            "/st",
            f"{hour}:{minute}",
            "/f",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"status": "error", "message": result.stderr}
    return {
        "status": "scheduled",
        "method": "task_scheduler",
        "time": f"{hour}:{minute}",
        "task_name": task_name,
        "command": cmd,
    }


def list_schedules() -> list[dict]:
    """List active sticky schedules."""
    if platform.system() == "Windows":
        return _list_windows()
    else:
        return _list_crontab()


def _list_crontab() -> list[dict]:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return []
        return [
            {"schedule": line.split("# ")[0].strip(), "name": "sticky-digest"}
            for line in result.stdout.splitlines()
            if "# sticky-digest" in line
        ]
    except FileNotFoundError:
        return []


def _list_windows() -> list[dict]:
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", "StickyDigest", "/fo", "LIST"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [{"name": "StickyDigest", "details": result.stdout.strip()}]
    except FileNotFoundError:
        return []


def remove_schedule(name: str = "digest") -> dict:
    """Remove a sticky schedule by name."""
    if platform.system() == "Windows":
        return _remove_windows()
    else:
        return _remove_crontab()


def _remove_crontab() -> dict:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"status": "not_found"}
        lines = [l for l in result.stdout.splitlines() if "# sticky-digest" not in l]
        new_crontab = "\n".join(lines) + "\n" if lines else ""
        subprocess.run(
            ["crontab", "-"], input=new_crontab, capture_output=True, text=True
        )
        return {"status": "removed", "method": "crontab"}
    except FileNotFoundError:
        return {"status": "error", "message": "crontab not found"}


def _remove_windows() -> dict:
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", "StickyDigest", "/f"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"status": "error", "message": result.stderr}
    return {"status": "removed", "method": "task_scheduler"}
