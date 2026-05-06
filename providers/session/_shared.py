from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from urllib.parse import unquote

log = logging.getLogger(__name__)


def uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        return unquote(uri[7:])
    return uri


def proc_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def child_pids(parent_pid: int) -> list[int]:
    pids: list[int] = []
    try:
        children_file = Path(f"/proc/{parent_pid}/task/{parent_pid}/children")
        if children_file.exists():
            return [int(p) for p in children_file.read_text().split() if p]
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():
                continue
            try:
                fields = (p / "stat").read_text().split()
                if int(fields[3]) == parent_pid:
                    pids.append(int(p.name))
            except (OSError, ValueError, IndexError):
                continue
    except Exception:
        pass
    return pids


def find_vscode_project_path(project_name: str) -> str:
    """Return the absolute path of a VS Code project folder by name."""
    candidates = [
        Path.home() / ".config" / "Code" / "User" / "globalStorage" / "storage.json",
        Path.home() / ".config" / "Code - OSS" / "User" / "globalStorage" / "storage.json",
        Path.home() / ".config" / "VSCodium" / "User" / "globalStorage" / "storage.json",
        Path.home() / ".config" / "Code" / "storage.json",
        Path.home() / ".config" / "Code - OSS" / "storage.json",
        Path.home() / ".config" / "VSCodium" / "storage.json",
    ]
    for storage in candidates:
        if not storage.exists():
            continue
        try:
            data = json.loads(storage.read_text())
            # Modern VS Code (1.70+): backupWorkspaces.folders[].folderUri
            for entry in data.get("backupWorkspaces", {}).get("folders", []):
                uri = entry.get("folderUri", "") if isinstance(entry, dict) else ""
                p = uri_to_path(uri)
                if p and Path(p).name == project_name:
                    return p
            # Legacy: openedPathsList
            entries = (
                data.get("openedPathsList", {}).get("entries")
                or data.get("openedPathsList", {}).get("workspaces3")
                or []
            )
            for entry in entries:
                uri = entry if isinstance(entry, str) else (
                    entry.get("folderUri") or entry.get("fileUri") or ""
                )
                p = uri_to_path(uri)
                if p and Path(p).name == project_name:
                    return p
        except Exception:
            log.debug("vscode storage parse failed: %s", storage, exc_info=True)
    return ""


def find_jetbrains_project_path(project_name: str) -> str:
    """Return the absolute path of a JetBrains project folder by name."""
    jetbrains_root = Path.home() / ".config" / "JetBrains"
    if not jetbrains_root.exists():
        return ""
    for ide_dir in jetbrains_root.iterdir():
        xml = ide_dir / "options" / "recentProjects.xml"
        if not xml.exists():
            continue
        try:
            for line in xml.read_text().splitlines():
                if "value=" not in line:
                    continue
                start = line.find('value="') + 7
                end = line.find('"', start)
                if start < 7 or end < 0:
                    continue
                p = uri_to_path(line[start:end])
                if p and Path(p).name == project_name:
                    return p
        except Exception:
            log.debug("jetbrains xml parse failed: %s", xml, exc_info=True)
    return ""
