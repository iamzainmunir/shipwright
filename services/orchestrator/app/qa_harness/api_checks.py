"""Real HTTP contract checks for the QA harness (v2 Phase 2 — API-contract checks).

Issue actual requests against a served app and grade each contract item: status, content-type,
response shape, plus always-on *error hygiene* (unknown routes 404, malformed input is rejected not
crashed, error bodies aren't leaked stack traces). Output is :class:`CheckResult` — advisory input to
the QA verdict, never authoritative.

Two hard rules mirror the rest of the harness:
- SAFE BY DEFAULT: only GET/HEAD/OPTIONS fire unless ``allow_mutations=True``; an undeclared mutating
  verb is a *skip*, never a live POST/DELETE against someone's data.
- NEVER RAISE into a run: every request is wrapped; a transport/parse error becomes a warn, and a
  total failure returns a single warn CheckResult.
"""

from __future__ import annotations

import httpx

from .checks import CheckResult
from .discover import Endpoint, _normalize_path

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# A path we can be confident does not exist — used to prove a route is not a 200-everything catch-all.
_NONEXISTENT_SUFFIX = "/__shipwright_nonexistent__"
# Malformed JSON: an unterminated object. A well-behaved API answers 4xx; a 500 means it crashed.
_MALFORMED_BODY = '{"__bad": '


