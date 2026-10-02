"""定期检验业务规则：状态流转、字段校验与筛选口径都收在这里。

状态序列：待检验 → 检验中 → 合格 / 不合格（终态），另有 已取消（管道报废等原因撤销）。
与压力管道联动：
- 被检设备字段以 PIPE:<id> 关联到压力管道；
- 对关联管道的任务录入结论时，管道按结论重新定格；
- 管道报废后，其尚未结束的检验任务统一撤销，不再赖在待检验清单里。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "inspection"
REQUIRED_FIELDS = ["检验编号", "被检设备", "检验类别"]
OPTIONAL_FIELDS = ["检验机构", "计划检验日", "实际检验日", "检验结论"]
PRESENT_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

STATUS_ORDER = ["待检验", "检验中", "合格", "不合格", "已取消"]
STATUS_PENDING = "待检验"
STATUS_INSPECTING = "检验中"
STATUS_PASSED = "合格"
STATUS_FAILED = "不合格"
STATUS_CANCELED = "已取消"
TERMINAL_STATUSES = {STATUS_PASSED, STATUS_FAILED, STATUS_CANCELED}

# 结论里出现这些关键字时判定为不合格（需要整改）
FAIL_KEYWORDS = ("不合格", "整改", "报废")
PIPELINE_LINK_PREFIX = "PIPE:"


def _pipeline_ref(task: dict[str, Any]) -> int | None:
    target = str(task.get("被检设备") or "").strip()
    if target.startswith(PIPELINE_LINK_PREFIX):
        suffix = target[len(PIPELINE_LINK_PREFIX):].strip()
        if suffix.isdigit():
            return int(suffix)
    return None


def present(task: dict[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or STATUS_PENDING)
    item: dict[str, Any] = {"id": task.get("id")}
    for field in PRESENT_FIELDS:
        item[field] = task.get(field)
    item["status"] = status
    item["检验状态"] = status
    return item


class InspectionService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("检验编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        page = self._clamp_page(page, size, total)
        start = (page - 1) * size
        return [present(row) for row in rows[start:start + size]], total, page

    @staticmethod
    def _clamp_page(page: int, size: int, total: int) -> int:
        page = max(int(page or 1), 1)
        last_page = max((total + size - 1) // size, 1)
        return min(page, last_page)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        task = store.find(MODULE, entry_id)
        return present(task) if task is not None else None

    def create_task(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        task = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip():
                task[field] = value
        task["status"] = STATUS_PENDING
        self._sync_flags(task)
        rows.append(task)
        return present(task), []

    # 向后兼容旧命名
    create_entry = create_task

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        conclusion: str | None = None,
        inspected_on: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        task = store.find(MODULE, entry_id)
        if task is None:
            return None, f"检验任务 {entry_id} 不存在或已归档"
        if action == "安排检验":
            return self._advance(task, STATUS_PENDING, STATUS_INSPECTING, action)
        if action == "录入结论":
            return self._record_conclusion(task, conclusion, inspected_on)
        if action == "下达整改":
            # 只针对非管道类检验任务；压力管道的处理结论统一走“录入结论”联动定格
            return self._advance(task, STATUS_INSPECTING, STATUS_FAILED, action)
        return None, f"动作「{action}」不属于定期检验可执行范围"

    def _advance(
        self, task: dict[str, Any], expected: str, target: str, action: str
    ) -> tuple[dict[str, Any] | None, str]:
        current = str(task.get("status") or STATUS_PENDING)
        if current in TERMINAL_STATUSES:
            return None, f"检验任务已「{current}」，终态不能再执行操作"
        if current != expected:
            return None, f"检验任务当前为「{current}」，不能执行「{action}」"
        task["status"] = target
        self._sync_flags(task)
        return present(task), f"检验任务已{action}"

    def _record_conclusion(
        self, task: dict[str, Any], conclusion: str | None, inspected_on: str | None
    ) -> tuple[dict[str, Any] | None, str]:
        current = str(task.get("status") or STATUS_PENDING)
        if current in TERMINAL_STATUSES:
            return None, f"检验任务已「{current}」，终态不能重复录入结论"
        if current != STATUS_INSPECTING:
            return None, f"检验任务当前为「{current}」，需先安排检验才能录入结论"
        text = (conclusion or "").strip()
        if not text:
            return None, "请先填写检验结论，再录入"
        target = STATUS_FAILED if any(word in text for word in FAIL_KEYWORDS) else STATUS_PASSED
        task["status"] = target
        task["检验结论"] = text
        if inspected_on and str(inspected_on).strip():
            task["实际检验日"] = str(inspected_on).strip()
        self._sync_flags(task)
        message = f"检验结论已录入，任务状态为「{target}」"
        # 关联到压力管道的任务：管道按同一结论重新定格，两处同步换出新结果
        pipe_id = _pipeline_ref(task)
        if pipe_id is not None:
            from app.services.pipeline import pipeline_service

            # 由检验侧发起定格，管道侧不再反向结算本任务，避免重复联动
            entry, pipe_message = pipeline_service.record_conclusion(
                pipe_id, text, inspected_on=inspected_on, settle_inspection=False
            )
            if entry is None:
                # 管道定格失败（例如已报废）不影响任务自身留痕，把原因带回去
                return present(task), f"{message}；{pipe_message}"
        return present(task), message

    def ensure_linked_task(self, pipeline_id: int, *, pipe_no: str, category: str) -> dict[str, Any]:
        """管道安排检验时挂一条关联任务；已有未结束的关联任务则直接复用，避免重复挂单。"""
        prefix = f"{PIPELINE_LINK_PREFIX}{pipeline_id}"
        rows = store.rows(MODULE)
        for task in rows:
            if str(task.get("被检设备") or "").strip() == prefix and task.get("status") not in TERMINAL_STATUSES:
                return present(task)
        task = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "检验编号": f"INSP-PIPE-{pipeline_id}-{len(rows) + 1:04d}",
            "被检设备": prefix,
            "检验类别": category,
            "检验机构": None,
            "计划检验日": None,
            "实际检验日": None,
            "检验结论": None,
            "关联管道编号": pipe_no,
            "status": STATUS_PENDING,
            "pending": True,
            "abnormal": False,
            "检验状态": STATUS_PENDING,
        }
        rows.append(task)
        return present(task)

    def settle_linked_task(
        self, pipeline_id: int, conclusion: str, inspected_on: str | None = None
    ) -> None:
        """管道侧按结论定格后，把仍在执行中的关联检验任务同步结算到终态。"""
        prefix = f"{PIPELINE_LINK_PREFIX}{pipeline_id}"
        text = (conclusion or "").strip()
        target = STATUS_FAILED if any(word in text for word in FAIL_KEYWORDS) else STATUS_PASSED
        for task in store.rows(MODULE):
            if str(task.get("被检设备") or "").strip() != prefix:
                continue
            if task.get("status") in TERMINAL_STATUSES:
                continue
            task["status"] = target
            task["检验结论"] = text
            if inspected_on and str(inspected_on).strip():
                task["实际检验日"] = str(inspected_on).strip()
            self._sync_flags(task)

    def cancel_linked_tasks(self, pipeline_id: int) -> int:
        """撤销关联到指定管道、且尚未结束的检验任务（管道进入待检验或已报废时调用）。"""
        prefix = f"{PIPELINE_LINK_PREFIX}{pipeline_id}"
        changed = 0
        for task in store.rows(MODULE):
            if str(task.get("被检设备") or "").strip() != prefix:
                continue
            if task.get("status") in TERMINAL_STATUSES:
                continue
            task["status"] = STATUS_CANCELED
            self._sync_flags(task)
            changed += 1
        return changed

    @staticmethod
    def _sync_flags(task: dict[str, Any]) -> None:
        status = str(task.get("status") or STATUS_PENDING)
        task["pending"] = status == STATUS_PENDING
        task["abnormal"] = status == STATUS_FAILED
        task["检验状态"] = status


inspection_service = InspectionService()
