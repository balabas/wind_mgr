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
        # Walk to root. If an ancestor was manually moved to a group, inherit
        # that visible group instead of continuing to the ancestor's old root.
        visited: set[int] = set()
        current = record
        while current.parent_xid is not None:
            if current.parent_xid in visited:
                break
            visited.add(current.xid)
            parent = self._reg.get(current.parent_xid)
            if parent is None or not parent.is_alive or _is_self_record(parent):
                break
            if parent.project_id:
                return parent.project_id
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
            if _is_self_record(record):
                continue
            pid = self.get_project_id(record)
            if pid not in seen:
                name = ""
                members = self.get_project_records(pid)
                custom_name = next(
                    (r.metadata.get("custom_project_name") for r in members
                     if r.metadata.get("custom_project_name")),
                    "",
                )
                if ":solo" in pid:
                    solo_xid_str = pid.split(":")[0]
                    r = self._reg.get(int(solo_xid_str)) if solo_xid_str.isdigit() else None
                    if r:
                        name = (custom_name
                                or r.metadata.get("domain")
                                or r.app_name
                                or r.wm_class_group
                                or (r.title[:20] if r.title else ""))
                else:
                    root = self._reg.get(int(pid)) if pid.isdigit() else None
                    if root:
                        name = (custom_name
                                or root.metadata.get("domain")
                                or root.app_name
                                or root.wm_class_group
                                or root.title[:20])
                    else:
                        name = custom_name
                seen[pid] = {
                    "id": pid,
                    "name": name or f"project-{pid[-4:]}",
                    "root_xid": int(pid) if pid.isdigit() else 0,
                    "color": colors[color_idx % len(colors)],
                }
                color_idx += 1
        return list(seen.values())

    def move_node(self, xid: int, target_project_id: str,
                  with_children) -> None:
        record = self._reg.get(xid)
        if record is None:
            return
        previous_project_id = self.get_project_id(record)
        if with_children == "same_project":
            self._move_same_project_subtree(record, previous_project_id, target_project_id)
            return
        self._detach_from_parent_if_leaving_parent_project(record, target_project_id)
        if not with_children:
            # If a parent card is moved alone, give its direct children an
            # explicit old group. Otherwise they would inherit the parent's new
            # project_id and the whole hull would follow the dragged card.
            for child_xid in list(record.children_xids):
                child = self._reg.get(child_xid)
                if child is not None and child.project_id is None:
                    child.project_id = previous_project_id
        record.project_id = target_project_id
        if with_children:
            for child_xid in list(record.children_xids):
                self.move_node(child_xid, target_project_id, with_children=True)

    def set_parent(self, xid: int, parent_xid: int, with_children=False) -> None:
        record = self._reg.get(xid)
        parent = self._reg.get(parent_xid)
        if record is None or parent is None or xid == parent_xid:
            return
        if self._would_create_cycle(xid, parent_xid):
            if self._is_descendant(root_xid=xid, candidate_xid=parent_xid):
                self._reparent_to_descendant(record, parent, with_children=with_children)
            else:
                log.warning("refusing parent cycle: child=%s parent=%s", xid, parent_xid)
            return

        source_project_id = self.get_project_id(record)
        parent_project_id = self.get_project_id(parent)
        old_parent = self._reg.get(record.parent_xid) if record.parent_xid is not None else None
        if old_parent and xid in old_parent.children_xids:
            old_parent.children_xids.remove(xid)

        record.parent_xid = parent_xid
        if xid not in parent.children_xids:
            parent.children_xids.append(xid)

        if with_children == "same_project":
            self._move_same_project_subtree(record, source_project_id, parent_project_id)
            return
        self.move_node(xid, parent_project_id, with_children=with_children)

    def _is_descendant(self, root_xid: int, candidate_xid: int) -> bool:
        stack = list(self.get_children(root_xid))
        visited: set[int] = set()
        while stack:
            current = stack.pop()
            if current.xid == candidate_xid:
                return True
            if current.xid in visited:
                continue
            visited.add(current.xid)
            stack.extend(self.get_children(current.xid))
        return False

    def _reparent_to_descendant(
        self,
        record: "WindowRecord",
        descendant: "WindowRecord",
        with_children=False,
    ) -> None:
        """Move parent under its descendant without creating a cycle.

        The descendant takes the original parent slot of ``record``. The old
        branch edge into the descendant is cut, then ``record`` becomes a child
        of the descendant. Other children/subtrees remain attached to their
        current node.
        """
        source_project_id = self.get_project_id(record)
        target_project_id = self.get_project_id(descendant)
        old_parent_xid = record.parent_xid
        old_parent = self._reg.get(old_parent_xid) if old_parent_xid is not None else None
        descendant_old_parent = self._reg.get(descendant.parent_xid) if descendant.parent_xid is not None else None

        if old_parent and record.xid in old_parent.children_xids:
            old_parent.children_xids.remove(record.xid)
        if descendant_old_parent and descendant.xid in descendant_old_parent.children_xids:
            descendant_old_parent.children_xids.remove(descendant.xid)

        descendant.parent_xid = old_parent_xid
        if old_parent and descendant.xid not in old_parent.children_xids:
            old_parent.children_xids.append(descendant.xid)

        record.parent_xid = descendant.xid
        if record.xid not in descendant.children_xids:
            descendant.children_xids.append(record.xid)

        log.info(
            "reparent parent-to-descendant: child=%s descendant_parent=%s old_parent=%s cut_from=%s mode=%r",
            record.xid,
            descendant.xid,
            old_parent_xid,
            descendant_old_parent.xid if descendant_old_parent else None,
            with_children,
        )
        if with_children == "same_project":
            self._move_same_project_subtree(record, source_project_id, target_project_id)
        elif with_children:
            self.move_node(record.xid, target_project_id, with_children=True)
        else:
            record.project_id = target_project_id

    def _would_create_cycle(self, child_xid: int, parent_xid: int) -> bool:
        current_xid: int | None = parent_xid
        visited: set[int] = set()
        while current_xid is not None:
            if current_xid == child_xid:
                return True
            if current_xid in visited:
                return True
            visited.add(current_xid)
            current = self._reg.get(current_xid)
            if current is None:
                return False
            current_xid = current.parent_xid
        return False

    def _move_same_project_subtree(
        self,
        record: "WindowRecord",
        source_project_id: str,
        target_project_id: str,
    ) -> None:
        to_move = self._same_project_descendants(record, source_project_id)
        move_xids = {r.xid for r in to_move}
        pinned_source_members: dict[int, str] = {}
        pinned_children: dict[int, str] = {}
        log.info(
            "same-project move subtree: root=%s title=%r source=%s target=%s move=%s",
            record.xid,
            record.title,
            source_project_id,
            target_project_id,
            [self._record_summary(r) for r in to_move],
        )
        if record.parent_xid not in move_xids:
            self._detach_from_parent_if_leaving_parent_project(record, target_project_id)

        # Keep all non-moving cards that currently belong to the source hull in
        # that hull. Without this, cards that inherited the source project
        # implicitly can re-resolve into new solo hulls after the dragged
        # parent/root moves away.
        for source_member in self._reg.all_alive():
            if source_member.xid in move_xids or source_member.project_id is not None:
                continue
            if self.get_project_id(source_member) == source_project_id:
                pinned_source_members[source_member.xid] = source_project_id

        # Children outside the source project must not inherit the moved
        # parent's new project through the parent chain.
        for moving in to_move:
            for child_xid in list(moving.children_xids):
                if child_xid in move_xids:
                    continue
                child = self._reg.get(child_xid)
                if child is not None and child.project_id is None:
                    pinned_children[child.xid] = self.get_project_id(child)
        if pinned_source_members:
            log.info(
                "same-project move pinned non-moving source members: root=%s pinned=%s",
                record.xid,
                pinned_source_members,
            )
        if pinned_children:
            log.info(
                "same-project move pinned non-moving children: root=%s pinned=%s",
                record.xid,
                pinned_children,
            )

        for member_xid, project_id in pinned_source_members.items():
            member = self._reg.get(member_xid)
            if member is not None and member.project_id is None:
                member.project_id = project_id

        for moving in to_move:
            moving.project_id = target_project_id

        for child_xid, project_id in pinned_children.items():
            child = self._reg.get(child_xid)
            if child is not None and child.project_id is None:
                child.project_id = project_id

    def _record_summary(self, record: "WindowRecord") -> dict:
        return {
            "xid": record.xid,
            "parent": record.parent_xid,
            "explicit": record.project_id,
            "effective": self.get_project_id(record),
            "children": list(record.children_xids),
            "title": (record.title or "")[:60],
        }

    def _same_project_descendants(
        self,
        record: "WindowRecord",
        source_project_id: str,
    ) -> list["WindowRecord"]:
        result: list["WindowRecord"] = []
        stack = [record]
        visited: set[int] = set()
        while stack:
            current = stack.pop()
            if current.xid in visited:
                continue
            visited.add(current.xid)
            if self.get_project_id(current) != source_project_id:
                continue
            result.append(current)
            for child_xid in reversed(list(current.children_xids)):
                child = self._reg.get(child_xid)
                if child is not None:
                    stack.append(child)
        return result

    def _detach_from_parent_if_leaving_parent_project(
        self,
        record: "WindowRecord",
        target_project_id: str,
    ) -> None:
        if record.parent_xid is None:
            return
        parent = self._reg.get(record.parent_xid)
        if parent is None:
            record.parent_xid = None
            return
        parent_project_id = self.get_project_id(parent)
        if parent_project_id == target_project_id:
            return
        if record.xid in parent.children_xids:
            parent.children_xids.remove(record.xid)
        log.debug(
            "detached parent link: child=%s parent=%s parent_project=%s target_project=%s",
            record.xid,
            parent.xid,
            parent_project_id,
            target_project_id,
        )
        record.parent_xid = None


def _is_self_record(record: "WindowRecord") -> bool:
    values = {
        record.title,
        record.app_name,
        record.wm_class,
        record.wm_class_group,
    }
    return any((v or "").strip().lower() == "wind_mgr" for v in values)
