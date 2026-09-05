"""Playwright evidence capture (v2 Phase 2 — plan 06 §3). Availability-guarded: if Playwright or
its chromium browser is missing/broken, capture returns nothing and records WHY — the harness then
runs at the no-browser rung. Capture must NEVER raise into the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Capture:
    screenshots: list[Path] = field(default_factory=list)  # (path, criterion_id) pairs via meta below
    screenshot_meta: list[dict] = field(default_factory=list)
    video: Path | None = None
    console_errors: list[str] = field(default_factory=list)
    ok: bool = False
    detail: str = ""


# A MISSING STATIC ASSET (favicon, icon, image, font) logs a console error that is NOT a code defect.
# But a failed API/data fetch — a real backend bug — MUST be kept, or QA would greenlight a broken app.
# We decide from the FAILING RESOURCE URL when the browser gives it; a real JS bug also surfaces as an
# uncaught pageerror, which we always keep.
_BENIGN_URL_MARKERS = ("favicon", "apple-touch-icon")
_STATIC_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
               ".woff", ".woff2", ".ttf", ".otf", ".map", ".css")
_API_URL_MARKERS = ("/api/", "/graphql", ".json")


def _is_benign_console_error(text: str, url: str = "") -> bool:
    u = (url or "").lower()
    if u:
        if any(m in u for m in _API_URL_MARKERS):
            return False  # an API/data failure is a real defect — never drop it
        base = u.split("?", 1)[0]
        if any(m in u for m in _BENIGN_URL_MARKERS) or any(base.endswith(e) for e in _STATIC_EXT):
            return True
        return False  # unknown resource — err on the side of KEEPING the error
    # No URL on the message → only the unambiguous favicon/icon phrasing counts as benign.
    return any(m in (text or "").lower() for m in _BENIGN_URL_MARKERS)


async def browser_available() -> tuple[bool, str]:
    """Probe once: Playwright import + a launchable chromium. Cheap, cached by the caller."""
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright not importable: {exc}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await browser.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001 — chromium not installed / sandbox denies launch
        return False, f"chromium unavailable (run `playwright install chromium`): {str(exc)[:120]}"


async def capture_screens(
    base_url: str, routes: list[tuple[str, str | None]], out_dir: Path, *,
    viewport=(1440, 900), scale: int = 2, record_video: bool = False, video_max_seconds: int = 30,
) -> Capture:
    """Full-page screenshot of each (route, criterion_id). Optional short video of the first route.
    Returns a :class:`Capture`; on ANY failure returns ``ok=False`` with a reason (never raises)."""
    cap = Capture()
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        cap.detail = f"playwright not importable: {exc}"
        return cap

    (out_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    video_dir = out_dir / "video"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx_kwargs: dict = {"viewport": {"width": viewport[0], "height": viewport[1]},
                                    "device_scale_factor": scale}
                if record_video:
                    video_dir.mkdir(parents=True, exist_ok=True)
                    ctx_kwargs["record_video_dir"] = str(video_dir)
                    ctx_kwargs["record_video_size"] = {"width": 1280, "height": 720}
                context = await browser.new_context(**ctx_kwargs)
                page = await context.new_page()

                def _on_console(m) -> None:
                    # Real JS defects come through here (console.error) AND via pageerror below. Drop only
                    # a missing STATIC asset (favicon/icon/image) — decided from the failing resource URL,
                    # so a real API/data failure is kept and can still fail QA.
                    if m.type != "error":
                        return
                    loc = m.location if isinstance(m.location, dict) else {}
                    if _is_benign_console_error(m.text or "", loc.get("url", "")):
                        return
                    cap.console_errors.append(m.text or "")

                page.on("console", _on_console)
                page.on("pageerror", lambda e: cap.console_errors.append(str(e)))

                for seq, (route, crit_id) in enumerate(routes or [("/", None)], start=1):
                    url = base_url.rstrip("/") + "/" + route.lstrip("/")
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=15000)
                    except Exception:  # noqa: BLE001 — one bad route mustn't lose the others
                        try:
                            await page.goto(url, timeout=15000)
                        except Exception:  # noqa: BLE001 — still screenshot what's on the page, then move on
                            pass
                    slug = (crit_id or route.strip("/") or "home").replace("/", "-")[:40]
                    shot = out_dir / "screenshots" / f"{seq:02d}_{slug}.png"
                    await page.screenshot(path=str(shot), full_page=True)
                    cap.screenshots.append(shot)
                    cap.screenshot_meta.append({"path": shot, "route": route, "criterion_id": crit_id, "seq": seq})

                # Video finalizes only on context.close() — close BEFORE reading the path.
                vid = page.video if record_video else None
                await context.close()
                if vid is not None:
                    with_path = await vid.path()
                    if with_path:
                        cap.video = Path(with_path)
            finally:
                await browser.close()
        cap.ok = True
    except Exception as exc:  # noqa: BLE001 — capture failure degrades, never fails the run
        cap.detail = f"chromium unavailable/crashed: {str(exc)[:160]}"
    return cap
