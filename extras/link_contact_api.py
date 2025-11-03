"""Simple HTTP API for scanning multiple domains for links and contacts.

The server exposes a single ``POST /scan`` endpoint that accepts a JSON body
containing a list of URLs to inspect. Results are returned as JSON and reuse
the logic from :mod:`extras.link_contact_extractor`.

Example request::

    curl -X POST http://127.0.0.1:8000/scan \
      -H "Content-Type: application/json" \
      -d '{"urls": ["https://example.com", "https://www.python.org"]}'

Command-line usage::

    python extras/link_contact_api.py --host 0.0.0.0 --port 8000 \
        --max-workers 32 --timeout 15

The server is designed to handle batches of 100+ domains per minute by
executing requests concurrently using a thread pool.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List


if __package__ is None or __package__ == "":
    # Allow importing the extractor when executed as a script.
    sys.path.append(str(Path(__file__).resolve().parent))

from .link_contact_extractor import analyse_url  # type: ignore  # noqa: E402


class ScanHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server that keeps track of concurrency limits."""

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        max_workers: int,
        request_timeout: float,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.max_workers = max_workers
        self.request_timeout = request_timeout


class ScanRequestHandler(BaseHTTPRequestHandler):
    server_version = "LinkContactScan/1.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - required name by BaseHTTPRequestHandler
        if self.path.rstrip("/") == "":
            payload = {
                "status": "ok",
                "message": "Use POST /scan with a JSON body to initiate scans.",
            }
            self._write_json(HTTPStatus.OK, payload)
            return

        if self.path == "/healthz":
            self._write_json(HTTPStatus.OK, {"status": "healthy"})
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/scan":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(
                HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length header"}
            )
            return

        if content_length <= 0:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Request body must contain JSON data."},
            )
            return

        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Invalid JSON payload: {exc.msg}"},
            )
            return

        urls = payload.get("urls")
        if not isinstance(urls, list) or not urls:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Payload must include a non-empty 'urls' list."},
            )
            return

        user_agent = payload.get("user_agent")
        if user_agent is not None and not isinstance(user_agent, str):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "'user_agent' must be a string when provided."},
            )
            return

        timeout = self.server.request_timeout
        if "timeout" in payload:
            timeout_value = payload["timeout"]
            if not isinstance(timeout_value, (int, float)) or timeout_value <= 0:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "'timeout' must be a positive number when provided."},
                )
                return
            timeout = float(timeout_value)

        try:
            workers = self._resolve_worker_count(payload.get("concurrency"), len(urls))
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        start = time.perf_counter()
        results: List[Dict[str, Any] | None] = [None] * len(urls)
        failures = 0

        def _scan(index: int, target_url: str) -> tuple[int, Dict[str, Any]]:
            single_start = time.perf_counter()
            try:
                result = analyse_url(target_url, user_agent=user_agent, timeout=timeout)
                elapsed_ms = round((time.perf_counter() - single_start) * 1000, 2)
                payload: Dict[str, Any] = {"status": "ok", "elapsed_ms": elapsed_ms}
                payload.update(result)
                return index, payload
            except Exception as exc:  # Broad catch to capture network issues.
                return index, {
                    "status": "error",
                    "error": str(exc),
                    "input_url": target_url,
                }

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_scan, idx, url) for idx, url in enumerate(urls)]
            for future in as_completed(futures):
                index, outcome = future.result()
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
            "requests_per_minute": round((len(urls) / elapsed) * 60, 2)
            if elapsed > 0
            else None,
        }

        ordered_results: List[Dict[str, Any]] = [
            entry
            if entry is not None
            else {"status": "error", "error": "scan did not complete"}
            for entry in results
        ]
        self._write_json(
            HTTPStatus.OK,
            {"summary": summary, "results": ordered_results},
        )

    # Helper utilities -------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        """Reduce log noise by prefixing with the client address."""

        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), format % args)
        )

    def _resolve_worker_count(self, requested: Any, batch_size: int) -> int:
        max_workers = max(1, getattr(self.server, "max_workers", 1))
        workers = max_workers
        if requested is not None:
            if not isinstance(requested, int) or requested <= 0:
                raise ValueError("'concurrency' must be a positive integer when provided.")
            workers = min(requested, max_workers)
        workers = min(workers, batch_size) or 1
        return workers

    def _write_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Host/IP to bind the server to")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=32,
        help="Maximum number of concurrent scans per request.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Default timeout (in seconds) for outbound HTTP requests.",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.max_workers <= 0:
        raise SystemExit("--max-workers must be a positive integer")

    server = ScanHTTPServer(
        (args.host, args.port),
        ScanRequestHandler,
        max_workers=args.max_workers,
        request_timeout=args.timeout,
    )
    try:
        print(f"Serving link/contact scans on http://{args.host}:{args.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