def _join(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _missing_keys(body: object, required: list[str]) -> list[str]:
    """Which required top-level keys are absent. For a list body, every element must carry them (a
    collection endpoint's items should be uniformly shaped); a scalar body can carry none."""
    if isinstance(body, list):
        missing: set[str] = set()
        for el in body:
            if not isinstance(el, dict):
                return list(required)  # a non-object element cannot satisfy key requirements
            missing.update(k for k in required if k not in el)
        return sorted(missing)
    if isinstance(body, dict):
        return [k for k in required if k not in body]
    return list(required)


def _looks_like_stack_trace(text: str) -> bool:
    """A leaked HTML error page or Python traceback in an error body — a defect worth flagging."""
    return "<html" in text.lower() or "Traceback (most recent call last)" in text


def _error_body_check(cid: str, resp: httpx.Response) -> CheckResult:
    """Advisory: an error response should be a clean JSON message, not a rendered stack trace."""
    try:
        text = resp.text[:4000]
    except Exception:  # noqa: BLE001 — undecodable body → nothing to flag
        text = ""
    leaked = _looks_like_stack_trace(text)
    return CheckResult(
        cid, "error body is not a stack trace", "warn" if leaked else "pass",
        "error body leaks an HTML page / stack trace" if leaked else "error body is clean",
    )


async def _send(
    client: httpx.AsyncClient, method: str, url: str, *,
    headers: dict | None = None, json_body: object = None, content: str | None = None,
    auth: object = None,
) -> httpx.Response:
    kwargs: dict = {}
    if headers:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth  # type: ignore[assignment]
    if content is not None:
        kwargs["content"] = content
    elif json_body is not None:
        kwargs["json"] = json_body
    return await client.request(method, url, **kwargs)


def _auth_and_headers(req_spec: dict) -> tuple[dict, object]:
    """Translate the contract's optional ``request.headers`` / ``request.auth`` into httpx inputs.
    A 2-tuple/list → basic auth; a string → a Bearer header. Defensive: an unusable value is ignored."""
    headers = dict(req_spec.get("headers") or {}) if isinstance(req_spec.get("headers"), dict) else {}
    auth: object = None
    raw = req_spec.get("auth")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        auth = tuple(raw)
    elif isinstance(raw, str) and raw:
        headers["Authorization"] = raw if raw.lower().startswith("bearer") else f"Bearer {raw}"
    return headers, auth


async def _check_item(
    client: httpx.AsyncClient, base_url: str, item: dict, idx: int, allow_mutations: bool,
) -> list[CheckResult]:
    """Grade one contract item. Isolated: any unexpected error degrades to a single warn."""
    out: list[CheckResult] = []
    try:
        if not isinstance(item, dict):
            return [CheckResult(f"api:item{idx}:status", "api check", "warn", "contract item is not an object")]

        item_id = str(item.get("id") or f"item{idx}")
        method = str(item.get("method") or "").upper()
        path = str(item.get("path") or "")
        crit_id = str(item["id"]) if item.get("id") else None
        crit_name = str(item.get("criterion") or f"{method} {path}".strip())
        if not method or not path:
            return [CheckResult(f"api:{item_id}:status", crit_name, "warn",
                                "contract item missing method/path", criterion_id=crit_id)]

        resp_spec = item.get("response") if isinstance(item.get("response"), dict) else {}
        req_spec = item.get("request") if isinstance(item.get("request"), dict) else {}
        headers, auth = _auth_and_headers(req_spec)
        url = _join(base_url, path)

        is_safe = method in _SAFE_METHODS
        is_mutation = method in _MUTATING_METHODS

        # 1 + 2: SAFETY gate, then the primary status/shape checks on the declared request.
        resp: httpx.Response | None = None
        if is_mutation and not allow_mutations:
            out.append(CheckResult(f"api:{item_id}:status", crit_name, "skipped",
                                   "mutation not run (safe mode)", criterion_id=crit_id))
        elif not is_safe and not is_mutation:
            out.append(CheckResult(f"api:{item_id}:status", crit_name, "skipped",
                                   f"unsupported method {method!r} not run", criterion_id=crit_id))
        else:
            body = req_spec.get("example") if is_mutation else None
            try:
                resp = await _send(client, method, url, headers=headers, json_body=body, auth=auth)
            except Exception as exc:  # noqa: BLE001 — a transport error is data, not a crash
                out.append(CheckResult(f"api:{item_id}:status", crit_name, "warn",
                                       f"request failed: {str(exc)[:160]}", criterion_id=crit_id))
                resp = None
            else:
                out.append(_status_check(item_id, crit_name, crit_id, method, path, resp_spec, resp))
                if resp_spec.get("contentType"):
                    out.append(_content_type_check(item_id, resp_spec, resp))
                if resp_spec.get("requiredKeys"):
                    out.append(_shape_check(item_id, resp_spec, resp))
                if resp.status_code >= 400:
                    out.append(_error_body_check(f"api:{item_id}:error-body", resp))

        # 5: error hygiene — always best-effort, advisory.
        out.extend(await _unknown_route_check(client, base_url, item_id, path))
        if allow_mutations and method in ("POST", "PUT", "PATCH"):
            out.extend(await _malformed_body_check(client, item_id, method, url, headers, auth))
        return out
    except Exception as exc:  # noqa: BLE001 — one item must never sink the whole check pass
        return [CheckResult(f"api:item{idx}:status", "api check", "warn", f"check error: {str(exc)[:160]}")]


def _status_check(
    item_id: str, name: str, crit_id: str | None, method: str, path: str, resp_spec: dict,
    resp: httpx.Response,
) -> CheckResult:
    """Assert the status code. An explicit ``response.status`` must match exactly; unset → any 2xx."""
    raw = resp_spec.get("status")
    try:
        want = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        want = None
    actual = resp.status_code
    if want is not None:
        ok = actual == want
        detail = f"{method} {path} → {actual} (expected {want})"
    else:
        ok = 200 <= actual < 300
        detail = f"{method} {path} → {actual} (expected 2xx)"
    return CheckResult(f"api:{item_id}:status", name, "pass" if ok else "fail", detail, criterion_id=crit_id)


def _content_type_check(item_id: str, resp_spec: dict, resp: httpx.Response) -> CheckResult:
    """Advisory: the Content-Type header should start with the declared media type."""
    want = str(resp_spec.get("contentType"))
    got = resp.headers.get("content-type", "")
    ok = got.lower().startswith(want.lower())
    return CheckResult(f"api:{item_id}:content-type", "content-type", "pass" if ok else "warn",
                       f"content-type={got!r} (expected prefix {want!r})")


def _shape_check(item_id: str, resp_spec: dict, resp: httpx.Response) -> CheckResult:
    """Assert every declared key is present at the top level (or in each element of a list body)."""
    required = [str(k) for k in (resp_spec.get("requiredKeys") or [])]
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a required shape over a non-JSON body is a defect
        return CheckResult(f"api:{item_id}:shape", "response shape", "fail", "response body is not valid JSON")
    missing = _missing_keys(body, required)
    return CheckResult(f"api:{item_id}:shape", "response shape", "pass" if not missing else "fail",
                       "all required keys present" if not missing else f"missing keys: {missing}")


async def _unknown_route_check(
    client: httpx.AsyncClient, base_url: str, item_id: str, path: str,
) -> list[CheckResult]:
    """A GET on a sibling path that cannot exist should 404. A 2xx means a catch-all/wildcard is
    swallowing every route (a real defect); other codes are advisory. Skipped for templated paths
    (a literal ``{id}`` request is meaningless)."""
    if "{" in path or "}" in path:
        return []
    sib = _join(base_url, _normalize_path(path) + _NONEXISTENT_SUFFIX)
    cid = f"api:{item_id}:unknown-route"
    try:
        r = await _send(client, "GET", sib)
    except Exception as exc:  # noqa: BLE001 — probe failure is advisory
        return [CheckResult(cid, "unknown route 404s", "warn", f"probe failed: {str(exc)[:120]}")]
    sc = r.status_code
    if 200 <= sc < 300:
        return [CheckResult(cid, "unknown route 404s", "fail",
                            f"route wildcard/catch-all returns {sc} on a nonexistent path")]
    if sc == 404:
        return [CheckResult(cid, "unknown route 404s", "pass", "nonexistent path → 404")]
    return [CheckResult(cid, "unknown route 404s", "warn", f"nonexistent path → {sc} (expected 404)")]


async def _malformed_body_check(
    client: httpx.AsyncClient, item_id: str, method: str, url: str, headers: dict, auth: object,
) -> list[CheckResult]:
    """Send invalid JSON to a declared mutating endpoint: it must reject with a 4xx, never 500. A 500
    means the handler crashes on untrusted input. Only runs under ``allow_mutations``."""
    cid = f"api:{item_id}:malformed-body"
    bad_headers = {**headers, "content-type": "application/json"}
    try:
        r = await _send(client, method, url, headers=bad_headers, content=_MALFORMED_BODY, auth=auth)
    except Exception as exc:  # noqa: BLE001 — probe failure is advisory
        return [CheckResult(cid, "handles malformed input", "warn", f"probe failed: {str(exc)[:120]}")]
    out: list[CheckResult] = []
    sc = r.status_code
    if 500 <= sc < 600:
        out.append(CheckResult(cid, "handles malformed input", "fail", f"crashes on malformed input (HTTP {sc})"))
    elif 400 <= sc < 500:
        out.append(CheckResult(cid, "handles malformed input", "pass", f"rejects malformed input (HTTP {sc})"))
    else:
        out.append(CheckResult(cid, "handles malformed input", "warn", f"malformed input → {sc} (expected 4xx)"))
    if sc >= 400:
        out.append(_error_body_check(f"api:{item_id}:malformed-body-error", r))
    return out


async def api_checks(
    base_url: str, contract_items: list[dict], endpoints: list[Endpoint] | None = None, *,
    allow_mutations: bool = False, client: httpx.AsyncClient | None = None,
) -> list[CheckResult]:
    """Grade every contract item against the served app, and flag discovered endpoints the contract
    forgot (advisory warn — never a failure). Pass ``client`` to inject a transport (tests use an
    ASGI client); otherwise a 10s follow-redirects client is created and closed here. Never raises."""
    try:
        own = client is None
        cl = client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        try:
            out: list[CheckResult] = []
            covered: set[tuple[str, str]] = set()
            for idx, item in enumerate(contract_items):
                out.extend(await _check_item(cl, base_url, item, idx, allow_mutations))
                if isinstance(item, dict):
                    m = str(item.get("method") or "").upper()
                    p = item.get("path")
                    if m and p:
                        covered.add((m, _normalize_path(str(p))))
            # Coverage gap: a route the app exposes but the contract never asserts. Advisory only —
            # missing coverage is a hint to the author, not a defect in the app under test.
            for ep in endpoints or []:
                if (ep.method.upper(), _normalize_path(ep.path)) not in covered:
                    out.append(CheckResult(
                        f"api:uncovered:{ep.method.upper()}:{ep.path}",
                        f"{ep.method.upper()} {ep.path} coverage", "warn",
                        "discovered endpoint not in contract"))
            return out
        finally:
            if own:
                await cl.aclose()
    except Exception as exc:  # noqa: BLE001 — the harness must never fail the run
        return [CheckResult("api:harness", "api checks", "warn", f"api_checks error: {str(exc)[:160]}")]
