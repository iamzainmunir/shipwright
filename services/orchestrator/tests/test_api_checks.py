"""API contract checks — real HTTP against an in-process app (v2 Phase 2).

A tiny Starlette app is served over ``httpx.ASGITransport`` and injected into ``api_checks`` via its
``client`` param, so the checks exercise a real request/response cycle with no network or port.
"""

from __future__ import annotations

import httpx
from app.qa_harness.api_checks import api_checks
from app.qa_harness.checks import CheckResult
from app.qa_harness.discover import Endpoint
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route


async def _list_tasks(_request):
    return JSONResponse({"items": [{"id": 1, "title": "x"}]})


async def _no_keys(_request):
    return JSONResponse({"foo": 1})


async def _listing(_request):
    return JSONResponse([{"id": 1}, {"id": 2}])


async def _bad_list(_request):
    return JSONResponse([{"id": 1}, {"name": "no id"}])


async def _echo_crashes(request):
    # A defect on purpose: the handler blows up on invalid JSON and answers 500 instead of 4xx.
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return PlainTextResponse("boom", status_code=500)
    return JSONResponse(data)


async def _create_ok(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return PlainTextResponse("bad request", status_code=400)  # well-behaved rejection
    return JSONResponse(data, status_code=201)


async def _html_error(_request):
    return HTMLResponse(
        "<html><body>Traceback (most recent call last): KeyError</body></html>", status_code=500,
    )


def _app() -> Starlette:
    return Starlette(routes=[
        Route("/api/tasks", _list_tasks, methods=["GET"]),
        Route("/api/nokeys", _no_keys, methods=["GET"]),
        Route("/api/listing", _listing, methods=["GET"]),
        Route("/api/badlist", _bad_list, methods=["GET"]),
        Route("/api/echo", _echo_crashes, methods=["POST"]),
        Route("/api/items", _create_ok, methods=["POST"]),
        Route("/api/htmlerror", _html_error, methods=["GET"]),
    ])


async def _catch_all(_request):
    return JSONResponse({"ok": True})  # answers 200 to EVERYTHING — the wildcard defect


def _wildcard_app() -> Starlette:
    return Starlette(routes=[Route("/{path:path}", _catch_all, methods=["GET"])])


def _client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _by_id(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.id: r for r in results}


# ---- happy path ---------------------------------------------------------------

async def test_conformant_endpoint_passes_status_shape_content_type():
    contract = [{"id": "tasks", "criterion": "list tasks", "method": "GET", "path": "/api/tasks",
                 "response": {"status": 200, "contentType": "application/json", "requiredKeys": ["items"]}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    by = _by_id(res)
    assert by["api:tasks:status"].status == "pass"
    assert by["api:tasks:content-type"].status == "pass"
    assert by["api:tasks:shape"].status == "pass"
    assert by["api:tasks:unknown-route"].status == "pass"  # sibling 404s


async def test_required_keys_over_list_body():
    contract = [{"id": "ls", "method": "GET", "path": "/api/listing",
                 "response": {"status": 200, "requiredKeys": ["id"]}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    assert _by_id(res)["api:ls:shape"].status == "pass"  # every element carries "id"


async def test_status_defaults_to_any_2xx_when_unset():
    contract = [{"id": "any", "method": "GET", "path": "/api/tasks", "response": {}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    assert _by_id(res)["api:any:status"].status == "pass"


# ---- failure detection --------------------------------------------------------

async def test_wrong_status_fails():
    contract = [{"id": "t2", "method": "GET", "path": "/api/tasks", "response": {"status": 201}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    assert _by_id(res)["api:t2:status"].status == "fail"


async def test_missing_required_key_fails():
    contract = [{"id": "nk", "method": "GET", "path": "/api/nokeys",
                 "response": {"status": 200, "requiredKeys": ["items"]}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    shape = _by_id(res)["api:nk:shape"]
    assert shape.status == "fail"
    assert "items" in shape.detail


async def test_missing_key_in_list_element_fails():
    contract = [{"id": "bl", "method": "GET", "path": "/api/badlist",
                 "response": {"status": 200, "requiredKeys": ["id"]}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    assert _by_id(res)["api:bl:shape"].status == "fail"


async def test_500_on_malformed_input_is_a_failure_with_allow_mutations():
    contract = [{"id": "echo", "method": "POST", "path": "/api/echo",
                 "request": {"example": {"a": 1}}, "response": {"status": 200}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c, allow_mutations=True)
    by = _by_id(res)
    assert by["api:echo:status"].status == "pass"          # POST example → 200
    assert by["api:echo:malformed-body"].status == "fail"  # invalid JSON → 500 = crash


async def test_well_behaved_malformed_handling_passes():
    contract = [{"id": "items", "method": "POST", "path": "/api/items",
                 "request": {"example": {"a": 1}}, "response": {"status": 201}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c, allow_mutations=True)
    by = _by_id(res)
    assert by["api:items:status"].status == "pass"
    assert by["api:items:malformed-body"].status == "pass"  # invalid JSON → 400 = graceful


async def test_html_stack_trace_error_body_warns():
    contract = [{"id": "he", "method": "GET", "path": "/api/htmlerror", "response": {"status": 500}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    by = _by_id(res)
    assert by["api:he:status"].status == "pass"       # 500 was declared, so status matches
    assert by["api:he:error-body"].status == "warn"   # leaked HTML/traceback


async def test_unknown_route_returning_200_is_a_failure():
    contract = [{"id": "w", "method": "GET", "path": "/api/thing", "response": {"status": 200}}]
    async with _client(_wildcard_app()) as c:
        res = await api_checks("http://test", contract, client=c)
    by = _by_id(res)
    assert by["api:w:status"].status == "pass"                 # catch-all answers the real path too
    assert by["api:w:unknown-route"].status == "fail"
    assert "catch-all" in by["api:w:unknown-route"].detail


# ---- safety + coverage --------------------------------------------------------

async def test_mutation_is_skipped_in_safe_mode():
    contract = [{"id": "m", "method": "POST", "path": "/api/echo",
                 "request": {"example": {"a": 1}}, "response": {"status": 200}}]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, client=c)  # allow_mutations defaults False
    status = _by_id(res)["api:m:status"]
    assert status.status == "skipped"
    assert "safe mode" in status.detail
    # No malformed-body probe should have fired either.
    assert "api:m:malformed-body" not in _by_id(res)


async def test_discovered_endpoint_not_in_contract_warns():
    contract = [{"id": "tasks", "method": "GET", "path": "/api/tasks", "response": {"status": 200}}]
    endpoints = [Endpoint("GET", "/api/tasks", "openapi"), Endpoint("GET", "/api/orphan", "openapi")]
    async with _client(_app()) as c:
        res = await api_checks("http://test", contract, endpoints=endpoints, client=c)
    uncovered = [r for r in res if r.detail == "discovered endpoint not in contract"]
    assert len(uncovered) == 1
    assert uncovered[0].status == "warn"
    assert uncovered[0].id == "api:uncovered:GET:/api/orphan"  # the covered /api/tasks is not flagged


# ---- never raises -------------------------------------------------------------

async def test_api_checks_never_raises_on_broken_base_url():
    # No client → own client is created; a schemeless URL makes every request raise, which must be
    # caught and reported as warns, not propagated.
    res = await api_checks("not-a-real-url",
                           [{"id": "x", "method": "GET", "path": "/a", "response": {"status": 200}}])
    assert isinstance(res, list)
    assert all(isinstance(r, CheckResult) for r in res)
    assert any(r.status == "warn" for r in res)


async def test_missing_method_or_path_warns_not_raises():
    async with _client(_app()) as c:
        res = await api_checks("http://test", [{"id": "bad", "path": "/api/tasks"}], client=c)
    warn = _by_id(res)["api:bad:status"]
    assert warn.status == "warn"
    assert "missing method/path" in warn.detail


async def test_empty_contract_returns_empty_list():
    async with _client(_app()) as c:
        assert await api_checks("http://test", [], client=c) == []
