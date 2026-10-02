"""压力管道业务规则：状态流转、字段校验与筛选口径都收在这里。

状态只能沿 STATUS_ORDER 逐级向下：正常 → 壁厚减薄 → 待检验 → 已报废，
不允许跳档，也不允许往回走；已报废是终态，不再接受任何动作。
检验完成后凭「录入检验结论」按结论重新定格：合格→正常、壁厚减薄→壁厚减薄、报废→已报废。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "pipeline"
REQUIRED_FIELDS = ["管道编号", "管道级别", "设计压力"]
STATUS_ORDER = ["正常", "壁厚减薄", "待检验", "已报废"]
FINAL_STATUS = STATUS_ORDER[-1]
# 日常动作只能把状态推进到紧邻的下一档，不许跳档、不许回流
ACTION_RULES = {"记录减薄": "壁厚减薄", "安排检验": "待检验", "申请报废": "已报废"}
# 检验结论允许把「待检验」的管道重新定格到对应档位（含退回正常）
CONCLUSION_RULES = {"合格": "正常", "壁厚减薄": "壁厚减薄", "报废": "已报废"}
INSPECTION_STATUS = "待检验"

def _sync_seed_rows() -> None:
    """让历史数据也遵守同一条状态口径：展示字段与派生标记以 status 为准。

    幂等：重复执行只是按当前 status 重算派生字段，不会推进任何管道的状态。
    """
    for row in store.rows(MODULE):
        if row.get("status") not in STATUS_ORDER:
            row["status"] = STATUS_ORDER[0]
        PipelineService._apply_status(row, row["status"])


class PipelineService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        _sync_seed_rows()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("管道编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        _sync_seed_rows()
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        _sync_seed_rows()
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("输送介质", "管道长度", "敷设方式", "检验日期"):
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        self._apply_status(entry, STATUS_ORDER[0])
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        conclusion: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        _sync_seed_rows()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力管道 {entry_id} 不存在或已归档"

        current = entry.get("status", STATUS_ORDER[0])

        if action == "录入检验结论":
            if current != INSPECTION_STATUS:
                return None, f"压力管道当前为{current}，只有{INSPECTION_STATUS}的管道才能录入检验结论"
            target = CONCLUSION_RULES.get(str(conclusion or "").strip())
            if target is None:
                allowed = "、".join(CONCLUSION_RULES)
                return None, f"检验结论「{conclusion}」无法识别，仅支持：{allowed}"
            self._apply_status(entry, target)
            return entry, f"压力管道检验结论为{conclusion}，状态定格为{target}"

        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于压力管道可执行范围"

        if current == FINAL_STATUS:
            return None, "压力管道已报废，终态不再接受任何操作"

        current_index = STATUS_ORDER.index(current)
        target = ACTION_RULES[action]
        target_index = STATUS_ORDER.index(target)
        if target_index < current_index:
            return None, f"压力管道当前为{current}，不能回退到{target}，状态只允许逐级向下"
        if target_index == current_index:
            return None, f"压力管道已处于{current}，请勿重复操作"
        if target_index > current_index + 1:
            return None, f"压力管道当前为{current}，不能跳档到{target}，请先完成中间环节"

        self._apply_status(entry, target)
        return entry, f"压力管道已{action}"

    @staticmethod
    def _apply_status(entry: dict[str, Any], status: str) -> None:
        """状态是唯一口径：派生字段和列表展示字段都跟着 status 一起换。"""
        entry["status"] = status
        entry["管道状态"] = status
        entry["pending"] = status == INSPECTION_STATUS
        entry["abnormal"] = status == "壁厚减薄"
