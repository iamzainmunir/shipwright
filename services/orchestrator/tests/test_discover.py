"""Endpoint discovery — OpenAPI parsing and static source scanning (v2 Phase 2).

OpenAPI tests serve a tiny in-process Starlette app over ``httpx.ASGITransport`` (no network, no
port). Source tests build a throwaway repo under ``tmp_path``. Every entry point must degrade to []
rather than raise.
"""

from __future__ import annotations

import httpx
from app.qa_harness.discover import (
    Endpoint,
    discover_endpoints,
    discover_from_openapi,
    discover_from_source,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


def _openapi_app(doc: dict, *, at: str = "/openapi.json") -> Starlette:
    """Serve ``doc`` at one well-known location; every other path 404s (Starlette default)."""
    async def spec(_request):
        return JSONResponse(doc)
    return Starlette(routes=[Route(at, spec)])


def _client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _write(base, rel: str, text: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---- OpenAPI ------------------------------------------------------------------

async def test_openapi_parses_paths_and_ignores_metadata():
    doc = {
        "openapi": "3.1.0",
        "paths": {
            "/api/tasks": {"get": {}, "post": {}},
            "/api/tasks/{id}": {"get": {}, "delete": {}, "parameters": []},
        },
    }
    async with _client(_openapi_app(doc)) as c:
        eps = await discover_from_openapi("http://test", client=c)
    got = {(e.method, e.path) for e in eps}
    assert got == {
        ("GET", "/api/tasks"), ("POST", "/api/tasks"),
        ("GET", "/api/tasks/{id}"), ("DELETE", "/api/tasks/{id}"),
    }
    assert all(e.source == "openapi" for e in eps)
    assert not any(e.method == "PARAMETERS" for e in eps)  # non-verb key ignored


async def test_openapi_falls_back_to_swagger_json():
    doc = {"paths": {"/health": {"get": {}}}}
    async with _client(_openapi_app(doc, at="/swagger.json")) as c:
        eps = await discover_from_openapi("http://test", client=c)
    assert {(e.method, e.path) for e in eps} == {("GET", "/health")}


async def test_openapi_falls_back_to_api_docs():
    doc = {"paths": {"/ping": {"get": {}}}}
    async with _client(_openapi_app(doc, at="/api-docs")) as c:
        eps = await discover_from_openapi("http://test", client=c)
    assert {(e.method, e.path) for e in eps} == {("GET", "/ping")}


async def test_openapi_no_paths_returns_empty_without_raising():
    async with _client(_openapi_app({"openapi": "3.1.0"})) as c:
        eps = await discover_from_openapi("http://test", client=c)
    assert eps == []


async def test_openapi_never_raises_on_bad_base_url():
    # No client → creates its own; a schemeless URL makes httpx raise, which must be swallowed.
    eps = await discover_from_openapi("not-a-real-url")
    assert eps == []


# ---- Source scan --------------------------------------------------------------

def test_source_discovers_fastapi(tmp_path):
    _write(tmp_path, "app/routes.py",
           "router = APIRouter()\n"
           '@router.get("/api/tasks")\n'
           "async def list_tasks(): ...\n"
           '@app.post("/api/tasks")\n'
           "def create(): ...\n"
           "@api.delete('/api/tasks/{id}')\n"
           "def remove(): ...\n")
    got = {(e.method, e.path, e.source) for e in discover_from_source(str(tmp_path))}
    assert ("GET", "/api/tasks", "fastapi") in got
    assert ("POST", "/api/tasks", "fastapi") in got
    assert ("DELETE", "/api/tasks/{id}", "fastapi") in got


def test_source_discovers_express(tmp_path):
    _write(tmp_path, "server.js",
           "const app = express();\n"
           "app.get('/health', (req, res) => res.send('ok'));\n"
           'router.post("/api/login", handler);\n'
           "app.put('/api/users/:id', update);\n"
           "myapp.get('/should-not-match', h);\n")  # 'myapp' is not the app/router token
    got = {(e.method, e.path, e.source) for e in discover_from_source(str(tmp_path))}
    assert ("GET", "/health", "express") in got
    assert ("POST", "/api/login", "express") in got
    assert ("PUT", "/api/users/:id", "express") in got
    assert not any(e.path == "/should-not-match" for e in discover_from_source(str(tmp_path)))


def test_source_discovers_nextjs_and_converts_dynamic_segment(tmp_path):
    _write(tmp_path, "src/app/api/tasks/[id]/route.ts",
           "export async function GET(req) { return Response.json({}); }\n"
           "export const DELETE = async () => new Response(null, { status: 204 });\n")
    _write(tmp_path, "src/app/api/tasks/route.ts",
           "export async function GET() {}\n"
           "export async function POST() {}\n")
    got = {(e.method, e.path, e.source) for e in discover_from_source(str(tmp_path))}
    assert ("GET", "/api/tasks/{id}", "nextjs") in got      # [id] → {id}
    assert ("DELETE", "/api/tasks/{id}", "nextjs") in got   # export const form
    assert ("GET", "/api/tasks", "nextjs") in got
    assert ("POST", "/api/tasks", "nextjs") in got


def test_source_skips_bad_files_and_excluded_dirs(tmp_path):
    # An undecodable file must not raise and must simply yield nothing.
    (tmp_path / "weird.py").write_bytes(b"\xff\xfe\x00@app.get(\x00broken")
    # Routes inside a pruned dir must never surface.
    _write(tmp_path, "node_modules/pkg/index.js", "app.get('/vendored', h)\n")
    _write(tmp_path, "good.py", '@app.get("/ok")\ndef f(): ...\n')
    paths = {e.path for e in discover_from_source(str(tmp_path))}
    assert "/ok" in paths
    assert "/vendored" not in paths


def test_source_scan_on_missing_dir_returns_empty():
    assert discover_from_source("/definitely/not/here/xyz") == []


# ---- Union --------------------------------------------------------------------

async def test_discover_endpoints_unions_and_dedups(tmp_path):
    _write(tmp_path, "app/routes.py", '@app.get("/api/tasks")\ndef f(): ...\n')
    doc = {"paths": {"/api/tasks": {"get": {}}, "/openapi/extra": {"get": {}}}}
    async with _client(_openapi_app(doc)) as c:
        eps = await discover_endpoints(str(tmp_path), base_url="http://test", client=c)
    keys = [(e.method, e.path) for e in eps]
    assert keys.count(("GET", "/api/tasks")) == 1     # deduped across sources
    assert ("GET", "/openapi/extra") in keys
    dup = next(e for e in eps if e.method == "GET" and e.path == "/api/tasks")
    assert dup.source == "openapi"                     # OpenAPI added first → wins the tie


async def test_discover_endpoints_source_only_without_base_url(tmp_path):
    _write(tmp_path, "server.js", "app.get('/x', h)\n")
    eps = await discover_endpoints(str(tmp_path))
    assert ("GET", "/x") in {(e.method, e.path) for e in eps}


async def test_discover_endpoints_never_raises_on_bad_path():
    assert await discover_endpoints("/nonexistent/path/xyz") == []


def test_endpoint_dataclass_defaults():
    ep = Endpoint("GET", "/x")
    assert ep.source == ""
