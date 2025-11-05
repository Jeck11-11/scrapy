# extras/link_contact_api.py
from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl, conint, confloat

from .link_contact_extractor import analyse_url  # relative import


# ---------- Request / Response models ----------

class ScanRequest(BaseModel):
    urls: List[HttpUrl]
    user_agent: Optional[str] = None
    timeout: Optional[confloat(gt=0)] = 15.0
    concurrency: Optional[conint(gt=0)] = None  # if omitted we auto-pick


# ---------- App ----------

app = FastAPI(title="Link/Contact Extractor API", version="1.0.0")


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "link-contact-extractor",
        "usage": "POST /scan with JSON: { 'urls': ['https://example.com', ...] }",
    }


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}


@app.post("/scan")
async def scan(payload: ScanRequest):
    urls = [str(u) for u in payload.urls]
    if not urls:
        raise HTTPException(status_code=400, detail="Payload must include a non-empty 'urls' list.")

    # Pick an effective worker count
    max_workers = 32
    workers = min(payload.concurrency or max_workers, max_workers, len(urls)) or 1
    timeout = float(payload.timeout or 15.0)
    user_agent = payload.user_agent

    async def _scan_one(index: int, url: str):
        start = perf_counter()
        try:
            # Run blocking extraction in a worker thread
            result = await asyncio.to_thread(
                analyse_url, url, user_agent=user_agent, timeout=timeout
            )
            elapsed_ms = round((perf_counter() - start) * 1000, 2)
            ok: Dict[str, Any] = {"status": "ok", "elapsed_ms": elapsed_ms}
            ok.update(result)
            return index, ok
        except Exception as exc:
            return index, {"status": "error", "error": str(exc), "input_url": url}

    # Concurrency limiter
    sem = asyncio.Semaphore(workers)

    async def _guarded_scan(index: int, url: str):
        async with sem:
            return await _scan_one(index, url)

    tasks = [asyncio.create_task(_guarded_scan(i, u)) for i, u in enumerate(urls)]

    start_all = perf_counter()
    results: List[Optional[Dict[str, Any]]] = [None] * len(urls)
    failures = 0

    for coro in asyncio.as_completed(tasks):
        index, outcome = await coro
        if outcome.get("status") == "error":
            failures += 1
        results[index] = outcome

    elapsed = perf_counter() - start_all
    summary = {
        "requested": len(urls),
        "succeeded": len(urls) - failures,
        "failed": failures,
        "duration_ms": round(elapsed * 1000, 2),
        "effective_workers": workers,
        "requests_per_minute": round((len(urls) / elapsed) * 60, 2) if elapsed > 0 else None,
    }

    ordered_results: List[Dict[str, Any]] = [
        r if r is not None else {"status": "error", "error": "scan did not complete"}
        for r in results
    ]
    return {"summary": summary, "results": ordered_results}
