"""Smoke-test the local Ollama models through Shipwright's OllamaProvider.

Runs the same prompt through every model listed (default: qwen2.5:7b and gemma2:2b) and prints
the reply plus token counts — a quick end-to-end check that the local-LLM adapter works.

    uv run python scripts/try_ollama.py
    uv run python scripts/try_ollama.py llama3.1:8b   # override models via argv
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable standalone

from app.providers.ollama import OllamaProvider  # noqa: E402

SYSTEM = "You are a senior software engineer. Reply in ONE short, concrete sentence."
PROMPT = "What does 'tenant scoping' mean for a by-id read in a multi-tenant API?"


async def main() -> int:
    models = sys.argv[1:] or ["qwen2.5:7b", "gemma2:2b"]
    failures = 0
    for model in models:
        provider = OllamaProvider(model=model)
        print(f"\n=== {model} ===")
        started = time.monotonic()
        try:
            result = await provider.complete(system=SYSTEM, prompt=PROMPT, max_tokens=120)
        except Exception as exc:  # noqa: BLE001 - a smoke test reports any failure plainly
            print(f"  ERROR: {exc}")
            failures += 1
            continue
        elapsed = time.monotonic() - started
        print(f"  {result.text.strip()}")
        print(f"  [tokens in={result.tokens_in} out={result.tokens_out} · {elapsed:.1f}s · cost={result.cost_cents}¢]")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
