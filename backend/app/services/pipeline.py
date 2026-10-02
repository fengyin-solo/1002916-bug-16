"""压力管道业务规则：状态流转、字段校验与筛选口径都收在这里。

状态序列固定为：正常 → 壁厚减薄 → 待检验 → 已报废。
- 日常动作（记录减薄 / 安排检验 / 申请报废）只能从当前档逐级往前走一步，
  不许跳档、不许回流，已报废是终态；
- 检验完成后按检验结论重新定格（合格/继续使用→正常、减薄/降级使用→壁厚减薄、
  报废→已报废），这是唯一允许从待检验回到前档的入口；
- 管道当前状态以最后一次操作为准，present() 是列表页与详情页共同的唯一出口，
  两处读到的级别、设计压力、状态永远一致。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "pipeline"
REQUIRED_FIELDS = ["管道编号", "管道级别", "设计压力"]
OPTIONAL_FIELDS = ["输送介质", "管道长度", "敷设方式", "检验日期"]
PRESENT_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

STATUS_ORDER = ["正常", "壁厚减薄", "待检验", "已报废"]
STATUS_NORMAL = "正常"
STATUS_THINNED = "壁厚减薄"
STATUS_PENDING = "待检验"
STATUS_SCRAPPED = "已报废"

# 日常动作：目标档必须恰好是当前档的下一档
ACTION_RULES = {"记录减薄": "壁厚减薄", "安排检验": "待检验", "申请报废": "已报废"}

# 检验结论 → 重新定格后的管道状态：先查精确口径，再按关键字兜底
CONCLUSION_RULES = {
    "合格": "正常",
    "继续使用": "正常",
    "合格继续使用": "正常",
    "壁厚减薄": "壁厚减薄",
    "减薄": "壁厚减薄",
    "降级使用": "壁厚减薄",
    "报废": "已报废",
    "建议报废": "已报废",
}
# 按优先级匹配：先命中“报废”，再命中“减薄/降级/整改/不合格”，其余按合格回归正常
CONCLUSION_KEYWORDS = [
    ("报废", "已报废"),
    ("减薄", "壁厚减薄"),
    ("降级", "壁厚减薄"),
    ("整改", "壁厚减薄"),
    ("不合格", "壁厚减薄"),
]


def resolve_conclusion(conclusion: str) -> str | None:
    text = (conclusion or "").strip()
    if not text:
        return None
    if text in CONCLUSION_RULES:
        return CONCLUSION_RULES[text]
    head = text.replace("，", ",").replace("、", ",").split(",")[0].strip()
    if head in CONCLUSION_RULES:
        return CONCLUSION_RULES[head]
    for keyword, status in CONCLUSION_KEYWORDS:
        if keyword in text:
            return status
    return "正常"


def _index(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return 0


def present(entry: dict[str, Any]) -> dict[str, Any]:
    """把内部记录翻译成页面口径：列表页、详情页、导出都走这里，保证两处不打架。"""
    status = str(entry.get("status") or STATUS_NORMAL)
    item: dict[str, Any] = {"id": entry.get("id")}
    for field in PRESENT_FIELDS:
        item[field] = entry.get(field)
    item["status"] = status
    item["管道状态"] = status
    return item


class PipelineService:
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
            rows = [row for row in rows if keyword in str(row.get("管道编号", ""))]
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
        entry = store.find(MODULE, entry_id)
        return present(entry) if entry is not None else None

    def status_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in STATUS_ORDER}
        for row in store.rows(MODULE):
            status = str(row.get("status") or STATUS_NORMAL)
            if status in counts:
                counts[status] += 1
        return counts

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip():
                entry[field] = value
        entry["status"] = STATUS_ORDER[0]
        rows.append(entry)
        self._sync_flags(entry)
        return present(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力管道 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于压力管道可执行范围"
        current = str(entry.get("status") or STATUS_NORMAL)
        if current == STATUS_SCRAPPED:
            return None, "压力管道已报废，终态不再接受任何操作"
        target = ACTION_RULES[action]
        if _index(target) != _index(current) + 1:
            return None, f"压力管道当前为「{current}」，只能先「{self._next_action(current)}」，不允许跳档或回流"
        entry["status"] = target
        self._sync_flags(entry)
        if target == STATUS_PENDING:
            self._open_linked_inspection(entry_id, entry)
        if target == STATUS_SCRAPPED:
            self._close_linked_inspections(entry_id)
        return present(entry), f"压力管道已{action}"

    @staticmethod
    def _next_action(current: str) -> str:
        target = STATUS_ORDER[_index(current) + 1]
        for action, status in ACTION_RULES.items():
            if status == target:
                return action
        return target

    def record_conclusion(
        self,
        entry_id: int,
        conclusion: str,
        inspected_on: str | None = None,
        *,
        settle_inspection: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        """检验完成后按结论重新定格；这是待检验唯一允许的离开方式（含回到前档）。

        settle_inspection 为 True（管道侧发起）时顺带结算关联检验任务；
        检验侧发起时任务已在自身流程里结算，置 False 避免重复联动。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力管道 {entry_id} 不存在或已归档"
        current = str(entry.get("status") or STATUS_NORMAL)
        if current == STATUS_SCRAPPED:
            return None, "压力管道已报废，不能再录入检验结论"
        if current != STATUS_PENDING:
            return None, f"压力管道当前为「{current}」，只有待检验的管道才能按检验结论定格"
        target = resolve_conclusion(conclusion)
        if target is None:
            return None, "检验结论不能为空，请填写：合格、壁厚减薄或报废"
        entry["status"] = target
        if inspected_on and str(inspected_on).strip():
            entry["检验日期"] = str(inspected_on).strip()
        self._sync_flags(entry)
        if target == STATUS_SCRAPPED:
            self._close_linked_inspections(entry_id)
        elif settle_inspection:
            self._settle_linked_inspection(entry_id, (conclusion or "").strip(), inspected_on)
        return present(entry), f"检验结论为「{conclusion}」，管道已定格为「{target}」"

    @staticmethod
    def _sync_flags(entry: dict[str, Any]) -> None:
        status = str(entry.get("status") or STATUS_NORMAL)
        # pending 只表示“还在待检验清单里”：已报废不再回到待检验清单
        entry["pending"] = status == STATUS_PENDING
        entry["abnormal"] = status in (STATUS_THINNED, STATUS_PENDING)
        entry["管道状态"] = status

    @staticmethod
    def _open_linked_inspection(entry_id: int, entry: dict[str, Any]) -> None:
        """安排检验时在定期检验模块挂上一条关联任务，两边的待检验清单同步出现。"""
        from app.services.inspection import inspection_service

        inspection_service.ensure_linked_task(
            entry_id,
            pipe_no=str(entry.get("管道编号") or f"PIPE-{entry_id}"),
            category="压力管道定期检验",
        )

    @staticmethod
    def _close_linked_inspections(entry_id: int) -> None:
        # 延迟导入，避免两个业务模块在模块加载期互相牵扯
        from app.services.inspection import inspection_service

        inspection_service.cancel_linked_tasks(entry_id)

    @staticmethod
    def _settle_linked_inspection(
        entry_id: int, conclusion: str, inspected_on: str | None
    ) -> None:
        from app.services.inspection import inspection_service

        inspection_service.settle_linked_task(entry_id, conclusion, inspected_on)


pipeline_service = PipelineService()
