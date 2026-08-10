"""
webmaker.agents.migration_agent.layout_composer
================================================
Recreate the original page layout for the WordPress demo.

Strategy
--------
Do **not** rely on theme starter-template blocks or fragile Gutenberg column
nesting.  Emit one self-contained ``<!-- wp:html -->`` fragment with CSS that
mirrors the source site composition (taken from the live-site screenshot):

Homepage (exact composition from screenshot)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. Brand title + tagline
2. Main H1 (Festpreis headline)
3. Two-column grid (~65 / 35):
   - Left: 3 lead paragraphs, then the photo
   - Right: 4 light-grey feature cards (title + short text)
4. Full-width lower sections (headings, paragraphs, bullets)

Other pages get a clean single-column reading layout with the same typography.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal

from webmaker.core.logging import get_logger

log = get_logger("migration.layout_composer")

BlockKind = Literal["h1", "h2", "h3", "p", "img", "li"]


@dataclass
class ContentBlock:
    """One content unit extracted from the source page."""

    kind: BlockKind
    text: str = ""
    src: str = ""
    alt: str = ""


def compose_page_html(blocks: list[ContentBlock], *, slug: str = "homepage") -> str:
    """Compose a self-contained HTML layout matching the original site."""
    if not blocks:
        return ""
    if slug == "homepage":
        inner = _compose_homepage_exact(blocks)
    else:
        inner = _compose_standard_exact(blocks)
    return (
        "<!-- wp:html -->\n"
        f"{_EXACT_CSS}\n"
        f'<div class="wm-exact" data-wm-slug="{escape(slug, quote=True)}">\n'
        f"{inner}\n"
        f"</div>\n"
        "<!-- /wp:html -->"
    )


# ── Homepage — screenshot-faithful ─────────────────────────────────────────────

def _compose_homepage_exact(blocks: list[ContentBlock]) -> str:
    brand, tagline, main_h1, leads, cards, image, rest = _partition_homepage(blocks)

    parts: list[str] = []

    # Header band (matches screenshot top)
    parts.append('<header class="wm-exact__header">')
    if brand:
        parts.append(f'<p class="wm-exact__brand">{escape(brand)}</p>')
    if tagline:
        parts.append(f'<p class="wm-exact__tagline">{escape(tagline)}</p>')
    if main_h1:
        parts.append(f'<h1 class="wm-exact__title">{escape(main_h1)}</h1>')
    parts.append("</header>")

    # Two-column split — left content+image, right feature cards
    parts.append('<div class="wm-exact__split">')

    parts.append('<div class="wm-exact__main">')
    for p in leads:
        parts.append(f"<p>{escape(p)}</p>")
    if image and image.src:
        alt = image.alt or ""
        parts.append(
            f'<figure class="wm-exact__photo">'
            f'<img src="{escape(image.src, quote=True)}" '
            f'alt="{escape(alt, quote=True)}" loading="lazy" />'
            f"</figure>"
        )
    parts.append("</div>")  # main

    parts.append('<aside class="wm-exact__side">')
    for title, body in cards:
        parts.append('<div class="wm-exact__card">')
        parts.append(f"<h3>{escape(title)}</h3>")
        if body:
            parts.append(f"<p>{escape(body)}</p>")
        parts.append("</div>")
    parts.append("</aside>")

    parts.append("</div>")  # split

    # Lower full-width sections
    if rest:
        parts.append('<div class="wm-exact__lower">')
        parts.append(_render_lower(rest))
        parts.append("</div>")

    return "\n".join(parts)


def _partition_homepage(
    blocks: list[ContentBlock],
) -> tuple[
    str,
    str,
    str,
    list[str],
    list[tuple[str, str]],
    ContentBlock | None,
    list[ContentBlock],
]:
    """Partition blocks to match the screenshot regions exactly."""
    brand = ""
    tagline = ""
    main_h1 = ""
    leads: list[str] = []
    cards: list[tuple[str, str]] = []
    image: ContentBlock | None = None

    i = 0
    # Skip leading decorative images (logo / stock before content).
    while i < len(blocks) and blocks[i].kind == "img":
        i += 1

    if i < len(blocks) and blocks[i].kind == "h1":
        brand = blocks[i].text
        i += 1

    if i < len(blocks) and blocks[i].kind in ("h3", "p"):
        t = blocks[i].text.strip()
        if len(t) <= 120:
            tagline = t
            i += 1

    if i < len(blocks) and blocks[i].kind == "h1":
        main_h1 = blocks[i].text
        i += 1
    elif brand and not main_h1:
        main_h1 = brand
        brand = ""

    # Exactly the three lead paragraphs from the screenshot (before the photo).
    while i < len(blocks) and blocks[i].kind == "p" and len(leads) < 3:
        leads.append(blocks[i].text)
        i += 1

    if i < len(blocks) and blocks[i].kind == "img":
        image = blocks[i]
        i += 1

    # Exactly four feature cards (right column in the screenshot).
    while i < len(blocks) and len(cards) < 4:
        b = blocks[i]
        if b.kind not in ("h1", "h2", "h3"):
            break
        title = b.text.strip()
        body = ""
        if i + 1 < len(blocks) and blocks[i + 1].kind == "p":
            candidate = blocks[i + 1].text.strip()
            # Feature cards stay short — long copy belongs below the fold.
            if len(candidate) <= 450 and len(title) <= 90:
                body = candidate
                cards.append((title, body))
                i += 2
                continue
            break
        cards.append((title, ""))
        i += 1

    rest = blocks[i:]
    log.info(
        "Exact homepage partition: brand={b!r} title={t!r} leads={l} "
        "cards={c} image={img} rest={r}",
        b=(brand[:40] if brand else ""),
        t=(main_h1[:40] if main_h1 else ""),
        l=len(leads),
        c=len(cards),
        img=bool(image),
        r=len(rest),
    )
    return brand, tagline, main_h1, leads, cards, image, rest


def _render_lower(blocks: list[ContentBlock]) -> str:
    """Render the full-width lower half with proper lists and spacing."""
    parts: list[str] = []
    list_buf: list[str] = []
    # Also collect short consecutive paragraphs that look like bullet points
    # (original site sometimes uses plain text bullets without <li>).
    bullet_buf: list[str] = []

    def flush_list() -> None:
        nonlocal list_buf
        if list_buf:
            parts.append("<ul>")
            for item in list_buf:
                parts.append(f"<li>{escape(item)}</li>")
            parts.append("</ul>")
            list_buf = []

    def flush_bullets() -> None:
        nonlocal bullet_buf
        if bullet_buf:
            parts.append("<ul>")
            for item in bullet_buf:
                parts.append(f"<li>{escape(item)}</li>")
            parts.append("</ul>")
            bullet_buf = []

    for b in blocks:
        if b.kind == "li":
            flush_bullets()
            list_buf.append(b.text)
            continue

        flush_list()

        if b.kind == "p" and _looks_like_bullet_line(b.text):
            bullet_buf.append(_strip_bullet_prefix(b.text))
            continue

        flush_bullets()

        if b.kind == "h1":
            parts.append(f'<h2 class="wm-exact__h2">{escape(b.text)}</h2>')
        elif b.kind == "h2":
            # All-caps short headings (NACHHALTIGKEIT) get a label style.
            if b.text.isupper() and len(b.text) < 40:
                parts.append(f'<h2 class="wm-exact__label">{escape(b.text)}</h2>')
            elif len(b.text) > 140:
                parts.append(f'<p class="wm-exact__statement">{escape(b.text)}</p>')
            else:
                parts.append(f'<h2 class="wm-exact__h2">{escape(b.text)}</h2>')
        elif b.kind == "h3":
            parts.append(f"<h3>{escape(b.text)}</h3>")
        elif b.kind == "img":
            parts.append(
                f'<figure class="wm-exact__photo">'
                f'<img src="{escape(b.src, quote=True)}" '
                f'alt="{escape(b.alt, quote=True)}" loading="lazy" />'
                f"</figure>"
            )
        elif b.kind == "p":
            # Short location / service link lines → compact list-like rows.
            if _looks_like_link_row(b.text):
                parts.append(f'<p class="wm-exact__linkrow">{escape(b.text)}</p>')
            else:
                parts.append(f"<p>{escape(b.text)}</p>")

    flush_list()
    flush_bullets()
    return "\n".join(parts)


def _looks_like_bullet_line(text: str) -> bool:
    t = text.strip()
    if t.startswith(("•", "–", "-", "·", "*")):
        return True
    # German "Warum uns wählen?" answers are often short benefit lines.
    if 25 <= len(t) <= 160 and t[0].isupper() and t.count(".") <= 1:
        # Heuristic only when many such lines appear — handled by caller buffer.
        return False
    return False


def _strip_bullet_prefix(text: str) -> str:
    return text.lstrip("•–-*· \t")


def _looks_like_link_row(text: str) -> bool:
    t = text.strip()
    if len(t) > 80:
        return False
    low = t.lower()
    return any(
        k in low
        for k in (
            "entrümpelung ", "entruempelung ", "olpe", "netphen",
            "hilchenbach", "kreuztal", "freudenberg", "wilnsdorf",
            "geschäftsauflösung", "haushaltsauflösung", "aktenvernichtung",
            "scanservice", "hausmeister",
        )
    )


# ── Standard pages ─────────────────────────────────────────────────────────────

def _compose_standard_exact(blocks: list[ContentBlock]) -> str:
    parts: list[str] = ['<div class="wm-exact__page">']
    parts.append(_render_lower(blocks))
    parts.append("</div>")
    return "\n".join(parts)


# ── CSS — mirrors original screenshot ──────────────────────────────────────────

_EXACT_CSS = """
<style>
/* Exact-migration layout — mirrors original site screenshot */
.wm-exact{
  --wm-text:#2b2b2b;
  --wm-muted:#5a5a5a;
  --wm-line:#dcdcdc;
  --wm-card:#efefef;
  --wm-max:980px;
  max-width:var(--wm-max);
  margin:0 auto;
  padding:8px 12px 48px;
  color:var(--wm-text);
  font-family:"Segoe UI", Calibri, "Helvetica Neue", Arial, sans-serif;
  font-size:16px;
  line-height:1.55;
  box-sizing:border-box;
}
.wm-exact *,.wm-exact *::before,.wm-exact *::after{box-sizing:border-box}
.wm-exact__header{margin:0 0 1.25rem}
.wm-exact__brand{
  margin:0 0 .15rem;
  font-size:clamp(1.55rem, 2.6vw, 2rem);
  font-weight:700;
  line-height:1.2;
  color:#1c1c1c;
}
.wm-exact__tagline{
  margin:0 0 1.1rem;
  font-size:.98rem;
  color:var(--wm-muted);
  font-weight:400;
}
.wm-exact__title{
  margin:0 0 1.15rem;
  font-size:clamp(1.25rem, 2.1vw, 1.55rem);
  font-weight:700;
  line-height:1.3;
  color:#1a1a1a;
}
.wm-exact__split{
  display:grid;
  grid-template-columns:minmax(0, 1.9fr) minmax(240px, 1fr);
  gap:2rem 2.4rem;
  align-items:start;
  margin:0 0 2rem;
}
.wm-exact__main p{
  margin:0 0 1rem;
  color:var(--wm-text);
  font-size:1rem;
  line-height:1.62;
}
.wm-exact__photo{margin:1.1rem 0 0;padding:0}
.wm-exact__photo img{
  display:block;
  width:100%;
  height:auto;
  border:0;
}
.wm-exact__side{display:flex;flex-direction:column;gap:1rem}
.wm-exact__card{
  background:var(--wm-card);
  padding:1rem 1.05rem 1.05rem;
  border-radius:0;
}
.wm-exact__card h3{
  margin:0 0 .45rem;
  font-size:1.02rem;
  font-weight:700;
  line-height:1.3;
  color:#1a1a1a;
}
.wm-exact__card p{
  margin:0;
  font-size:.95rem;
  line-height:1.5;
  color:#3a3a3a;
}
.wm-exact__lower{
  margin-top:.5rem;
  padding-top:1.6rem;
  border-top:1px solid var(--wm-line);
}
.wm-exact__lower .wm-exact__h2,
.wm-exact__page h2.wm-exact__h2{
  margin:1.65rem 0 .55rem;
  font-size:1.2rem;
  font-weight:700;
  line-height:1.3;
  color:#1a1a1a;
}
.wm-exact__lower .wm-exact__h2:first-child,
.wm-exact__page .wm-exact__h2:first-child{margin-top:0}
.wm-exact__label{
  margin:1.75rem 0 .5rem;
  font-size:1.05rem;
  font-weight:700;
  letter-spacing:.04em;
  text-transform:uppercase;
  color:#1a1a1a;
}
.wm-exact__statement{
  margin:1.25rem 0 1rem;
  font-size:1.15rem;
  font-weight:700;
  line-height:1.35;
  color:#1a1a1a;
}
.wm-exact__lower p,
.wm-exact__page p{
  margin:0 0 1rem;
  line-height:1.62;
  color:var(--wm-text);
}
.wm-exact__lower ul,
.wm-exact__page ul{
  margin:.35rem 0 1.25rem 1.15rem;
  padding:0;
}
.wm-exact__lower li,
.wm-exact__page li{
  margin:0 0 .45rem;
  line-height:1.5;
}
.wm-exact__linkrow{
  margin:0 0 .35rem !important;
  font-weight:600;
  color:#222 !important;
}
.wm-exact__page{max-width:760px}
.wm-exact__page h3{
  margin:1.25rem 0 .4rem;
  font-size:1.05rem;
  font-weight:700;
}
@media (max-width:820px){
  .wm-exact__split{grid-template-columns:1fr;gap:1.5rem}
  .wm-exact{padding:4px 8px 36px}
}
</style>
""".strip()
