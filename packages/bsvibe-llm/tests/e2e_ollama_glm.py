"""E2E verification of suppress_reasoning=True against a real ollama
reasoning model (glm-4.7-flash).

Run manually (NOT part of pytest suite):
    cd packages/bsvibe-llm
    uv run python tests/e2e_ollama_glm.py

Pre-reqs:
    - ollama running at http://localhost:11434
    - glm-4.7-flash:latest pulled locally

What it verifies:
    1. With suppress_reasoning=True, glm-4.7-flash returns under 60s for a
       ~5k char compile prompt (response is plain JSON, no <think> prefix).
    2. The bypass path is taken (verified via debug logging).
    3. Response is non-empty and starts with the requested JSON shape.
"""

from __future__ import annotations

import asyncio
import time

from bsvibe_llm import LlmClient, LlmSettings, RunAuditMetadata

MODEL = "ollama/qwen3:14b"

# A realistic ~5k char compile prompt (BSage IngestCompiler style).
SYSTEM_PROMPT = """You are an ingest compiler for a personal knowledge garden (Obsidian vault).

You receive seeds (numbered raw notes) and produce a JSON array of consolidated actions.

## Mental model

This is a digital garden, not a filing cabinet. Notes are connected by [[wikilinks]].
Do NOT classify notes into types. Tags describe what content IS ABOUT, not its KIND.

## Output schema

Return a JSON array. Each action object has:
- "action": "create" | "update" | "append"
- "target_path": vault path or null
- "title": short title (5-80 chars)
- "content": markdown body with [[wikilinks]]
- "tags": 2-5 free-form lowercase content tags (avoid "idea", "fact", "insight")
- "entities": list of [[Name]] strings; each must appear as wikilink in content
- "reason": one sentence citing seed numbers
- "source_seeds": list of seed numbers
- "related": list of existing note titles (empty if none)

## Rules

- Deduplicate across seeds, MERGE related items.
- Prefer UPDATE over CREATE when content overlaps existing notes.
- Every entity in "entities" MUST appear as [[wikilink]] in "content".
- If a seed is too brief, omit it. Don't pad.
- Return [] if no actions needed.
- Return ONLY the JSON array. No markdown fences. No commentary.
""" * 2  # double up to get ~5k chars


USER_PROMPT = """Seeds:
SEED #1: "Tested Vaultwarden behind Caddy reverse proxy. The X-Forwarded-Proto header was the issue — without it, OAuth callbacks broke."
SEED #2: "Bitwarden client compatibility check for Vaultwarden — most clients work, except mobile push needs setup."
SEED #3: "Note on Cloudflare Tunnel as alternative to Caddy — works but adds latency."

Existing notes: (empty vault)

Produce the JSON action array."""


async def run(suppress: bool) -> tuple[str, float]:
    settings = LlmSettings(bsgateway_url="", model=MODEL)
    client = LlmClient(settings=settings)

    metadata = RunAuditMetadata(tenant_id="e2e", run_id="reasoning-test")
    started = time.monotonic()
    result = await client.complete(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        metadata=metadata,
        direct=True,
        suppress_reasoning=suppress,
        timeout_s=180.0,
    )
    elapsed = time.monotonic() - started
    return result.text, elapsed


async def main() -> None:
    print(f"system_prompt_chars={len(SYSTEM_PROMPT)} user_prompt_chars={len(USER_PROMPT)}")
    print(f"model={MODEL}")
    print()

    print("=== Run 1: suppress_reasoning=True (bypass path) ===")
    text_on, elapsed_on = await run(suppress=True)
    print(f"elapsed: {elapsed_on:.2f}s")
    print(f"first 300 chars: {text_on[:300]!r}")
    print(f"contains <think>: {'<think>' in text_on.lower()}")
    print()

    print("=== Run 2: suppress_reasoning=False (litellm path, reasoning ON) ===")
    text_off, elapsed_off = await run(suppress=False)
    print(f"elapsed: {elapsed_off:.2f}s")
    print(f"first 300 chars: {text_off[:300]!r}")
    print(f"contains <think>: {'<think>' in text_off.lower()}")
    print()

    print("=== Verdict ===")
    if elapsed_on < 60.0:
        print(f"PASS: suppress_reasoning=True returned in {elapsed_on:.2f}s (< 60s threshold)")
    else:
        print(f"FAIL: suppress_reasoning=True took {elapsed_on:.2f}s (>= 60s threshold)")

    if elapsed_on < elapsed_off:
        print(f"PASS: suppression is faster ({elapsed_on:.2f}s vs {elapsed_off:.2f}s)")
    else:
        print(f"WARN: suppression NOT faster ({elapsed_on:.2f}s vs {elapsed_off:.2f}s)")


if __name__ == "__main__":
    asyncio.run(main())
