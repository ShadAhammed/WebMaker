"""
webmaker.agents.migration_agent.passthrough_writer
==================================================
Convert raw crawl output into ``optimized_*.json`` files that contain the
verbatim source content of the original website.

No AI is called.  No content is rewritten.

Content source priority
-----------------------
1. **``raw/<stem>.html``** via BeautifulSoup — structured ``h1/h2/h3/p/img``
   extraction (strips nav/header/footer). This is the only source that
   preserves paragraph breaks AND images.
2. **``json/pages/<stem>.json``** metadata + heading-split of ``text_content``
   when raw HTML is missing (text_content is often a single space-joined blob).
3. **``pages/*.txt``** — last-resort plain text.
4. Empty skeleton pages for any standard slug that couldn't be mapped.

Mapping
-------
Each crawled page is mapped to a canonical WordPress slug:
    home/unknown → homepage
    about        → about
    services     → services
    contact      → contact
    faq          → faq

Any remaining standard slug (not found in crawl) gets an empty skeleton page
so ``WordPressGenerator`` always finds all five files.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from webmaker.agents.migration_agent.layout_composer import (
    ContentBlock,
    compose_page_html,
)
from webmaker.core.logging import get_logger
from webmaker.core.schema import write_versioned_json

log = get_logger("migration.passthrough_writer")

# Canonical slug mapping (page_type → WP slug)
_SLUG_MAP: dict[str, str] = {
    "home":     "homepage",
    "about":    "about",
    "services": "services",
    "service":  "services",
    "contact":  "contact",
    "faq":      "faq",
    "blog":     "blog",
    "unknown":  "homepage",
}

_STANDARD_SLUGS: tuple[str, ...] = ("homepage", "about", "services", "contact", "faq")

_DE_TITLES: dict[str, str] = {
    "homepage": "Startseite",
    "about":    "Über uns",
    "services": "Leistungen",
    "contact":  "Kontakt",
    "faq":      "FAQ",
}


# ── Public API ─────────────────────────────────────────────────────────────────

def write_passthrough_pages(data_dir: Path) -> list[str]:
    """Convert crawl output → ``optimized_*.json`` with verbatim content.

    Args:
        data_dir: Project root (directory containing ``json/``).

    Returns:
        Sorted list of slugs written (crawled + any fallback standard pages).
    """
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    # ── Primary: per-page JSON files (rich content) ────────────────────────────
    per_page_dir = json_dir / "pages"
    if per_page_dir.is_dir():
        page_files = sorted(per_page_dir.glob("*.json"))
        if page_files:
            log.info(
                "Using per-page JSON source ({n} files in json/pages/)",
                n=len(page_files),
            )
            return _write_from_per_page_jsons(data_dir, json_dir, page_files)

    # ── Fallback: pages.json summary + pages/*.txt ─────────────────────────────
    pages_json = json_dir / "pages.json"
    txt_dir    = data_dir / "pages"
    if pages_json.is_file() or txt_dir.is_dir():
        log.info("Using pages.json + pages/*.txt source (per-page JSON not found)")
        return _write_from_pages_txt(data_dir, json_dir, pages_json, txt_dir)

    # ── Last resort: empty skeletons ───────────────────────────────────────────
    log.warning("No crawl content found — writing empty skeleton pages")
    return _write_all_fallbacks(json_dir)


# ── Source: json/pages/*.json (primary) ────────────────────────────────────────

def _write_from_per_page_jsons(
    data_dir: Path,
    json_dir: Path,
    page_files: list[Path],
) -> list[str]:
    """Build optimized_*.json from the rich per-page JSON files."""
    page_data_list: list[dict] = []
    for f in page_files:
        try:
            page_data_list.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read {f}: {e}", f=f.name, e=exc)

    # Sort so homepage/home comes first.
    page_data_list.sort(key=lambda d: _page_priority(_effective_type(d)))

    # Local image filename → absolute path (for embedding when CDN fails).
    image_index = _build_image_index(data_dir)

    written: list[str] = []
    seen_slugs: set[str] = set()

    for data in page_data_list:
        slug = _resolve_slug_from_type(_effective_type(data), seen_slugs)
        seen_slugs.add(slug)

        payload = _build_from_rich_data(slug, data, data_dir, image_index)
        out = json_dir / f"optimized_{slug}.json"
        write_versioned_json(out, payload)
        written.append(slug)
        log.info(
            "Pass-through page written → {f}  (title={t!r}, body_len={n})",
            f=out.name, t=payload["title"], n=len(payload["body_html"]),
        )

    # Ensure all five standard pages exist.
    for std_slug in _STANDARD_SLUGS:
        if std_slug not in seen_slugs:
            out = json_dir / f"optimized_{std_slug}.json"
            write_versioned_json(out, _build_empty_page(std_slug))
            written.append(std_slug)
            log.info("Empty fallback page written → {f}", f=out.name)

    return written


def _build_from_rich_data(
    slug: str,
    data: dict,
    data_dir: Path,
    image_index: dict[str, Path],
) -> dict:
    """Build an ``optimized_*.json`` payload from a per-page JSON file.

    Prefers structured HTML from ``raw/<stem>.html`` (paragraphs + images).
    Falls back to heading-split of flat ``text_content``.
    """
    title = (
        _first_str(data.get("meta_title"))
        or _first_str(data.get("title"))
        or _de_title(slug)
    ).strip()
    meta_desc = (_first_str(data.get("meta_description")) or "").strip()

    h1_list = _coerce_to_str_list(data.get("h1", []))
    h2_list = _coerce_to_str_list(data.get("h2", []))[:12]
    primary_h1 = next((h for h in h1_list if h.strip()), title)

    # ── Prefer raw HTML extraction (real paragraphs + images + layout) ────────
    body_html = ""
    extracted_h1 = ""
    raw_path = _resolve_raw_html(data, data_dir)
    if raw_path is not None:
        body_html, extracted_h1 = _extract_html_from_raw(
            raw_path,
            image_index,
            page_images=data.get("images") or [],
            slug=slug,
        )
        if extracted_h1:
            primary_h1 = extracted_h1

    # ── Fallback: split flat text_content by known headings ───────────────────
    if not body_html or len(body_html) < 80:
        text_content = (_first_str(data.get("text_content")) or "").strip()
        flat = _build_body_html_from_flat_text(
            primary_h1, h1_list + h2_list, text_content
        )
        flat += _images_html(data.get("images") or [], image_index)
        # Wrap flat fallback in the same layout composer via simple blocks.
        body_html = compose_page_html(
            _html_string_to_blocks(flat),
            slug=slug,
        ) if flat.strip() else flat

    all_headings = [primary_h1] + [h for h in h2_list if h != primary_h1]

    return {
        "slug":             slug,
        "title":            title,
        "nav_title":        title,
        "body_html":        body_html,
        "meta_title":       (_first_str(data.get("meta_title")) or title)[:60],
        "meta_description": meta_desc[:160],
        "headings":         all_headings[:20],
        "hero": {
            "heading":     primary_h1,
            "subheading":  meta_desc[:120] if meta_desc else "",
            "cta_primary": "",
        },
    }


def _resolve_raw_html(data: dict, data_dir: Path) -> Path | None:
    """Return the absolute path to the raw HTML file, or None."""
    rel = str(data.get("raw_html_path") or "").strip()
    candidates: list[Path] = []
    if rel:
        candidates.append(data_dir / rel)
        candidates.append(Path(rel))
    # Also try stem from page_text_path / url
    text_path = str(data.get("page_text_path") or "")
    if text_path:
        stem = Path(text_path).stem
        candidates.append(data_dir / "raw" / f"{stem}.html")
    url = str(data.get("url") or data.get("final_url") or "")
    if url:
        seg = url.rstrip("/").split("/")[-1] or "home"
        if "." in seg and not seg.endswith((".html", ".htm", ".php")):
            seg = "home"
        if not seg or seg.startswith("http"):
            seg = "home"
        candidates.append(data_dir / "raw" / f"{seg}.html")
        candidates.append(data_dir / "raw" / "home.html")

    for c in candidates:
        if c.is_file():
            return c
    return None


def _extract_html_from_raw(
    raw_path: Path,
    image_index: dict[str, Path],
    *,
    page_images: list,
    slug: str = "homepage",
) -> tuple[str, str]:
    """Extract content from raw HTML and compose a styled WordPress layout.

    Returns ``(body_html, primary_h1)``.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("beautifulsoup4 not installed — cannot extract raw HTML")
        return "", ""

    try:
        html = raw_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("Could not read raw HTML {p}: {e}", p=raw_path, e=exc)
        return "", ""

    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "header", "nav", "footer"]):
        tag.decompose()
    for sel in (
        ".menu", "#menu", ".navbar", ".cookie", ".wnd-popup",
        "[role='navigation']", ".site-header", ".site-footer",
    ):
        for el in soup.select(sel):
            el.decompose()

    blocks: list[ContentBlock] = []
    primary_h1 = ""
    seen_text: set[str] = set()
    img_count = 0

    # Walk top-level content tags; expand <ul> into list items once (avoid
    # double-counting nested <li> from find_all).
    for tag in soup.find_all(["h1", "h2", "h3", "p", "img", "ul"]):
        if tag.name == "ul":
            # Skip nav-ish lists (very short items only).
            items = []
            for li in tag.find_all("li", recursive=False) or tag.find_all("li"):
                t = re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip()
                if t and len(t) >= 8 and t.lower() not in seen_text:
                    seen_text.add(t.lower())
                    items.append(t)
            for t in items:
                blocks.append(ContentBlock(kind="li", text=t[:500]))
            continue

        if tag.name == "img":
            src = (tag.get("src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            try:
                w = int(tag.get("width") or 0)
                h = int(tag.get("height") or 0)
            except ValueError:
                w = h = 0
            if (w and w < 80) or (h and h < 80):
                continue
            # Skip images nested inside already-handled regions if any.
            alt = (tag.get("alt") or "").strip()
            blocks.append(ContentBlock(kind="img", src=src, alt=alt))
            img_count += 1
            continue

        # Skip paragraphs/headings that live inside a list we already handled.
        if tag.find_parent("ul") is not None:
            continue

        text = tag.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 4:
            continue
        key = text.lower()
        if key in seen_text:
            continue
        if _looks_like_chrome(text):
            continue
        seen_text.add(key)

        if tag.name == "h1":
            if not primary_h1:
                primary_h1 = text
            blocks.append(ContentBlock(kind="h1", text=text))
        elif tag.name == "h2":
            blocks.append(ContentBlock(kind="h2", text=text))
        elif tag.name == "h3":
            blocks.append(ContentBlock(kind="h3", text=text))
        else:
            blocks.append(ContentBlock(kind="p", text=text[:1200]))

    body = compose_page_html(blocks, slug=slug)
    log.info(
        "Extracted+composed {p}: {n} blocks, {i} images, body_len={b}, slug={s}",
        p=raw_path.name, n=len(blocks), i=img_count, b=len(body), s=slug,
    )
    return body, primary_h1


def _html_string_to_blocks(html: str) -> list[ContentBlock]:
    """Best-effort parse of a simple HTML string into ContentBlocks."""
    blocks: list[ContentBlock] = []
    for m in re.finditer(
        r"<(h[123]|p|li)[^>]*>(.*?)</\1>|<img[^>]+src=['\"]([^'\"]+)['\"][^>]*>",
        html,
        flags=re.I | re.S,
    ):
        if m.group(3):
            blocks.append(ContentBlock(kind="img", src=m.group(3)))
        else:
            kind = m.group(1).lower()
            text = re.sub(r"<[^>]+>", "", m.group(2) or "").strip()
            if text:
                blocks.append(ContentBlock(kind=kind, text=text))  # type: ignore[arg-type]
    return blocks


def _looks_like_chrome(text: str) -> bool:
    """True for header/nav/footer chrome that should not become page body."""
    t = text.lower().strip()
    if len(t) < 40 and any(
        k in t for k in (
            "menü", "menu", "startseite", "cookie", "datenschutz",
            "impressum", "mehr", "login", "warenkorb",
        )
    ):
        # Short lines that are almost certainly nav.
        words = t.split()
        if len(words) <= 6:
            return True
    # Pure contact strip often prepended by the crawler text blob — skip when short.
    if re.fullmatch(r"[\d\s/+\-()]+", t) and len(t) < 30:
        return True
    return False


def _build_body_html_from_flat_text(
    primary_h1: str,
    headings: list[str],
    text_content: str,
) -> str:
    """Split a space-joined ``text_content`` blob by known headings.

    WebsiteCrawler often stores text with **zero newlines**, so a naive
    paragraph split produces one giant irregular ``<p>``.  Splitting on
    known H1/H2 strings restores readable structure.
    """
    parts: list[str] = []
    if primary_h1:
        parts.append(f"<h1>{escape(primary_h1)}</h1>")

    if not text_content:
        return "\n".join(parts)

    # Build unique heading markers longest-first so longer matches win.
    markers: list[str] = []
    for h in headings:
        h = (h or "").strip()
        if h and len(h) >= 4 and h not in markers:
            markers.append(h)
    markers.sort(key=len, reverse=True)

    # Find heading positions in the blob.
    spans: list[tuple[int, int, str]] = []  # start, end, heading
    lower = text_content
    for h in markers:
        idx = lower.find(h)
        if idx < 0:
            # Try collapsed whitespace variant.
            collapsed_h = re.sub(r"\s+", " ", h)
            idx = re.sub(r"\s+", " ", lower).find(collapsed_h)
            if idx < 0:
                continue
        spans.append((idx, idx + len(h), h))
    spans.sort(key=lambda s: s[0])

    # Deduplicate overlapping spans (keep first).
    cleaned: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, h in spans:
        if start < last_end:
            continue
        cleaned.append((start, end, h))
        last_end = end

    if not cleaned:
        # No headings found — sentence-split the blob, drop chrome prefix.
        cleaned_text = _strip_chrome_prefix(text_content, primary_h1)
        for sentence in _split_sentences(cleaned_text)[:25]:
            parts.append(f"<p>{escape(sentence)}</p>")
        return "\n".join(parts)

    # Content before first heading (often chrome) — keep only if substantial
    # and after the primary h1 appears.
    first_start = cleaned[0][0]
    prefix = text_content[:first_start].strip()
    prefix = _strip_chrome_prefix(prefix, primary_h1)
    for sentence in _split_sentences(prefix)[:8]:
        if len(sentence) >= 40:
            parts.append(f"<p>{escape(sentence)}</p>")

    for i, (start, end, heading) in enumerate(cleaned):
        # Avoid duplicating the already-written primary h1.
        if heading != primary_h1 or i > 0:
            # Long heading-like blobs (footer claims) → paragraph, not h2.
            if len(heading) > 120:
                parts.append(f"<p><strong>{escape(heading[:300])}</strong></p>")
            elif heading == primary_h1:
                pass
            else:
                parts.append(f"<h2>{escape(heading)}</h2>")

        next_start = cleaned[i + 1][0] if i + 1 < len(cleaned) else len(text_content)
        chunk = text_content[end:next_start].strip()
        for sentence in _split_sentences(chunk)[:12]:
            if len(sentence) >= 25 and not _looks_like_chrome(sentence):
                parts.append(f"<p>{escape(sentence[:800])}</p>")

    return "\n".join(parts)


def _strip_chrome_prefix(text: str, primary_h1: str) -> str:
    """Drop leading nav/phone chrome before the main headline when present."""
    if not text:
        return ""
    if primary_h1 and primary_h1 in text:
        # Keep from primary h1 onward (caller may already have written the h1).
        idx = text.find(primary_h1)
        after = text[idx + len(primary_h1):].strip()
        return after
    # Drop common early chrome tokens.
    text = re.sub(
        r"^(.*?)\b(Menü|Menu|Startseite)\b",
        "",
        text,
        count=1,
        flags=re.I,
    ).strip()
    return text


def _split_sentences(text: str) -> list[str]:
    """Split flat German/English text into sentence-ish paragraphs."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []
    # Split on sentence boundaries; keep chunks of useful length.
    chunks = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text)
    out: list[str] = []
    buf = ""
    for c in chunks:
        c = c.strip()
        if not c:
            continue
        if len(buf) + len(c) < 280:
            buf = f"{buf} {c}".strip()
        else:
            if buf:
                out.append(buf)
            buf = c
    if buf:
        out.append(buf)
    return out


def _build_image_index(data_dir: Path) -> dict[str, Path]:
    """Map lowercase filename → local Path for downloaded images."""
    index: dict[str, Path] = {}
    images_dir = data_dir / "images"
    if images_dir.is_dir():
        for f in images_dir.iterdir():
            if f.is_file():
                index[f.name.lower()] = f
                # Also index URL-decoded name.
                try:
                    from urllib.parse import unquote
                    index[unquote(f.name).lower()] = f
                except Exception:
                    pass
    # Also from images.json
    images_json = data_dir / "json" / "images.json"
    if images_json.is_file():
        try:
            raw = json.loads(images_json.read_text(encoding="utf-8"))
            items = raw.get("items", raw) if isinstance(raw, dict) else raw
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    fn = str(it.get("filename") or "")
                    lp = str(it.get("local_path") or "")
                    if fn and lp and Path(lp).is_file():
                        index[fn.lower()] = Path(lp)
        except (OSError, json.JSONDecodeError):
            pass
    return index


def _match_local_image(
    src: str,
    image_index: dict[str, Path],
    page_images: list,
) -> Path | None:
    """Find a local file matching a remote image URL."""
    from urllib.parse import unquote, urlparse

    path = unquote(urlparse(src).path)
    filename = Path(path).name.lower()
    if filename in image_index:
        return image_index[filename]
    # Match against page image metadata.
    for img in page_images:
        if not isinstance(img, dict):
            continue
        fn = str(img.get("filename") or "").lower()
        su = str(img.get("source_url") or "")
        if fn and (fn == filename or src in su or su in src):
            if fn in image_index:
                return image_index[fn]
            lp = str(img.get("local_path") or "")
            if lp and Path(lp).is_file():
                return Path(lp)
    return None


def _images_html(page_images: list, image_index: dict[str, Path]) -> str:
    """Append ``<figure><img>`` tags for page images (CDN URL preferred)."""
    parts: list[str] = []
    for img in page_images:
        if not isinstance(img, dict):
            continue
        src = str(img.get("source_url") or "").strip()
        if not src:
            continue
        alt = str(img.get("alt_text") or img.get("filename") or "").strip()
        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
        if (w and w < 80) or (h and h < 80):
            continue
        parts.append(
            f'\n<figure class="migrated-image">'
            f'<img src="{escape(src, quote=True)}" '
            f'alt="{escape(alt, quote=True)}" '
            f'loading="lazy" style="max-width:100%;height:auto;" />'
            f"</figure>"
        )
    return "".join(parts[:6])


# Keep old name as alias used by text fallback callers — replaced above.
def _build_body_html(h1: str, h2_list: list[str], text_content: str) -> str:
    return _build_body_html_from_flat_text(h1, [h1] + list(h2_list), text_content)


# ── Source: pages.json + pages/*.txt (fallback) ────────────────────────────────

def _write_from_pages_txt(
    data_dir: Path,
    json_dir: Path,
    pages_json: Path,
    txt_dir: Path,
) -> list[str]:
    """Build optimized_*.json using pages.json metadata + .txt file bodies."""
    pages: list[dict] = []

    if pages_json.is_file():
        try:
            raw = json.loads(pages_json.read_text(encoding="utf-8"))
            pages = _normalise_list(raw)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read pages.json: {e}", e=exc)

    if not pages and txt_dir.is_dir():
        # Build synthetic page list from .txt files alone.
        for txt_f in sorted(txt_dir.glob("*.txt")):
            stem = txt_f.stem
            pages.append({
                "url":       f"/{stem}",
                "title":     stem.replace("-", " ").title(),
                "page_type": _stem_to_type(stem),
            })

    if not pages:
        log.warning("No page data found — writing fallbacks")
        return _write_all_fallbacks(json_dir)

    pages.sort(key=lambda d: _page_priority(_effective_type(d)))

    written: list[str] = []
    seen_slugs: set[str] = set()

    for page in pages:
        if not isinstance(page, dict):
            continue
        slug = _resolve_slug_from_type(_effective_type(page), seen_slugs)
        seen_slugs.add(slug)

        # Try to load content from the .txt file.
        text_content = ""
        if txt_dir.is_dir():
            # Derive stem from URL or page_type.
            url_path = str(page.get("url") or "").rstrip("/").split("/")[-1]
            candidates = [url_path, slug, _type_to_stem(slug)]
            for stem in candidates:
                if stem:
                    txt_f = txt_dir / f"{stem}.txt"
                    if txt_f.is_file():
                        try:
                            text_content = txt_f.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            pass
                        break

        title = _first_str(page.get("title")) or _de_title(slug)
        body_html = _build_body_html(title, [], text_content) if text_content else (
            f"<h1>{escape(title)}</h1>"
        )

        payload = {
            "slug":             slug,
            "title":            title,
            "nav_title":        title,
            "body_html":        body_html,
            "meta_title":       title[:60],
            "meta_description": "",
            "headings":         [title],
            "hero": {
                "heading":     title,
                "subheading":  "",
                "cta_primary": "",
            },
        }
        out = json_dir / f"optimized_{slug}.json"
        write_versioned_json(out, payload)
        written.append(slug)
        log.info(
            "Pass-through (txt) page written → {f}  (title={t!r}, body_len={n})",
            f=out.name, t=title, n=len(body_html),
        )

    for std_slug in _STANDARD_SLUGS:
        if std_slug not in seen_slugs:
            out = json_dir / f"optimized_{std_slug}.json"
            write_versioned_json(out, _build_empty_page(std_slug))
            written.append(std_slug)
            log.info("Empty fallback page written → {f}", f=out.name)

    return written


# ── Fallback: empty skeleton pages ─────────────────────────────────────────────

def _write_all_fallbacks(json_dir: Path) -> list[str]:
    """Write minimal skeleton pages for all five standard slugs."""
    json_dir.mkdir(parents=True, exist_ok=True)
    slugs: list[str] = []
    for slug in _STANDARD_SLUGS:
        out = json_dir / f"optimized_{slug}.json"
        write_versioned_json(out, _build_empty_page(slug))
        slugs.append(slug)
    return slugs


def _build_empty_page(slug: str) -> dict:
    title = _de_title(slug)
    return {
        "slug":             slug,
        "title":            title,
        "nav_title":        title,
        "body_html":        f"<h1>{escape(title)}</h1>",
        "meta_title":       title,
        "meta_description": "",
        "headings":         [title],
        "hero": {
            "heading":     title,
            "subheading":  "",
            "cta_primary": "",
        },
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalise_list(raw: object) -> list[dict]:
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if isinstance(raw, dict):
        for key in ("items", "data", "pages"):
            candidate = raw.get(key)
            if isinstance(candidate, list):
                return [p for p in candidate if isinstance(p, dict)]
    return []


_TYPE_PRIORITY = ("home", "about", "services", "service", "contact", "faq")


def _page_priority(page_type: str) -> int:
    t = str(page_type or "unknown").lower()
    try:
        return _TYPE_PRIORITY.index(t)
    except ValueError:
        return len(_TYPE_PRIORITY)


# URL-path-segment → canonical page type.
# Used when the crawler's page_type is "unknown" or incorrect.
_URL_SLUG_TO_TYPE: dict[str, str] = {
    "home":                     "home",
    "index":                    "home",
    "about":                    "about",
    "about-us":                 "about",
    "ueber-uns":                "about",
    "uber-uns":                 "about",
    "wir-uber-uns":             "about",
    "unternehmen":              "about",
    "services":                 "services",
    "leistungen":               "services",
    "angebote":                 "services",
    "contact":                  "contact",
    "kontakt":                  "contact",
    "kontaktieren-sie-uns":     "contact",
    "angebot-anfragen":         "contact",
    "anfrage":                  "contact",
    "faq":                      "faq",
    "haufige-fragen":           "faq",
    "haeufige-fragen":          "faq",
    "haufig-gestellte-fragen":  "faq",
    "gallery":                  "gallery",
    "galerie":                  "gallery",
    "ablauf-bilder":            "gallery",
    "bilder":                   "gallery",
    "fotos":                    "gallery",
    "bewertungen":              "reviews",
    "referenzen":               "reviews",
    "impressum":                "impressum",
    "datenschutz":              "datenschutz",
    "datenschutzerklaerung":    "datenschutz",
    "agb":                      "agb",
}


def _effective_type(data: dict) -> str:
    """Resolve the best page type using page_type field + URL-path heuristic.

    The crawler occasionally misclassifies pages (e.g. labels a contact page
    as "about"). The URL path segment is a more reliable signal when available.
    """
    declared = str(data.get("page_type") or "unknown").lower().strip()
    url      = str(data.get("url") or data.get("final_url") or "")

    # Derive last path segment from URL (e.g. "kontaktieren-sie-uns").
    url_seg = url.rstrip("/").split("/")[-1].lower()
    url_type = _URL_SLUG_TO_TYPE.get(url_seg, "")

    # Prefer URL-based type when the declared type is generic or disagrees
    # with a strongly-matched URL segment.
    if url_type and (declared in ("unknown", "") or _is_overridable(declared, url_type)):
        return url_type
    return declared if declared != "unknown" else "unknown"


def _is_overridable(declared: str, url_type: str) -> bool:
    """Return True if the URL type should override a weakly declared type."""
    # Allow URL to win over "about" / "services" / "gallery" when the URL
    # clearly points to a different function (e.g. contact page).
    weak_types = {"about", "services", "gallery", "blog", "unknown"}
    strong_url = {"contact", "faq", "home", "impressum", "datenschutz"}
    return declared in weak_types and url_type in strong_url


def _resolve_slug_from_type(page_type: str, seen: set[str]) -> str:
    t     = str(page_type or "unknown").lower().strip()
    slug  = _SLUG_MAP.get(t, t.replace("-", "_").replace(" ", "_"))
    if slug in seen:
        n = 2
        while f"{slug}_{n}" in seen:
            n += 1
        slug = f"{slug}_{n}"
    return slug


def _coerce_to_str_list(value: object) -> list[str]:
    """Normalise h1/h2 fields that may be str, list[str], or list[list]."""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, list):
                for sub in item:
                    if isinstance(sub, str) and sub.strip():
                        out.append(sub.strip())
        return out
    return []


def _first_str(value: object) -> str:
    """Return the first non-empty string from a value that may be str or list."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            s = _first_str(item)
            if s:
                return s
    return ""


def _stem_to_type(stem: str) -> str:
    mapping = {
        "home":      "home",
        "index":     "home",
        "about":     "about",
        "ueber-uns": "about",
        "uber-uns":  "about",
        "services":  "services",
        "leistungen":"services",
        "contact":   "contact",
        "kontakt":   "contact",
        "kontaktieren-sie-uns": "contact",
        "faq":       "faq",
    }
    return mapping.get(stem.lower(), "unknown")


def _type_to_stem(slug: str) -> str:
    return {
        "homepage": "home",
        "about":    "about",
        "services": "services",
        "contact":  "contact",
        "faq":      "faq",
    }.get(slug, slug)


def _de_title(slug: str) -> str:
    return _DE_TITLES.get(slug, slug.replace("_", " ").title())
