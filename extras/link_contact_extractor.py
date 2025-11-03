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

"""Utility script to extract links and contact information from a web page."""
from __future__ import annotations
import argparse, json, re, sys, urllib.parse, urllib.request
from email.message import Message
from typing import Any, Dict, Iterable, Set, Tuple
from parsel import Selector

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3}[\s.-]?\d{4})")

def _clean_phone_number(v: str) -> str: return re.sub(r"[^+\d]", "", v)

def _decode_response(data: bytes, content_type: str | None) -> str:
    enc = "utf-8"
    if content_type:
        h = Message(); h["content-type"] = content_type
        enc = h.get_content_charset() or enc
    return data.decode(enc, errors="replace")

def _canonicalize_link(href: str, base_url: str) -> str | None:
    href = href.strip()
    if not href or href.startswith("#"): return None
    absolute = urllib.parse.urljoin(base_url, href)
    p = urllib.parse.urlparse(absolute)
    if p.scheme not in {"http","https"}: return None
    p = p._replace(fragment="")
    return p.geturl()

def _split_links(links: Iterable[str], base_host: str) -> Tuple[Set[str], Set[str]]:
    internal, external = set(), set()
    for link in links:
        p = urllib.parse.urlparse(link)
        (internal if p.netloc.lower()==base_host else external).add(link)
    return internal, external

def fetch_html(url: str, user_agent: str | None = None, timeout: float | None = None) -> Tuple[str, str]:
    headers = {"User-Agent": user_agent or "Scrapy link-contact extractor"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
        ct = resp.headers.get("Content-Type")
        body = resp.read()
    html = _decode_response(body, ct)
    return html, (ct or "")

def extract_information(html: str, base_url: str) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    sel = Selector(text=html, base_url=base_url)
    raw_hrefs = sel.css("a::attr(href)").getall()
    internal, external, emails, phones = set(), set(), set(), set()
    base_host = urllib.parse.urlparse(base_url).netloc.lower()

    norm_links: list[str] = []
    for raw in raw_hrefs:
        if not raw: continue
        href = raw.strip()
        if href.lower().startswith("mailto:"):
            addr = href.split(":",1)[1].split("?",1)[0]
            if addr: emails.add(addr); continue
        if href.lower().startswith("tel:"):
            num = href.split(":",1)[1]
            cl = _clean_phone_number(num)
            if cl: phones.add(cl); continue
        n = _canonicalize_link(href, base_url)
        if n: norm_links.append(n)

    add_int, add_ext = _split_links(norm_links, base_host)
    internal.update(add_int); external.update(add_ext)

    text = sel.xpath("string()").get() or ""
    for m in EMAIL_RE.findall(text): emails.add(m)
    for m in PHONE_RE.findall(text):
        cl = _clean_phone_number(m)
        if len(cl) >= 7: phones.add(cl)

    return internal, external, emails, phones

def analyse_url(url: str, user_agent: str | None = None, timeout: float | None = None) -> Dict[str, Any]:
    p = urllib.parse.urlparse(url)
    if p.scheme not in {"http","https"}: raise ValueError("The URL must start with http:// or https://")
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="URL of the page to analyse")
    ap.add_argument("--user-agent", dest="user_agent", help="User-Agent header to use when fetching the URL.")
    ap.add_argument("--timeout", type=float, default=15.0, help="Timeout (in seconds) for HTTP requests.")
    return ap.parse_args(argv)

def get_user_input(prompt: str = "Enter the URL to analyse: ") -> str: return input(prompt).strip()

def main(argv: Iterable[str] | None = None) -> int:
    args = parse_arguments(argv)
    url = args.url or get_user_input()
    if not url: raise SystemExit("A URL must be provided to analyse the page.")
    try:
        result = analyse_url(url, user_agent=args.user_agent, timeout=args.timeout)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2)); return 0

if __name__ == "__main__":
    sys.exit(main())
