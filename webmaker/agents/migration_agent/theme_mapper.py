"""
webmaker.agents.migration_agent.theme_mapper
=============================================
Step 4 — Theme Mapper.

Converts the Semantic Layout Model into WordPress ``post_content`` HTML that
adopts the selected theme's design language via Gutenberg core blocks
(compatible with Kadence, Astra, Blocksy, GeneratePress, OceanWP).

Deterministic. No AI. No content rewriting — only structural mapping.
"""

from __future__ import annotations

from html import escape

from webmaker.agents.migration_agent.semantic_model import (
    LayoutItem,
    LayoutSection,
    PageLayout,
    SiteLayoutModel,
)
from webmaker.core.logging import get_logger

log = get_logger("migration.theme_mapper")


def map_page_to_html(page: PageLayout, *, theme_id: str = "") -> str:
    """Map one PageLayout to Gutenberg HTML for the chosen theme."""
    theme = (theme_id or "kadence").lower()
    parts: list[str] = [_theme_css(theme)]
    for sec in page.sections:
        parts.append(_map_section(sec, theme=theme))
    html = "\n\n".join(p for p in parts if p and p.strip())
    log.info(
        "Theme-mapped page={p!r} theme={t} sections={n} html_len={l}",
        p=page.slug or page.page, t=theme, n=len(page.sections), l=len(html),
    )
    return html


def map_site_to_page_html(
    model: SiteLayoutModel, *, theme_id: str = ""
) -> dict[str, str]:
    """Return ``{slug: body_html}`` for every page in the model."""
    out: dict[str, str] = {}
    for page in model.pages:
        slug = page.slug or _slugify(page.page)
        out[slug] = map_page_to_html(page, theme_id=theme_id)
    return out


# ── Section mappers ────────────────────────────────────────────────────────────

def _map_section(sec: LayoutSection, *, theme: str) -> str:
    t = sec.type
    if t == "hero":
        return _map_hero(sec, theme=theme)
    if t in ("services_grid", "features_grid", "cards"):
        return _map_grid(sec, theme=theme)
    if t == "labeled_sections":
        return _map_labeled(sec, theme=theme)
    if t in ("two_column", "image_text", "text_image"):
        return _map_two_column(sec, theme=theme)
    if t == "gallery":
        return _map_gallery(sec, theme=theme)
    if t == "faq":
        return _map_faq(sec, theme=theme)
    if t == "cta":
        return _map_cta(sec, theme=theme)
    if t == "contact":
        return _map_contact(sec, theme=theme)
    return _map_rich_text(sec, theme=theme)


def _map_hero(sec: LayoutSection, *, theme: str) -> str:
    brand = ""
    if isinstance(sec.extras, dict):
        brand = str(sec.extras.get("brand") or "")
    inner: list[str] = []
    if brand and brand != sec.heading:
        inner.append(_heading(brand, 1, class_name="wm-hero-brand"))
    if sec.heading:
        level = 2 if brand and brand != sec.heading else 1
        inner.append(_heading(sec.heading, level, class_name="wm-hero-title"))
    if sec.subheading:
        inner.append(_paragraph(sec.subheading, class_name="wm-hero-sub"))
    if sec.text:
        for para in sec.text.split("\n\n"):
            if para.strip():
                inner.append(_paragraph(para.strip()))
    if sec.button:
        inner.append(_button(sec.button, sec.button_url or "#"))
    left = "\n".join(inner)
    if sec.image:
        right = _image(sec.image)
        return _group(
            _columns([(left, "55%"), (right, "45%")], class_name="wm-hero-split"),
            class_name="wm-section wm-hero",
        )
    return _group(left, class_name="wm-section wm-hero")


