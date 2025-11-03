#!/usr/bin/env python
"""Run offline self-tests for the link/contact extractor helpers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from link_contact_extractor import extract_information


FIXTURE_BASE_URL = "https://example.com/self-test"


def evaluate_fixture(path: Path) -> Dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    internal, external, emails, phones = extract_information(html, FIXTURE_BASE_URL)
    return {
        "counts": {
            "internal_links": len(internal),
            "external_links": len(external),
            "email_addresses": len(emails),
            "phone_numbers": len(phones),
        },
        "internal_links": sorted(internal),
        "external_links": sorted(external),
        "email_addresses": sorted(emails),
        "phone_numbers": sorted(phones),
    }


def expected_result() -> Dict[str, Any]:
    return {
        "counts": {
            "internal_links": 2,
            "external_links": 1,
            "email_addresses": 2,
            "phone_numbers": 2,
        },
        "internal_links": [
            "https://example.com/about",
            "https://example.com/contact",
        ],
        "external_links": ["https://docs.scrapy.org/"],
        "email_addresses": [
            "info@example.com",
            "support@example.com",
        ],
        "phone_numbers": ["+35312345678", "12345678"],
    }


def run(path: Path) -> Dict[str, Any]:
    observed = evaluate_fixture(path)
    expected = expected_result()
    success = observed == expected
    return {
        "success": success,
        "expected": expected,
        "observed": observed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixture",
        nargs="?",
        default=Path(__file__).with_name("fixtures").joinpath("sample_site.html"),
        type=Path,
        help="Path to the HTML fixture to evaluate.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output instead of emitting a single line.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run(args.fixture)
    if args.pretty:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))
    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
