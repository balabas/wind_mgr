from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class WindowRecord:
    xid: int
    title: str
    app_name: str
    pid: int
    wm_class: str
    wm_class_group: str
    appeared_at: float
    last_focused_at: float
    parent_xid: int | None
    children_xids: list[int]
    metadata: dict[str, Any]
    is_alive: bool
    project_id: str | None = None  # None = auto (derive from root xid)

    @property
    def app_type(self) -> str:
        return self.metadata.get("app_type", "generic")

    @property
    def effective_project_id(self) -> str:
        return self.project_id or str(self.xid)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WindowRecord":
        return cls(
            xid=d["xid"],
            title=d["title"],
            app_name=d["app_name"],
            pid=d["pid"],
            wm_class=d["wm_class"],
            wm_class_group=d["wm_class_group"],
            appeared_at=d["appeared_at"],
            last_focused_at=d["last_focused_at"],
            parent_xid=d["parent_xid"],
            children_xids=list(d.get("children_xids", [])),
            metadata=dict(d.get("metadata", {})),
            is_alive=d["is_alive"],
            project_id=d.get("project_id"),
        )

    @classmethod
    def make(cls, xid: int, title: str, app_name: str, pid: int,
             wm_class: str, wm_class_group: str,
             parent_xid: int | None = None) -> "WindowRecord":
        now = time.time()
        return cls(
            xid=xid,
            title=title,
            app_name=app_name,
            pid=pid,
            wm_class=wm_class,
            wm_class_group=wm_class_group,
            appeared_at=now,
            last_focused_at=now,
            parent_xid=parent_xid,
            children_xids=[],
            metadata={},
            is_alive=True,
            project_id=None,
        )
