from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window_registry import WindowRegistry
    from .window_record import WindowRecord

log = logging.getLogger(__name__)


class RelationshipTree:
    def __init__(self, registry: "WindowRegistry") -> None:
        self._reg = registry

    def get_ancestors(self, xid: int) -> list["WindowRecord"]:
        visited: set[int] = set()
        chain: list["WindowRecord"] = []
        current_xid: int | None = xid
        while current_xid is not None:
            if current_xid in visited:
                break
            visited.add(current_xid)
            record = self._reg.get(current_xid)
            if record is None:
                break
            if current_xid != xid:
                chain.append(record)
            current_xid = record.parent_xid
        return chain

    def get_children(self, xid: int) -> list["WindowRecord"]:
        record = self._reg.get(xid)
        if record is None:
            return []
        return [r for c in record.children_xids if (r := self._reg.get(c))]

    def get_roots(self) -> list["WindowRecord"]:
        alive = self._reg.all_alive()
        return [r for r in alive
                if r.parent_xid is None or self._reg.get(r.parent_xid) is None]

    def get_breadcrumb(self, xid: int, max_levels: int = 2) -> str:
        ancestors = self.get_ancestors(xid)[:max_levels]
        if not ancestors:
            return ""
        parts = []
        for a in reversed(ancestors):
            label = a.metadata.get("project_name") or a.metadata.get("tab_title") or a.title
            if len(label) > 30:
                label = label[:28] + "…"
            parts.append(label)
        return " > ".join(parts)

    def get_project_id(self, record: "WindowRecord") -> str:
        if record.project_id:
            return record.project_id
        # Walk to root
        visited: set[int] = set()
        current = record
        while current.parent_xid is not None:
            if current.parent_xid in visited:
                break
            visited.add(current.xid)
            parent = self._reg.get(current.parent_xid)
            if parent is None:
                break
            current = parent
        return str(current.xid)

    def get_project_records(self, project_id: str) -> list["WindowRecord"]:
        return [r for r in self._reg.all_alive()
                if self.get_project_id(r) == project_id]

    def get_projects(self) -> list[dict]:
        """Return list of project dicts for serialisation."""
        seen: dict[str, dict] = {}
        colors = ["#4a90d9", "#7b68ee", "#48b674", "#e67e22",
                  "#e74c3c", "#1abc9c", "#9b59b6", "#f39c12"]
        color_idx = 0
        for record in self._reg.all_alive():
            pid = self.get_project_id(record)
            if pid not in seen:
                root = self._reg.get(int(pid)) if pid.isdigit() else None
                name = ""
                if root:
                    name = (root.metadata.get("project_name")
                            or root.metadata.get("domain")
                            or root.app_name
                            or root.title[:20])
                seen[pid] = {
                    "id": pid,
                    "name": name or f"project-{pid[-4:]}",
                    "root_xid": int(pid) if pid.isdigit() else 0,
                    "color": colors[color_idx % len(colors)],
                }
                color_idx += 1
        return list(seen.values())

    def move_node(self, xid: int, target_project_id: str,
                  with_children: bool) -> None:
        record = self._reg.get(xid)
        if record is None:
            return
        record.project_id = target_project_id
        if with_children:
            for child_xid in list(record.children_xids):
                self.move_node(child_xid, target_project_id, with_children=True)
