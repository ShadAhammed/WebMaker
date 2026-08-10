"""
Stage 4 — Content extraction (headings, paras, CTAs, forms, contact fields).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger
from webmaker.schemas.acquisition import PageContentStats

log = get_logger("acquisition.content")

_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s./-]?)?(?:\(?\d{2,5}\)?[\s./-]?)?\d{2,5}[\s./-]?\d{2,6}(?:[\s./-]?\d{2,6})?"
)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_HOURS_RE = re.compile(
    r"(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}[:.]\d{2}\s*[-–—]\s*\d{1,2}[:.]\d{2})",
    re.I,
)


def extract_content(data_dir: Path, package_dir: Path) -> dict[str, Any]:
    """Extract typed content per page into content.json + PageContentStats list."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)
    pages: list[dict[str, Any]] = []
    stats: list[PageContentStats] = []

    per_page_dir = data_dir / "json" / "pages"
    raw_dir = data_dir / "raw"

    page_files = sorted(per_page_dir.glob("*.json")) if per_page_dir.is_dir() else []
    if not page_files and raw_dir.is_dir():
        # Synthetic from raw HTML only
        for raw in sorted(raw_dir.glob("*.html")):
            page_files.append(raw)  # type: ignore[arg-type]

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        BeautifulSoup = None  # type: ignore[misc, assignment]

    for f in page_files:
        slug = f.stem
        meta: dict[str, Any] = {}
        html = ""
        if f.suffix == ".json":
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            raw_rel = str(meta.get("raw_html_path") or "")
            candidates = [
                data_dir / raw_rel if raw_rel else None,
                raw_dir / f"{slug}.html",
            ]
            for c in candidates:
                if c and c.is_file():
                    try:
                        html = c.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        html = ""
                    break
        else:
            try:
                html = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                html = ""

        extracted = _from_html(html, BeautifulSoup) if html and BeautifulSoup else {}
        # Merge meta headings when HTML parse thin
        if not extracted.get("h1"):
            extracted["h1"] = _as_list(meta.get("h1"))
        if not extracted.get("h2"):
            extracted["h2"] = _as_list(meta.get("h2"))

        url = str(meta.get("url") or meta.get("final_url") or "")
        title = str(meta.get("title") or meta.get("meta_title") or slug)
        shot = data_dir / "screenshots" / f"{slug}.png"
        has_shot = shot.is_file() or bool(meta.get("screenshot_path"))

        page_rec = {
            "slug": slug,
            "url": url,
            "title": title,
            "h1": extracted.get("h1") or [],
            "h2": extracted.get("h2") or [],
            "h3": extracted.get("h3") or [],
            "paragraphs": extracted.get("paragraphs") or [],
            "buttons": extracted.get("buttons") or [],
            "lists": extracted.get("lists") or [],
            "tables": extracted.get("tables") or [],
            "forms": extracted.get("forms") or [],
            "faq": extracted.get("faq") or [],
            "phones": extracted.get("phones") or [],
            "emails": extracted.get("emails") or [],
            "addresses": extracted.get("addresses") or [],
            "opening_hours": extracted.get("opening_hours") or [],
            "images": extracted.get("images") or meta.get("images") or [],
            "links_internal": extracted.get("links_internal") or [],
            "links_count": extracted.get("links_count") or 0,
        }
        pages.append(page_rec)

        stats.append(PageContentStats(
            slug=slug,
            url=url,
            title=title,
            h1=len(page_rec["h1"]),
            h2=len(page_rec["h2"]),
            h3=len(page_rec["h3"]),
            paragraphs=len(page_rec["paragraphs"]),
            images=len(page_rec["images"]) if isinstance(page_rec["images"], list) else 0,
            buttons=len(page_rec["buttons"]),
            links=int(page_rec["links_count"] or 0),
            forms=len(page_rec["forms"]),
            lists=len(page_rec["lists"]),
            tables=len(page_rec["tables"]),
            sections=0,  # filled after layout stage
            has_screenshot=has_shot,
            has_raw_html=bool(html),
        ))

    payload = {"pages": pages}
    out = package_dir / "content.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Content extracted for {n} page(s)", n=len(pages))
    return {"pages": pages, "stats": stats}


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, list):
                out.extend(_as_list(v))
        return out
    return []