def _map_grid(sec: LayoutSection, *, theme: str) -> str:
    parts: list[str] = []
    if sec.heading:
        parts.append(_heading(sec.heading, 2))
    items = sec.items or []
    # Long paragraph "cards" must stack vertically — never 3 skinny columns.
    long_copy = any(len(it.text or "") > 90 for it in items)
    if long_copy and sec.type == "features_grid":
        for it in items:
            parts.append(_item_block(it))
        if sec.button:
            parts.append(_button(sec.button, sec.button_url or "#"))
        return _group("\n".join(parts), class_name="wm-section wm-stack")

    cols = max(1, min(sec.columns or 3, 4))
    if sec.type == "services_grid":
        cols = min(3, max(2, len(items))) if items else 3
    rows_html: list[str] = []
    for i in range(0, len(items), cols):
        chunk = items[i : i + cols]
        width = f"{int(100 / max(len(chunk), 1))}%"
        col_parts = []
        for it in chunk:
            col_parts.append((_card_html(it, grid_type=sec.type), width))
        rows_html.append(_columns(col_parts, class_name="wm-grid-row"))
    parts.extend(rows_html)
    if sec.button:
        parts.append(_button(sec.button, sec.button_url or "#"))
    return _group("\n".join(parts), class_name=f"wm-section wm-grid wm-{sec.type}")


def _card_html(it: LayoutItem, *, grid_type: str) -> str:
    bits: list[str] = ['<div class="wm-card">']
    if it.image:
        bits.append(_image(it.image))
    if it.heading:
        bits.append(_heading(it.heading, 3, class_name="wm-card-title"))
    if it.blocks:
        bits.extend(_render_blocks(it.blocks))
    elif it.text:
        for para in it.text.split("\n\n"):
            if para.strip():
                bits.append(_paragraph(para.strip()))
    if it.bullets:
        bits.append(_list(it.bullets, chevron=(grid_type == "services_grid")))
    if it.button:
        bits.append(_button(it.button, it.link or "#"))
    bits.append("</div>")
    return "\n".join(bits)


def _map_labeled(sec: LayoutSection, *, theme: str) -> str:
    """Title-left / content-right rows — preserves service detail layouts."""
    parts: list[str] = []
    if sec.heading:
        parts.append(_heading(sec.heading, 2))
    for row in sec.rows:
        left = _heading(row.heading, 3, class_name="wm-label-title") if row.heading else ""
        right_bits: list[str] = []
        if row.text:
            for para in row.text.split("\n\n"):
                if para.strip():
                    right_bits.append(_paragraph(para.strip()))
        if row.bullets:
            right_bits.append(_list(row.bullets, chevron=True))
        if row.image:
            right_bits.append(_image(row.image))
        right = "\n".join(right_bits)
        parts.append(
            _columns(
                [(left or _paragraph(""), "28%"), (right or _paragraph(""), "72%")],
                class_name="wm-labeled-row",
            )
        )
    return _group("\n".join(parts), class_name="wm-section wm-labeled")


def _map_two_column(sec: LayoutSection, *, theme: str) -> str:
    # Faithful 50/50 with full column content (text + nested headings + image in-column)
    if len(sec.items) >= 2:
        a, b = sec.items[0], sec.items[1]
        return _group(
            _columns(
                [(_item_block(a), "50%"), (_item_block(b), "50%")],
                class_name="wm-two-col",
            ),
            class_name="wm-section wm-two-col-wrap",
        )
    if sec.type == "image_text" and sec.image:
        left = _image(sec.image)
        right = _text_block(sec)
        return _group(
            _columns([(left, "45%"), (right, "55%")], class_name="wm-two-col"),
            class_name="wm-section",
        )
    if sec.type == "text_image" and sec.image:
        left = _text_block(sec)
        right = _image(sec.image)
        return _group(
            _columns([(left, "55%"), (right, "45%")], class_name="wm-two-col"),
            class_name="wm-section",
        )
    return _map_rich_text(sec, theme=theme)


