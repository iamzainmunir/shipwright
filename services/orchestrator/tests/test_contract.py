"""P1 — Spec Contract parsing, slicing, rendering, and the feedback ledger."""
from __future__ import annotations

from app import contract as C
from foundry_core.models import Contract, ContractItem

_SPEC = """
Here is the spec.

```json foundry-criteria
[{"id": "AC1", "criterion": "User can add a task", "route": "/", "expect_text": ["Add task"],
  "expect_selector": ["form"], "severity": "blocking"},
 {"id": "AC2", "criterion": "Nice dark mode", "severity": "non_blocking"}]
```

And the API:

```json foundry-api
[{"id": "API1", "criterion": "list tasks", "method": "get", "path": "/api/tasks",
  "response": {"status": 200, "contentType": "application/json", "requiredKeys": ["items"]},
  "errors": [{"when": "bad body", "status": 400}]},
 {"id": "API2", "criterion": "create task", "method": "POST", "path": "/api/tasks",
  "request": {"example": {"title": "x"}}, "response": {"status": 201}, "severity": "blocking"}]
```
"""


def test_parse_contract_reads_ui_and_api():
    c = C.parse_contract(_SPEC, mission_id="M1", workspace_id="W1")
    ui = [i for i in c.items if i.kind == "ui"]
    api = [i for i in c.items if i.kind == "api"]
    assert {i.id for i in ui} == {"AC1", "AC2"}
    assert {i.id for i in api} == {"API1", "API2"}
    a1 = next(i for i in api if i.id == "API1")
    assert a1.method == "GET" and a1.path == "/api/tasks"
    assert a1.response["requiredKeys"] == ["items"]
    ac2 = next(i for i in ui if i.id == "AC2")
    assert ac2.severity == "non_blocking"


def test_parse_contract_fail_soft_on_empty():
    c = C.parse_contract("just prose, no blocks", mission_id="M1", workspace_id="W1")
    assert c.items == []


def test_extract_api_items_ignores_ui_block():
    # The UI foundry-criteria array (no path) must NOT be misread as API items.
    ui_only = '```json foundry-criteria\n[{"id":"AC1","criterion":"x","route":"/"}]\n```'
    assert C.extract_api_items(ui_only) == []


def test_contract_slice_by_role_and_path():
    c = C.parse_contract(_SPEC, mission_id="M1", workspace_id="W1")
    # a backend subtask owning the tasks API gets the API items
    be = C.contract_slice(c, {"role": "backend", "title": "tasks api", "files": ["server/tasks.py"]})
    assert all(i.kind == "api" for i in be.items) and be.items
    # a frontend subtask gets UI items
    fe = C.contract_slice(c, {"role": "frontend", "title": "task list ui", "files": ["web/app.jsx"]})
    assert any(i.kind == "ui" for i in fe.items)


def test_contract_slice_returns_all_when_no_localization():
    c = C.parse_contract(_SPEC, mission_id="M1", workspace_id="W1")
    sl = C.contract_slice(c, {"role": "", "title": "", "files": []})
    assert len(sl.items) == len(c.items)  # over-inform rather than starve


def test_render_contract_md_has_ui_and_api():
    c = C.parse_contract(_SPEC, mission_id="M1", workspace_id="W1")
    md = C.render_contract_md(c)
    assert "Spec Contract" in md and "UI:" in md and "API:" in md
    assert "GET /api/tasks" in md
    assert C.render_contract_md(None) == ""


def test_api_items_as_dicts():
    c = C.parse_contract(_SPEC, mission_id="M1", workspace_id="W1")
    dicts = C.api_items_as_dicts(c)
    assert len(dicts) == 2 and all(d["path"] for d in dicts)


def test_record_feedback_builds_entry():
    c = Contract(id="C1", workspace_id="W1", mission_id="M1",
                 items=[ContractItem(id="API1", kind="api", method="GET", path="/api/tasks")])
    fb = C.record_feedback(contract=c, item_id="API1", expected="200", actual="500",
                           phase="qa", run_id="R1", severity="blocking", feedback="500 on empty db")
    assert fb.contract_id == "C1" and fb.item_id == "API1"
    assert fb.expected == "200" and fb.actual == "500" and fb.severity == "blocking"