def _from_html(html: str, BeautifulSoup: Any) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    def texts(sel: str) -> list[str]:
        return [
            re.sub(r"\s+", " ", t.get_text(" ", strip=True)).strip()
            for t in soup.select(sel)
            if t.get_text(strip=True)
        ]

    h1 = texts("h1")
    h2 = texts("h2")
    h3 = texts("h3")
    paragraphs = [
        t for t in texts("p") if len(t) >= 20
    ][:40]
    buttons: list[str] = []
    for el in soup.select("a.btn, a.button, button, .b-btn-t, [role='button']"):
        t = el.get_text(" ", strip=True)
        if t and len(t) < 80:
            buttons.append(t)
    for a in soup.find_all("a", href=True):
        cls = " ".join(a.get("class") or []).lower()
        if any(k in cls for k in ("btn", "button", "cta")):
            t = a.get_text(" ", strip=True)
            if t and t not in buttons and len(t) < 80:
                buttons.append(t)

    lists: list[list[str]] = []
    for ul in soup.find_all(["ul", "ol"])[:20]:
        items = [li.get_text(" ", strip=True) for li in ul.find_all("li", recursive=False)]
        items = [i for i in items if i]
        if items:
            lists.append(items[:30])

    tables = len(soup.find_all("table"))
    forms: list[dict[str, Any]] = []
    for form in soup.find_all("form")[:10]:
        fields = [
            {"name": inp.get("name") or "", "type": inp.get("type") or inp.name}
            for inp in form.find_all(["input", "textarea", "select"])
        ]
        forms.append({"action": form.get("action") or "", "fields": fields[:30]})

    # FAQ-ish: h2/h3 with question mark
    faq = [t for t in h2 + h3 if "?" in t][:20]

    body_text = soup.get_text(" ", strip=True)
    emails = list(dict.fromkeys(_EMAIL_RE.findall(body_text)))[:10]
    phones: list[str] = []
    for m in _PHONE_RE.finditer(body_text):
        p = m.group(0).strip()
        digits = re.sub(r"\D", "", p)
        if 8 <= len(digits) <= 15 and p not in phones:
            phones.append(p)
        if len(phones) >= 8:
            break

    hours: list[str] = []
    for line in re.split(r"[\n.]", body_text):
        if _HOURS_RE.search(line) and 10 < len(line) < 120:
            hours.append(line.strip())
        if len(hours) >= 6:
            break

    addresses: list[str] = []
    # German-ish postal code lines
    for m in re.finditer(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[A-ZÄÖÜ][a-zäöüß\-]+)?", body_text):
        addresses.append(m.group(0))
        if len(addresses) >= 5:
            break

    imgs = [
        {"src": img.get("src") or "", "alt": img.get("alt") or ""}
        for img in soup.find_all("img")
        if img.get("src") and not str(img.get("src")).startswith("data:")
    ][:40]

    links = soup.find_all("a", href=True)
    return {
        "h1": h1[:10],
        "h2": h2[:30],
        "h3": h3[:30],
        "paragraphs": paragraphs,
        "buttons": list(dict.fromkeys(buttons))[:20],
        "lists": lists[:15],
        "tables": tables,
        "forms": forms,
        "faq": faq,
        "phones": phones,
        "emails": emails,
        "addresses": addresses,
        "opening_hours": hours,
        "images": imgs,
        "links_count": len(links),
        "links_internal": [
            a.get("href") for a in links
            if a.get("href", "").startswith("/") or "http" not in a.get("href", "")
        ][:40],
    }
