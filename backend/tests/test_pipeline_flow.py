"""压力管道状态机与检验联动回归测试。

覆盖问题单里的每一条：
- 状态只能逐级往下走：不许跳档、不许回流、已报废终态；
- 检验完成按结论重新定格；报废后不回待检验清单（含联动检验任务撤销）；
- 换档后列表页与详情页同时换出新结果，级别/设计压力两处一致；
- 待检验清单分页不漏项、不混入已报废；
- 越界分页自动夹回有效页。
"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    # 每个用例把内存数据恢复到种子状态，避免顺序污染
    from app.store import store

    store.reset()
    return TestClient(app)


def get_pipe(client: TestClient, pipe_id: int) -> dict:
    resp = client.get(f"/api/pipeline/{pipe_id}")
    assert resp.status_code == 200
    return resp.json()


def act(client: TestClient, pipe_id: int, action: str):
    return client.post(f"/api/pipeline/{pipe_id}/actions", json={"values": {"action": action}})


def conclude(client: TestClient, pipe_id: int, conclusion: str):
    return client.post(
        f"/api/pipeline/{pipe_id}/conclusion", json={"values": {"conclusion": conclusion}}
    )


def test_status_must_advance_one_step_at_a_time(client: TestClient) -> None:
    # 种子 1 号是“正常”：可以记录减薄
    ok = act(client, 1, "记录减薄")
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert get_pipe(client, 1)["status"] == "壁厚减薄"

    # 不允许回流：壁厚减薄不能再“记录减薄”
    back = act(client, 1, "记录减薄")
    assert back.json()["ok"] is False
    assert "不允许跳档或回流" in back.json()["message"]

    # 不允许跳档：壁厚减薄不能直接申请报废
    skip = act(client, 1, "申请报废")
    assert skip.json()["ok"] is False
    assert get_pipe(client, 1)["status"] == "壁厚减薄"

    # 逐级走完
    assert act(client, 1, "安排检验").json()["ok"] is True
    assert get_pipe(client, 1)["status"] == "待检验"
    assert act(client, 1, "申请报废").json()["ok"] is True
    assert get_pipe(client, 1)["status"] == "已报废"

    # 已报废终态：任何动作都被拒绝，不能再改回壁厚减薄
    again = act(client, 1, "记录减薄")
    assert again.json()["ok"] is False
    assert get_pipe(client, 1)["status"] == "已报废"


def test_conclusion_reframes_pipe(client: TestClient) -> None:
    # 3 号种子处于待检验
    assert get_pipe(client, 3)["status"] == "待检验"

    resp = conclude(client, 3, "合格，继续使用")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert get_pipe(client, 3)["status"] == "正常"

    # 非待检验状态不允许录结论
    bad = conclude(client, 3, "合格")
    assert bad.json()["ok"] is False

    # 走完前两档再检验：减薄结论定格到壁厚减薄
    act(client, 3, "记录减薄")
    act(client, 3, "安排检验")
    assert conclude(client, 3, "壁厚减薄，需缩短检验周期").json()["ok"] is True
    assert get_pipe(client, 3)["status"] == "壁厚减薄"

    # 报废结论直接定格终态
    act(client, 3, "安排检验")
    assert conclude(client, 3, "报废").json()["ok"] is True
    assert get_pipe(client, 3)["status"] == "已报废"


def test_list_and_detail_always_agree(client: TestClient) -> None:
    # 管道级别 / 设计压力 / 状态：列表行与详情是同一份口径
    listed = {row["id"]: row for row in client.get("/api/pipeline?size=200").json()["items"]}
    for pipe_id, row in listed.items():
        detail = get_pipe(client, int(pipe_id))
        assert row["管道级别"] == detail["管道级别"]
        assert row["设计压力"] == detail["设计压力"]
        assert row["管道状态"] == detail["管道状态"] == detail["status"]

    # 换档之后两处同时换新结果
    act(client, 1, "记录减薄")
    listed = {row["id"]: row for row in client.get("/api/pipeline?size=200").json()["items"]}
    assert listed[1]["管道状态"] == "壁厚减薄"
    assert get_pipe(client, 1)["管道状态"] == "壁厚减薄"


def test_pending_list_pagination_and_scrap_exclusion(client: TestClient) -> None:
    # 种子里只有 3 号待检验；1、2 号推进到待检验，凑出 3 条，再把 4 号保持报废
    act(client, 1, "记录减薄")
    act(client, 1, "安排检验")
    act(client, 2, "安排检验")

    pending = client.get("/api/pipeline?status=待检验&size=200").json()
    ids = {item["id"] for item in pending["items"]}
    assert ids == {1, 2, 3}
    assert 4 not in ids  # 已报废不回待检验清单

    # 每页 2 条翻页：两页合起来不重不漏
    p1 = client.get("/api/pipeline?status=待检验&page=1&size=2").json()
    p2 = client.get("/api/pipeline?status=待检验&page=2&size=2").json()
    page_ids = [item["id"] for item in p1["items"]] + [item["id"] for item in p2["items"]]
    assert sorted(page_ids) == [1, 2, 3]
    assert p1["total"] == 3

    # 在待检验清单里直接报废 3 号：清单少一条，但翻页仍不漏剩下的
    assert act(client, 3, "申请报废").json()["ok"] is True
    remain = client.get("/api/pipeline?status=待检验&size=200").json()
    assert {item["id"] for item in remain["items"]} == {1, 2}

    # 请求超出末页：夹回末页，不返回空白
    clamped = client.get("/api/pipeline?status=待检验&page=9&size=2").json()
    assert clamped["page"] == 1
    assert len(clamped["items"]) == 2


def test_arrange_inspection_creates_linked_task_and_scrap_cancels_it(client: TestClient) -> None:
    # 安排检验后，检验模块出现关联任务且进入待检验清单
    act(client, 1, "记录减薄")
    act(client, 1, "安排检验")
    tasks = client.get("/api/inspection?status=待检验&size=200").json()["items"]
    linked = [t for t in tasks if t["被检设备"] == "PIPE:1"]
    assert len(linked) == 1

    # 再次安排检验被状态机拒绝，也不会重复挂单
    again = act(client, 1, "安排检验")
    assert again.json()["ok"] is False
    tasks = client.get("/api/inspection?status=待检验&size=200").json()["items"]
    assert sum(1 for t in tasks if t["被检设备"] == "PIPE:1") == 1

    # 管道报废：关联任务撤销，不再赖在待检验清单
    act(client, 1, "申请报废")
    pending = client.get("/api/inspection?status=待检验&size=200").json()["items"]
    assert all(t["被检设备"] != "PIPE:1" for t in pending)
    canceled = client.get("/api/inspection?status=已取消&size=200").json()["items"]
    assert any(t["被检设备"] == "PIPE:1" for t in canceled)


def test_conclusion_from_inspection_side_reframes_pipe(client: TestClient) -> None:
    # 3 号管道的关联任务是种子 1 号检验任务：待检验 → 检验中 → 录入结论
    client.post("/api/inspection/1/actions", json={"values": {"action": "安排检验"}})
    resp = client.post(
        "/api/inspection/1/actions",
        json={"values": {"action": "录入结论", "conclusion": "合格"}},
    )
    assert resp.json()["ok"] is True
    # 管道同一拍定格为正常，两处同步
    assert get_pipe(client, 3)["status"] == "正常"


def test_status_counts_endpoint(client: TestClient) -> None:
    counts = client.get("/api/pipeline/stats").json()
    assert counts == {"正常": 1, "壁厚减薄": 1, "待检验": 1, "已报废": 1}


def test_inspection_side_thinning_and_scrap_reframes_pipe(client: TestClient) -> None:
    # 种子 1 号任务关联 3 号管道；走完全流程后用“减薄”结论，管道定格壁厚减薄，任务合格
    client.post("/api/inspection/1/actions", json={"values": {"action": "安排检验"}})
    resp = client.post(
        "/api/inspection/1/actions",
        json={"values": {"action": "录入结论", "conclusion": "壁厚减薄，缩短检验周期"}},
    )
    assert resp.json()["ok"] is True
    assert client.get("/api/inspection/1").json()["status"] == "合格"
    assert get_pipe(client, 3)["status"] == "壁厚减薄"

    # 再安排检验，用“报废”结论：管道终态，任务不合格，待检验清单两侧都不留
    act(client, 3, "安排检验")
    tasks = client.get("/api/inspection?status=待检验&size=200").json()["items"]
    new_task = [t for t in tasks if t["被检设备"] == "PIPE:3"][0]
    client.post(f"/api/inspection/{new_task['id']}/actions", json={"values": {"action": "安排检验"}})
    resp = client.post(
        f"/api/inspection/{new_task['id']}/actions",
        json={"values": {"action": "录入结论", "conclusion": "报废"}},
    )
    assert resp.json()["ok"] is True
    assert client.get(f"/api/inspection/{new_task['id']}").json()["status"] == "不合格"
    assert get_pipe(client, 3)["status"] == "已报废"


def test_rectify_action_on_unlinked_task_and_terminal_guard(client: TestClient) -> None:
    # 种子 2 号任务（检验中，不关联管道）：下达整改 -> 不合格，不应报错
    resp = client.post("/api/inspection/2/actions", json={"values": {"action": "下达整改"}})
    assert resp.json()["ok"] is True
    assert client.get("/api/inspection/2").json()["status"] == "不合格"
    # 终态再操作被拒
    again = client.post("/api/inspection/2/actions", json={"values": {"action": "安排检验"}})
    assert again.json()["ok"] is False
    # 录入结论必须带文本
    bad = client.post(
        "/api/inspection/1/actions",
        json={"values": {"action": "安排检验"}},
    ).json()
    # 1 号仍是待检验，推进到检验中后空结论被拒
    assert bad["ok"] is True
    empty = client.post(
        "/api/inspection/1/actions",
        json={"values": {"action": "录入结论", "conclusion": ""}},
    )
    assert empty.json()["ok"] is False


def test_pipeline_create_starts_normal_and_lists_same_as_detail(client: TestClient) -> None:
    resp = client.post(
        "/api/pipeline",
        json={"values": {"管道编号": "PIPE-NEW", "管道级别": "GC1", "设计压力": "4.0MPa"}},
    )
    assert resp.json()["ok"] is True
    new_id = resp.json()["entry"]["id"]
    assert get_pipe(client, new_id)["status"] == "正常"
    # 新登记管道不能跳档
    assert act(client, new_id, "安排检验").json()["ok"] is False