def _map_gallery(sec: LayoutSection, *, theme: str) -> str:
    parts: list[str] = []
    if sec.heading:
        parts.append(_heading(sec.heading, 2))
    imgs = sec.images or ([sec.image] if sec.image else [])
    row: list[tuple[str, str]] = []
    rows: list[str] = []
    for src in imgs:
        row.append((_image(src), "33.33%"))
        if len(row) == 3:
            rows.append(_columns(row, class_name="wm-gallery-row"))
            row = []
    if row:
        rows.append(_columns(row, class_name="wm-gallery-row"))
    parts.extend(rows)
    return _group("\n".join(parts), class_name="wm-section wm-gallery")


def _map_faq(sec: LayoutSection, *, theme: str) -> str:
    parts: list[str] = []
    if sec.heading:
        parts.append(_heading(sec.heading, 2))
    for it in sec.items:
        parts.append(_heading(it.heading, 3, class_name="wm-faq-q"))
        if it.text:
            parts.append(_paragraph(it.text, class_name="wm-faq-a"))
    return _group("\n".join(parts), class_name="wm-section wm-faq")


def _map_cta(sec: LayoutSection, *, theme: str) -> str:
    bits: list[str] = []
    if sec.heading:
        bits.append(_heading(sec.heading, 2))
    if sec.text:
        bits.append(_paragraph(sec.text))
    if sec.button:
        bits.append(_button(sec.button, sec.button_url or "#"))
    return _group("\n".join(bits), class_name="wm-section wm-cta")


def _map_contact(sec: LayoutSection, *, theme: str) -> str:
    bits: list[str] = []
    if sec.heading:
        bits.append(_heading(sec.heading, 2))
    if sec.text:
        for para in sec.text.split("\n\n"):
            if para.strip():
                bits.append(_paragraph(para.strip()))
    if sec.bullets:
        bits.append(_list(sec.bullets))
    if sec.button:
        bits.append(_button(sec.button, sec.button_url or "#contact"))
    return _group("\n".join(bits), class_name="wm-section wm-contact")


def _map_rich_text(sec: LayoutSection, *, theme: str) -> str:
    bits: list[str] = []
    if sec.heading:
        bits.append(_heading(sec.heading, 2))
    if sec.subheading:
        bits.append(_paragraph(sec.subheading, class_name="wm-sub"))
    if sec.blocks:
        bits.extend(_render_blocks(sec.blocks))
    elif sec.text:
        for para in sec.text.split("\n\n"):
            if para.strip():
                bits.append(_paragraph(para.strip()))
    if sec.bullets:
        bits.append(_list(sec.bullets, chevron=True))
    if sec.button:
        bits.append(_button(sec.button, sec.button_url or "#"))
    if sec.image:
        bits.append(_image(sec.image))
    for img in sec.images[:4]:
        if img != sec.image:
            bits.append(_image(img))
    return _group("\n".join(bits), class_name="wm-section wm-rich")


def _text_block(sec: LayoutSection) -> str:
    bits: list[str] = []
    if sec.heading:
        bits.append(_heading(sec.heading, 2))
    if sec.blocks:
        bits.extend(_render_blocks(sec.blocks))
    elif sec.text:
        for para in sec.text.split("\n\n"):
            if para.strip():
                bits.append(_paragraph(para.strip()))
    if sec.bullets:
        bits.append(_list(sec.bullets, chevron=True))
    if sec.button:
        bits.append(_button(sec.button, sec.button_url or "#"))
    return "\n".join(bits)


def _item_block(it: LayoutItem) -> str:
    """Render one column: heading → ordered blocks → bullets → CTA → image."""
    bits: list[str] = ['<div class="wm-col-inner">']
    if it.heading:
        bits.append(_heading(it.heading, 2, class_name="wm-col-title"))
    if it.blocks:
        bits.extend(_render_blocks(it.blocks))
    elif it.text:
        for para in it.text.split("\n\n"):
            if para.strip():
                bits.append(_paragraph(para.strip()))
    if it.bullets:
        bits.append(_list(it.bullets, chevron=True))
    if it.button:
        bits.append(_button(it.button, it.link or "#"))
    # Image after copy (matches original left-column pattern: text then photo)
    if it.image:
        bits.append(_image(it.image))
    bits.append("</div>")
    return "\n".join(bits)


