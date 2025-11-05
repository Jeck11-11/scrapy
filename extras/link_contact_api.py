"""ASGI application that exposes the link/contact extractor over HTTP.

The app provides a ``POST /scan`` endpoint that accepts a JSON payload with a
``urls`` list and optional ``concurrency``, ``timeout``, and ``user_agent``
fields. Each URL is analysed using :func:`extras.link_contact_extractor.analyse_url`
and the results are returned alongside aggregated statistics.

Run the service with ``uvicorn``::

    uvicorn extras.link_contact_api:app --host 0.0.0.0 --port 8000

The module also exposes a small CLI wrapper that launches Uvicorn directly so
that ``python -m extras.link_contact_api`` remains convenient during
development.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .link_contact_extractor import analyse_url

DEFAULT_MAX_WORKERS = int(os.environ.get("LINK_CONTACT_MAX_WORKERS", "32"))
DEFAULT_TIMEOUT = float(os.environ.get("LINK_CONTACT_REQUEST_TIMEOUT", "15.0"))


def _json_response(
    status: HTTPStatus, payload: Dict[str, Any]
) -> Tuple[int, List[Tuple[bytes, bytes]], bytes]:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return status.value, headers, body


def _resolve_worker_count(requested: Any, batch_size: int, max_workers: int) -> int:
    workers = max(1, max_workers)
    if requested is not None:
        if not isinstance(requested, int) or requested <= 0:
            raise ValueError("'concurrency' must be a positive integer when provided.")
        workers = min(requested, max_workers)
    workers = min(workers, batch_size) or 1
    return workers


async def _receive_body(receive: Any) -> bytes:
    body = b""
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        if chunk:
            body += chunk
        if not message.get("more_body", False):
            break
    return body


def _scan_single(
    index: int,
    target_url: str,
    *,
    user_agent: Optional[str],
    timeout: float,
) -> Tuple[int, Dict[str, Any]]:
    single_start = time.perf_counter()
    try:
        result = analyse_url(target_url, user_agent=user_agent, timeout=timeout)
        elapsed_ms = round((time.perf_counter() - single_start) * 1000, 2)
        payload: Dict[str, Any] = {"status": "ok", "elapsed_ms": elapsed_ms}
        payload.update(result)
        return index, payload
    except Exception as exc:  # pragma: no cover - defensive catch for network errors
        return index, {"status": "error", "error": str(exc), "input_url": target_url}


class ScanAPI:
    """Minimal ASGI application that exposes the scanning endpoint."""

    def __init__(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.max_workers = max_workers
        self.request_timeout = request_timeout

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            raise RuntimeError("ScanAPI only handles HTTP connections")

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")

        if method == "GET" and path.rstrip("/") == "":
            status, headers, body = _json_response(
                HTTPStatus.OK,
                {"status": "ok", "message": "Use POST /scan with a JSON body."},
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body})
            return

        if method == "GET" and path == "/healthz":
            status, headers, body = _json_response(HTTPStatus.OK, {"status": "healthy"})
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body})
            return

        if method != "POST" or path != "/scan":
            status, headers, body = _json_response(
                HTTPStatus.NOT_FOUND, {"error": "Not Found"}
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body})
            return

        body = await _receive_body(receive)
        if not body:
            status, headers, body_bytes = _json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "Request body must contain JSON data."},
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body_bytes})
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            status, headers, body_bytes = _json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Invalid JSON payload: {exc.msg}"},
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body_bytes})
            return

        urls = payload.get("urls")
        if not isinstance(urls, list) or not urls:
            status, headers, body_bytes = _json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "Payload must include a non-empty 'urls' list."},
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body_bytes})
            return

        user_agent = payload.get("user_agent")
        if user_agent is not None and not isinstance(user_agent, str):
            status, headers, body_bytes = _json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "'user_agent' must be a string when provided."},
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body_bytes})
            return

        timeout = self.request_timeout
        if "timeout" in payload:
            timeout_value = payload["timeout"]
            if not isinstance(timeout_value, (int, float)) or timeout_value <= 0:
                status, headers, body_bytes = _json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "'timeout' must be a positive number when provided."},
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": headers,
                    }
                )
                await send({"type": "http.response.body", "body": body_bytes})
                return
            timeout = float(timeout_value)

        try:
            workers = _resolve_worker_count(
                payload.get("concurrency"), len(urls), self.max_workers
            )
        except ValueError as exc:
            status, headers, body_bytes = _json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body_bytes})
            return

        start = time.perf_counter()
        results: List[Optional[Dict[str, Any]]] = [None] * len(urls)
        failures = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                asyncio.wrap_future(
                    executor.submit(
                        _scan_single,
                        idx,
                        url,
                        user_agent=user_agent,
                        timeout=timeout,
                    )
                )
                for idx, url in enumerate(urls)
            ]
            for future in asyncio.as_completed(futures):
                index, outcome = await future
                if outcome.get("status") == "error":
                    failures += 1
                results[index] = outcome

        elapsed = time.perf_counter() - start
        summary = {
            "requested": len(urls),
            "succeeded": len(urls) - failures,
            "failed": failures,
            "duration_ms": round(elapsed * 1000, 2),
            "effective_workers": workers,
            "requests_per_minute": (
                round((len(urls) / elapsed) * 60, 2) if elapsed > 0 else None
            ),
        }

        ordered_results: List[Dict[str, Any]] = [
            (
                entry
                if entry is not None
                else {"status": "error", "error": "scan did not complete"}
            )
            for entry in results
        ]

        status_code, headers, body_bytes = _json_response(
            HTTPStatus.OK,
            {"summary": summary, "results": ordered_results},
        )
        await send(
            {"type": "http.response.start", "status": status_code, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body_bytes})


app = ScanAPI()


def parse_arguments(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host/IP to bind the server to"
    )
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of concurrent scans per request.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Default timeout (in seconds) for outbound HTTP requests.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_arguments(argv)
    if args.max_workers <= 0:
        raise SystemExit("--max-workers must be a positive integer")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit("uvicorn is required to run the API server") from exc

    configured_app = ScanAPI(max_workers=args.max_workers, request_timeout=args.timeout)
    uvicorn.run(configured_app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
