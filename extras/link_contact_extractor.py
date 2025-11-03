#!/usr/bin/env python
"""Utility script to extract links and contact information from a web page.

The script fetches a given URL and prints the following information as JSON:

* Internal links (links that point to the same registrable domain as the supplied URL).
* External links (links that point to a different registrable domain).
* Phone numbers found on the page, including numbers referenced through ``tel:`` links,
  validated and normalized to E.164 with `phonenumbers`.
* Email addresses found on the page, including addresses referenced through ``mailto:`` links.

Usage::

    python extras/link_contact_extractor.py https://example.com [--render-js]

The script depends on the Python standard library, ``parsel``, ``phonenumbers``, ``tldextract``,
and (optionally for JS) ``playwright``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any, Dict, Iterable, Set, Tuple, List

from parsel import Selector
import phonenumbers
import tldextract

# -------- Email & phone detection ----------
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Coarse candidate finder; we validate with phonenumbers afterwards.
PHONE_PRE_RE = re.compile(
    r"\+?(?:\d[\s().-]?){7,20}(?:\s?(?:ext|x|#)\s?\d{1,6})?",
    re.IGNORECASE,
)


# -------- Helpers ----------
def _clean_phone_number(value: str) -> str:
    return re.sub(r"[^+\dx#]", "", value)


def _decode_response(data: bytes, content_type: str | None) -> str:
    encoding = "utf-8"
    if content_type:
        header = Message()
        header["content-type"] = content_type
        encoding = header.get_content_charset() or encoding
    return data.decode(encoding, errors="replace")


def _canonicalize_link(href: str, base_url: str) -> str | None:
    """Resolve relative links; keep only http(s); strip fragments."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    absolute = urllib.parse.urljoin(base_url, href)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    parsed = parsed._replace(fragment="")
    return parsed.geturl()


def _registrable_domain(url_or_host: str) -> str:
    """Return eTLD+1 (registrable domain), e.g. blog.shop.example.co.uk -> example.co.uk"""
    # Accept full URLs or bare hosts
    netloc = urllib.parse.urlparse(url_or_host).netloc or url_or_host
    ext = tldextract.extract(netloc)
    return ".".join(part for part in [ext.domain, ext.suffix] if part)


def _split_links_by_domain(links: Iterable[str], base_registrable: str) -> Tuple[Set[str], Set[str]]:
    internal: Set[str] = set()
    external: Set[str] = set()
    for link in links:
        reg = _registrable_domain(link)
        (internal if reg == base_registrable else external).add(link)
    return internal, external


def _parse_and_format_phone(raw: str, default_region: str | None = None) -> str | None:
    """Validate and format to E.164 using phonenumbers."""
    try:
        num = phonenumbers.parse(raw, default_region) if default_region else phonenumbers.parse(raw)
        if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass
    return None


# -------- Fetchers ----------
def fetch_html(url: str, user_agent: str | None = None, timeout: float | None = 15.0) -> Tuple[str, str]:
    headers = {"User-Agent": user_agent or "Scrapy link-contact extractor"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
        ct = resp.headers.get("Content-Type")
        body = resp.read()
    html = _decode_response(body, ct)
    return html, (ct or "")


def fetch_html_js(url: str, user_agent: str | None = None, timeout: float | None = 15.0) -> Tuple[str, str]:
    """
    Render the page with a headless browser and return (html, content_type).
    Requires: pip install playwright && playwright install --with-deps chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Playwright is not installed. Install it or run without --render-js") from e

    launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
    ua = user_agent or "Scrapy link-contact extractor (Playwright)"
    ms = int((timeout or 15.0) * 1000)

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(user_agent=ua)
        page = context.new_page()
        page.set_default_navigation_timeout(ms)
        page.goto(url, wait_until="networkidle")
        html = page.content()
        ctype = page.evaluate("() => document.contentType || 'text/html'")
        browser.close()
    return html, ctype or "text/html"


# -------- Core extraction ----------
def extract_information(html: str, base_url: str) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    sel = Selector(text=html, base_url=base_url)
    raw_hrefs: List[str] = sel.css("a::attr(href)").getall()

    internal: Set[str] = set()
    external: Set[str] = set()
    emails: Set[str] = set()
    phones: Set[str] = set()

    base_reg = _registrable_domain(base_url)

    # 1) Anchor processing (href/mailto/tel)
    normalized_links: List[str] = []
    for raw in raw_hrefs:
        if not raw:
            continue
        href = raw.strip()

        # mailto:
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?", 1)[0]
            if addr:
                emails.add(addr)
            continue

        # tel:
        if href.lower().startswith("tel:"):
            number = href.split(":", 1)[1]
            cleaned = _clean_phone_number(number)
            parsed = _parse_and_format_phone(cleaned)
            if parsed:
                phones.add(parsed)
            continue

        # regular links
        n = _canonicalize_link(href, base_url)
        if n:
            normalized_links.append(n)

    add_int, add_ext = _split_links_by_domain(normalized_links, base_reg)
    internal.update(add_int)
    external.update(add_ext)

    # 2) Page text scanning (emails & phones)
    text = sel.xpath("string()").get() or ""

    for m in EMAIL_RE.findall(text):
        emails.add(m)

    # Phone: coarse candidates -> validate/normalize
    for cand in PHONE_PRE_RE.findall(text):
        cleaned = _clean_phone_number(cand)
        parsed = _parse_and_format_phone(cleaned)
        if parsed:
            phones.add(parsed)

    return internal, external, emails, phones


# -------- Public API ----------
def analyse_url(
    url: str,
    user_agent: str | None = None,
    timeout: float | None = 15.0,
    render_js: bool = False,
) -> Dict[str, Any]:
    p = urllib.parse.urlparse(url)
    if p.scheme not in {"http", "https"}:
        raise ValueError("The URL must start with http:// or https://")

    if render_js:
        html, _ = fetch_html_js(url, user_agent=user_agent, timeout=timeout)
    else:
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


# -------- CLI ----------
def parse_arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="URL of the page to analyse")
    ap.add_argument("--user-agent", dest="user_agent", help="User-Agent header to use when fetching the URL.")
    ap.add_argument("--timeout", type=float, default=15.0, help="Timeout (in seconds) for HTTP requests.")
    ap.add_argument("--render-js", action="store_true", help="Render with Playwright (headless Chromium).")
    return ap.parse_args(argv)


def get_user_input(prompt: str = "Enter the URL to analyse: ") -> str:
    return input(prompt).strip()


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_arguments(argv)
    url = args.url or get_user_input()
    if not url:
        raise SystemExit("A URL must be provided to analyse the page.")
    try:
        result = analyse_url(
            url,
            user_agent=args.user_agent,
            timeout=args.timeout,
            render_js=getattr(args, "render_js", False),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