def _render_blocks(blocks: list[dict[str, str]]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        kind = (b.get("kind") or "p").lower()
        text = (b.get("text") or "").strip()
        if not text:
            continue
        if kind in ("h1",):
            out.append(_heading(text, 2, class_name="wm-block-h"))
        elif kind in ("h2", "h3"):
            level = 3 if kind == "h2" else 4
            out.append(_heading(text, level, class_name="wm-block-h"))
        else:
            out.append(_paragraph(text))
    return out


# ── Gutenberg primitives ───────────────────────────────────────────────────────

def _heading(text: str, level: int = 2, class_name: str = "") -> str:
    tag = f"h{level}"
    cls = f' class="{class_name}"' if class_name else ""
    meta = f'{{"level":{level}'
    if class_name:
        meta += f',"className":"{class_name}"'
    meta += "}"
    return (
        f"<!-- wp:heading {meta} -->\n"
        f"<{tag}{cls}>{escape(text)}</{tag}>\n"
        f"<!-- /wp:heading -->"
    )


def _paragraph(text: str, class_name: str = "") -> str:
    cls = f' class="{class_name}"' if class_name else ""
    meta = f' {{"className":"{class_name}"}}' if class_name else ""
    return (
        f"<!-- wp:paragraph{meta} -->\n"
        f"<p{cls}>{escape(text)}</p>\n"
        f"<!-- /wp:paragraph -->"
    )


def _image(src: str, alt: str = "") -> str:
    if not src:
        return ""
    return (
        '<!-- wp:image {"sizeSlug":"large"} -->\n'
        f'<figure class="wp-block-image size-large">'
        f'<img src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}" '
        f'loading="lazy"/></figure>\n'
        "<!-- /wp:image -->"
    )


def _list(items: list[str], *, chevron: bool = False) -> str:
    cls = "wm-chevron-list" if chevron else "wp-block-list"
    lis = "\n".join(f"<li>{escape(i)}</li>" for i in items if i.strip())
    return (
        f'<!-- wp:list {{"className":"{cls}"}} -->\n'
        f'<ul class="{cls}">{lis}</ul>\n'
        "<!-- /wp:list -->"
    )


def _button(label: str, url: str) -> str:
    return (
        '<!-- wp:buttons -->\n'
        '<div class="wp-block-buttons">'
        '<!-- wp:button -->\n'
        f'<div class="wp-block-button">'
        f'<a class="wp-block-button__link wp-element-button" href="{escape(url or "#", quote=True)}">'
        f"{escape(label)}</a></div>\n"
        "<!-- /wp:button --></div>\n"
        "<!-- /wp:buttons -->"
    )


def _group(inner: str, *, class_name: str = "") -> str:
    meta = f' {{"className":"{class_name}"}}' if class_name else ""
    cls = f"wp-block-group {class_name}".strip()
    return (
        f"<!-- wp:group{meta} -->\n"
        f'<div class="{cls}"><div class="wp-block-group__inner-container">\n'
        f"{inner}\n"
        f"</div></div>\n"
        "<!-- /wp:group -->"
    )


def _columns(cols: list[tuple[str, str]], *, class_name: str = "") -> str:
    meta = f' {{"className":"{class_name}"}}' if class_name else ""
    outer = f"wp-block-columns {class_name}".strip()
    chunks: list[str] = []
    for inner, width in cols:
        chunks.append(
            f'<!-- wp:column {{"width":"{width}"}} -->\n'
            f'<div class="wp-block-column" style="flex-basis:{width}">\n'
            f"{inner}\n"
            f"</div>\n"
            "<!-- /wp:column -->"
        )
    return (
        f"<!-- wp:columns{meta} -->\n"
        f'<div class="{outer}">\n'
        + "\n\n".join(chunks)
        + "\n</div>\n<!-- /wp:columns -->"
    )


def _slugify(name: str) -> str:
    s = (name or "page").lower().strip()
    s = "".join(ch if ch.isalnum() else "-" for ch in s)
    return "-".join(p for p in s.split("-") if p) or "page"


def _theme_css(theme: str) -> str:
    """Shared structural CSS so grids/cards/labels look intentional in any theme."""
    accent = {
        "kadence": "#2b6cb0",
        "astra": "#0274be",
        "blocksy": "#3a3a3a",
        "generatepress": "#1e73be",
        "oceanwp": "#13aff0",
    }.get(theme, "#2b6cb0")
    return f"""<!-- wp:html -->
<style>
.wm-section{{margin:0 0 2.25rem}}
.wm-hero{{padding:1rem 0 1.5rem}}
.wm-hero-brand{{margin:0 0 .25rem!important;font-size:clamp(1.5rem,2.5vw,1.9rem)!important;font-weight:700!important}}
.wm-hero-title{{margin:0 0 .75rem!important;font-size:clamp(1.25rem,2vw,1.6rem)!important;font-weight:700!important;line-height:1.3!important}}
.wm-hero-sub{{color:#555;font-style:italic;margin:0 0 1rem!important}}
.wm-hero-split{{align-items:start!important;gap:1.75rem!important}}
.wm-two-col{{align-items:flex-start!important;gap:2rem!important}}
.wm-two-col-wrap{{margin:0 0 2.5rem}}
.wm-col-inner{{width:100%}}
.wm-col-title{{margin:0 0 .85rem!important;font-size:clamp(1.35rem,2vw,1.75rem)!important;font-weight:700!important;line-height:1.25!important}}
.wm-block-h{{margin:1.1rem 0 .4rem!important;font-size:1.1rem!important;font-weight:700!important}}
.wm-grid-row{{gap:1.25rem!important;margin:0 0 1.25rem!important}}
.wm-card{{background:#f3f3f3;padding:1.15rem 1.25rem;height:100%;text-align:left}}
.wm-card-title{{margin:0 0 .55rem!important;font-size:1.1rem!important;font-weight:700!important}}
.wm-card p{{margin:0 0 .65rem!important;line-height:1.55}}
.wm-labeled-row{{align-items:start!important;gap:1.5rem!important;margin:0 0 1.5rem!important;padding:1.25rem 0;border-top:1px solid #ddd}}
.wm-labeled-row:first-of-type{{border-top:0}}
.wm-label-title{{margin:0!important;font-size:1.15rem!important;font-weight:700!important}}
.wm-chevron-list{{list-style:none;padding-left:0;margin:.4rem 0 1rem}}
.wm-chevron-list li{{position:relative;padding-left:1.1rem;margin:0 0 .45rem}}
.wm-chevron-list li::before{{content:">";position:absolute;left:0;color:#666;font-weight:700}}
.wm-cta{{background:#f7f7f7;padding:1.5rem 1.25rem;text-align:center;border-left:4px solid {accent}}}
.wm-faq-q{{margin:1.25rem 0 .35rem!important}}
.wm-faq-a{{margin:0 0 1rem!important;color:#333}}
.wm-gallery-row{{gap:.75rem!important;margin:0 0 .75rem!important}}
.wm-section .wp-block-image{{margin:1rem 0 0!important;max-width:100%}}
.wm-section .wp-block-image img{{width:100%!important;max-width:100%!important;height:auto!important;display:block}}
.wm-col-inner .wp-block-image{{margin-top:1.25rem!important}}
.wm-stack .wm-block-h,.wm-stack h3{{font-weight:700!important}}
@media(max-width:782px){{
  .wm-labeled-row,.wm-hero-split,.wm-grid-row,.wm-two-col{{display:block!important}}
  .wm-labeled-row .wp-block-column,.wm-hero-split .wp-block-column,.wm-two-col .wp-block-column{{flex-basis:100%!important;width:100%!important}}
}}
</style>
<!-- /wp:html -->"""
