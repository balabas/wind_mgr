from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from providers.session import SessionProvider
from providers.session._shared import child_pids, proc_cwd

if TYPE_CHECKING:
    from core.window_record import WindowRecord

_TERMINAL_CLASSES = {
    "gnome-terminal", "gnome-terminal-server", "konsole", "xterm",
    "alacritty", "tilix", "kitty", "terminator", "xfce4-terminal",
}
_SHELL_NAMES = {"bash", "zsh", "fish", "sh", "dash", "ksh"}


class Provider(SessionProvider):
    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _TERMINAL_CLASSES

    def collect_args(self, record: "WindowRecord") -> list[str]:
        cwd = proc_cwd(record.pid) or _shell_cwd(record.pid)
        if cwd and cwd != str(Path.home()):
            return ["--working-directory", cwd]
        return []


def _shell_cwd(terminal_pid: int) -> str:
    try:
        children = child_pids(terminal_pid)
        # Direct children first (terminal → shell)
        for pid in children:
            try:
                if Path(f"/proc/{pid}/comm").read_text().strip() in _SHELL_NAMES:
                    cwd = proc_cwd(pid)
                    if cwd:
                        return cwd
            except OSError:
                continue
        # One level deeper (terminal → pty helper → shell)
        for child_pid in children:
            for gc in child_pids(child_pid):
                try:
                    if Path(f"/proc/{gc}/comm").read_text().strip() in _SHELL_NAMES:
                        cwd = proc_cwd(gc)
                        if cwd:
                            return cwd
                except OSError:
                    continue
    except Exception:
        pass
    return ""
