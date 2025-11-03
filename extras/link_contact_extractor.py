#!/usr/bin/env python
"""Utility script to extract links and contact information from a web page.

The script fetches a given URL and prints the following information as JSON:

* Internal links (links that point to the same host as the supplied URL).
* External links (links that point to a different host).
* Phone numbers found on the page, including numbers referenced through
  ``tel:`` links.
* Email addresses found on the page, including addresses referenced through
  ``mailto:`` links.

Usage::

    python extras/link_contact_extractor.py https://example.com

The script only depends on the Python standard library and the ``parsel``
package (which Scrapy already depends on).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any, Iterable

from parsel import Selector


# Regular expressions used to find emails and phone numbers in the page text.
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# The phone pattern accepts international prefixes and common separators.
PHONE_RE = re.compile(
    r"(?:(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3}[\s.-]?\d{4})"
)


def _clean_phone_number(value: str) -> str:
    """Normalize phone numbers by removing formatting characters."""

    cleaned = re.sub(r"[^+\d]", "", value)
    return cleaned


def _decode_response(data: bytes, content_type: str | None) -> str:
    """Decode a HTTP response body using the declared encoding when available."""

    encoding = "utf-8"
    if content_type:
        header = Message()
        header["content-type"] = content_type
        encoding = header.get_content_charset() or encoding
    return data.decode(encoding, errors="replace")


def _canonicalize_link(href: str, base_url: str) -> str | None:
    """Resolve a link against the base URL, returning an absolute HTTP(S) URL."""

    href = href.strip()
    if not href or href.startswith("#"):
        return None

    absolute = urllib.parse.urljoin(base_url, href)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    parsed = parsed._replace(fragment="")
    return parsed.geturl()


def _split_links(links: Iterable[str], base_host: str) -> tuple[set[str], set[str]]:
    """Partition links into internal (same host) and external sets."""

    internal: set[str] = set()
    external: set[str] = set()

    for link in links:
        parsed = urllib.parse.urlparse(link)
        if parsed.netloc.lower() == base_host:
            internal.add(link)
        else:
            external.add(link)
    return internal, external


def fetch_html(
    url: str, user_agent: str | None = None, timeout: float | None = None
) -> tuple[str, str]:
    """Retrieve a web page and return its decoded HTML and Content-Type."""

    headers = {"User-Agent": user_agent or "Scrapy link-contact extractor"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(  # type: ignore[arg-type]
        request, timeout=timeout
    ) as response:
        content_type = response.headers.get("Content-Type")
        body = response.read()
    html = _decode_response(body, content_type)
    return html, content_type or ""


def extract_information(
    html: str, base_url: str
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Extract internal/external links, emails, and phone numbers."""

    selector = Selector(text=html, base_url=base_url)
    raw_hrefs = selector.css("a::attr(href)").getall()

    internal_links: set[str] = set()
    external_links: set[str] = set()
    emails: set[str] = set()
    phones: set[str] = set()

    parsed_base = urllib.parse.urlparse(base_url)
    base_host = parsed_base.netloc.lower()

    # Process anchor links first to capture tel/mailto links and classify URLs.
    normalised_links = []
    for raw_href in raw_hrefs:
        if not raw_href:
            continue
        href = raw_href.strip()
        if href.lower().startswith("mailto:"):
            address = href.split(":", 1)[1].split("?", 1)[0]
            if address:
                emails.add(address)
            continue
        if href.lower().startswith("tel:"):
            number = href.split(":", 1)[1]
            cleaned = _clean_phone_number(number)
            if cleaned:
                phones.add(cleaned)
            continue

        normalised = _canonicalize_link(href, base_url)
        if normalised:
            normalised_links.append(normalised)

    additional_internal, additional_external = _split_links(normalised_links, base_host)
    internal_links.update(additional_internal)
    external_links.update(additional_external)

    text_content = selector.xpath("string()").get() or ""
    for match in EMAIL_RE.findall(text_content):
        emails.add(match)
    for match in PHONE_RE.findall(text_content):
        cleaned = _clean_phone_number(match)
        if len(cleaned) >= 7:
            phones.add(cleaned)

    return internal_links, external_links, emails, phones


def analyse_url(
    url: str, user_agent: str | None = None, timeout: float | None = None
) -> dict[str, Any]:
    """Fetch and analyse a URL, returning the structured result."""

    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in {"http", "https"}:
        raise ValueError("The URL must start with http:// or https://")

    html, _ = fetch_html(url, user_agent=user_agent, timeout=timeout)
    internal, external, emails, phones = extract_information(html, url)

    return {
        "input_url": url,
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


def parse_arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", help="URL of the page to analyse")
    parser.add_argument(
        "--user-agent",
        dest="user_agent",
        help="User-Agent header to use when fetching the URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Timeout (in seconds) for HTTP requests.",
    )
    return parser.parse_args(argv)


def get_user_input(prompt: str = "Enter the URL to analyse: ") -> str:
    """Prompt the user for a URL when no command-line argument is provided."""

    return input(prompt).strip()


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_arguments(argv)
    url = args.url or get_user_input()
    if not url:
        raise SystemExit("A URL must be provided to analyse the page.")

    try:
        result = analyse_url(url, user_agent=args.user_agent, timeout=args.timeout)
    except ValueError as exc:  # Invalid scheme or malformed URL
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
