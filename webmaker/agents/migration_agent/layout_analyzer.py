"""
webmaker.agents.migration_agent.layout_analyzer
================================================
Step 2 — Layout Analyzer (CMS-agnostic).

Infers logical sections from DOM hierarchy, repetition, and content
patterns. Never keys off WebNode / Wix / Elementor class names.
"""

from __future__ import annotations

import re
from collections import Counter

from webmaker.agents.migration_agent.dom_extractor import DomDocument, DomNode
from webmaker.agents.migration_agent.semantic_model import (
    LayoutItem,
    LayoutSection,
    PageLayout,
)
from webmaker.core.logging import get_logger

log = get_logger("migration.layout_analyzer")

_SERVICE_WORDS = re.compile(
    r"service|leistung|entrümpel|auflös|reinig|garten|akten|scan|"
    r"angebot|pricing|preis|paket|team|über uns|about|kontakt|contact|"
    r"faq|frage|testimonial|bewertung|galerie|gallery|prozess|ablauf",
    re.I,
)

_COOKIE_RE = re.compile(r"cookie|datenschutzeinstellung|consent", re.I)


def analyze_page(doc: DomDocument, *, slug: str = "", page_name: str = "") -> PageLayout:
    """Build a PageLayout from an extracted DomDocument."""
    root = doc.root
    if root is None:
        return PageLayout(page=page_name or slug or "Page", slug=slug, title=doc.title, url=doc.url)

    main = _find_main(root) or root
    candidates = _section_candidates(main)
    sections: list[LayoutSection] = []

    for node in candidates:
        if _is_noise(node):
            continue
        for sec in _classify_or_split(node):
            if not (sec.heading or sec.text or sec.items or sec.rows or sec.images or sec.bullets):
                continue
            sections.append(sec)

    sections = _merge_adjacent(sections)
    sections = _drop_empty_grid_items(sections)
    sections = _ensure_services_title_grid(sections)
    sections = _normalize_heroes(sections, doc)

    layout = PageLayout(
        page=page_name or slug or doc.title or "Page",
        slug=slug,
        title=doc.title,
        url=doc.url,
        sections=sections,
    )
    log.info(
        "Layout analyzed slug={s}: {n} sections → {types}",
        s=slug,
        n=len(sections),
        types=[sec.type for sec in sections],
    )
    return layout


# ── Candidate discovery ────────────────────────────────────────────────────────

def _find_main(root: DomNode) -> DomNode | None:
    for n in root.depth_first():
        if n.role == "main":
            return n
    blocks = [
        c for c in root.block_children()
        if c.role not in ("nav", "header", "footer") and not _is_noise(c)
    ]
    if not blocks:
        return root
    return max(blocks, key=lambda n: len(n.text))


def _section_candidates(main: DomNode) -> list[DomNode]:
    """Unwrap CMS wrapper chains until sibling page sections appear."""
    node = _unwrap_singles(main, max_depth=10)
    kids = [c for c in node.block_children() if _meaningful(c) and not _is_noise(c)]
    if len(kids) >= 2:
        return kids
    # Still one blob — try one more expansion of wrapper-like children.
    expanded: list[DomNode] = []
    for k in kids or [node]:
        sub = [c for c in _unwrap_singles(k).block_children() if _meaningful(c) and not _is_noise(c)]
        if len(sub) >= 2:
            expanded.extend(sub)
        else:
            expanded.append(k)
    return expanded or [main]


def _unwrap_singles(node: DomNode, max_depth: int = 8) -> DomNode:
    """Follow single-child wrapper chains (common in page builders)."""
    cur = node
    for _ in range(max_depth):
        kids = [c for c in cur.block_children() if _meaningful(c) and not _is_noise(c)]
        if len(kids) != 1:
            return cur
        cur = kids[0]
    return cur


def _deep_peers(node: DomNode, max_depth: int = 8) -> list[DomNode]:
    """Unwrap singles, then return meaningful peer children."""
    cur = _unwrap_singles(node, max_depth=max_depth)
    peers = [c for c in cur.block_children() if _meaningful(c) and not _is_noise(c)]
    if len(peers) == 1:
        inner = _deep_peers(peers[0], max_depth=max(0, max_depth - 1))
        if len(inner) >= 2:
            return inner
    return peers


def _looks_like_wrapper(node: DomNode) -> bool:
    if node.own_text and len(node.own_text) > 40:
        return False
    subs = [c for c in node.block_children() if _meaningful(c)]
    return len(subs) >= 2


def _meaningful(node: DomNode) -> bool:
    if node.role in ("nav",):
        return False
    if node.img_src or any(c.img_src for c in node.children[:3]):
        return True
    if len(node.text) >= 8:
        return True
    if node.tag in ("h1", "h2", "h3", "form", "ul", "ol", "section", "article"):
        return True
    return bool(node.block_children())


def _is_noise(node: DomNode) -> bool:
    """Cookie banners / consent modals — not page content."""
    if node.role == "footer" and len(node.text) < 200:
        return True
    sample = (node.text or "")[:220]
    if _COOKIE_RE.search(sample) and (
        "akzeptieren" in sample.lower()
        or "accept" in sample.lower()
        or "einstellungen" in sample.lower()
        or "consent" in sample.lower()
    ):
        return True
    return False


# ── Classification ─────────────────────────────────────────────────────────────

def _classify_or_split(node: DomNode) -> list[LayoutSection]:
    """Classify one candidate; may split into multiple semantic sections."""
    peers = _deep_peers(node)

    # Partition long peer lists into grids / labeled rows / other blocks.
    if len(peers) >= 3:
        parts = _partition_peers(node, peers)
        if parts:
            return parts

    if len(peers) == 2:
        sec = _as_two_column(node, peers)
        # Two-column right side may itself be a feature card grid.
        return _expand_two_column(sec, peers)

    if len(peers) == 1:
        return _classify_or_split(peers[0])

    single = _classify_node(node)
    return [single] if single else []


def _partition_peers(parent: DomNode, peers: list[DomNode]) -> list[LayoutSection]:
    """Walk peer blocks and emit grids, labeled rows, and leftover sections."""
    out: list[LayoutSection] = []
    i = 0
    labeled_buf: list[LayoutItem] = []
    grid_buf: list[DomNode] = []

    def flush_labeled() -> None:
        nonlocal labeled_buf
        if labeled_buf:
            out.append(LayoutSection(type="labeled_sections", rows=list(labeled_buf)))
            labeled_buf = []

    def flush_grid() -> None:
        nonlocal grid_buf
        if len(grid_buf) >= 2:
            out.append(_as_grid(parent, grid_buf))
        elif len(grid_buf) == 1:
            for sec in _classify_or_split(grid_buf[0]):
                out.append(sec)
        grid_buf = []

    while i < len(peers):
        p = peers[i]
        inner = _deep_peers(p)

        # Nested similar heading-only columns → services title chips only
        if (
            len(inner) >= 2
            and not _looks_like_brand_stack(inner)
            and _are_similar(inner)
            and _mostly_heading_cards(inner)
            and all(len(_paragraph_text(n, 1)) < 40 for n in inner)
        ):
            flush_labeled()
            flush_grid()
            out.append(_as_grid(p, inner))
            i += 1
            continue

        # Nested 2-col labeled row
        if len(inner) == 2 and (
            _is_label_column(inner[0]) or _is_label_column(inner[1])
        ):
            flush_grid()
            row = _labeled_row_from_cols(inner)
            if row:
                labeled_buf.append(row)
            i += 1
            continue

        # Peer itself is a short heading card (part of a title grid)
        if _is_heading_card(p):
            flush_labeled()
            grid_buf.append(p)
            i += 1
            continue

        # Mixed: if we already have a grid buffer and this breaks the pattern
        flush_grid()
        flush_labeled()

        # Keep real 50/50 columns intact — never explode stacked h2/p into grids
        if len(inner) == 2:
            out.extend(_expand_two_column(_as_two_column(p, inner), inner))
        else:
            sec = _node_to_section(p) or _classify_node(p)
            if sec:
                out.append(sec)
        i += 1

    flush_grid()
    flush_labeled()
    return out


def _expand_two_column(sec: LayoutSection, cols: list[DomNode]) -> list[LayoutSection]:
    """Keep 50/50 content columns intact.

    Only split when one side is a peer grid of *short* title chips.
    Never explode stacked h2/p article subsections into a horizontal grid.
    """
    if len(cols) != 2:
        return [sec]

    if sec.type == "two_column" and len(sec.items) >= 2:
        return [sec]

    left, right = cols[0], cols[1]
    for side, other in ((right, left), (left, right)):
        inner = _deep_peers(side)
        if (
            len(inner) >= 3
            and not _looks_like_brand_stack(inner)
            and _mostly_heading_cards(inner)
            and all(len(_paragraph_text(n, 1)) < 40 for n in inner)
        ):
            other_item = _node_to_item(other)
            other_sec = LayoutSection(
                type="rich_text",
                heading=other_item.heading,
                text=other_item.text,
                image=other_item.image,
                images=[other_item.image] if other_item.image else [],
                bullets=other_item.bullets,
                blocks=other_item.blocks,
                button=other_item.button,
                button_url=other_item.link,
            )
            return [other_sec, _as_grid(side, inner)]

    return [sec]


def _looks_like_brand_stack(nodes: list[DomNode]) -> bool:
    """True for short stacked brand/tagline headings (not service cards)."""
    if not nodes or len(nodes) > 4:
        return False
    total = sum(len(n.text) for n in nodes)
    if total > 220:
        return False
    return all(_is_heading_card(n) or len(n.text) < 50 for n in nodes)


def _node_to_item(node: DomNode) -> LayoutItem:
    """Serialize one column into a LayoutItem, preserving heading order."""
    blocks = _extract_blocks(node)
    heading = ""
    rest: list[dict[str, str]] = []
    for b in blocks:
        if not heading and b.get("kind") in ("h1", "h2", "h3"):
            heading = b.get("text") or ""
            continue
        rest.append(b)
    paras = [b["text"] for b in rest if b.get("kind") == "p" and b.get("text")]
    imgs = _collect_images(node)
    return LayoutItem(
        heading=heading,
        text="\n\n".join(paras),
        blocks=rest,
        image=imgs[0] if imgs else "",
        bullets=_list_bullets(node),
        button=_first_button_text(node),
        link=_first_link(node),
    )


def _node_to_section(node: DomNode) -> LayoutSection | None:
    item = _node_to_item(node)
    if not (item.heading or item.text or item.blocks or item.image or item.bullets):
        return None
    return LayoutSection(
        type="rich_text",
        heading=item.heading,
        text=item.text,
        image=item.image,
        images=[item.image] if item.image else [],
        bullets=item.bullets,
        blocks=item.blocks,
        button=item.button,
        button_url=item.link,
    )


def _extract_blocks(node: DomNode) -> list[dict[str, str]]:
    """Ordered heading/paragraph blocks inside a column (document order)."""
    tags = [n for n in node.depth_first() if n.tag in ("h1", "h2", "h3", "p")]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for n in tags:
        t = (n.text or "").strip()
        if not t or len(t) < 2:
            continue
        key = f"{n.tag}:{t}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": n.tag, "text": t})
    return out


def _detect_subheading_cards(node: DomNode) -> LayoutSection | None:
    """Disabled — stacked h2/p must stay vertical inside their column."""
    return None


def _classify_node(node: DomNode) -> LayoutSection | None:
    if node.role in ("nav", "header") and len(node.text) < 200:
        return None

    cols = _deep_peers(node)

    if len(cols) >= 2 and _are_similar(cols):
        return _as_grid(node, cols)

    if len(cols) == 2:
        return _as_two_column(node, cols)

    if len(cols) >= 3:
        return _as_grid(node, cols)

    faq = _detect_faq(node)
    if faq:
        return faq

    imgs = _collect_images(node)
    if len(imgs) >= 3 and len(node.text) < 400:
        return LayoutSection(type="gallery", images=imgs, heading=_first_heading(node))

    cta = _detect_cta(node)
    if cta:
        return cta

    labeled = _detect_labeled_rows(node)
    if labeled:
        return labeled

    if _looks_like_hero(node):
        return _as_hero(node)

    if any(c.role == "form" or c.tag == "form" for c in node.depth_first()):
        return LayoutSection(
            type="contact",
            heading=_first_heading(node),
            text=_paragraph_text(node, limit=2),
        )

    heading = _first_heading(node)
    text = _paragraph_text(node, limit=6)
    bullets = _list_bullets(node)
    images = imgs[:6]
    if not heading and not text and not bullets and not images:
        return None

    sec_type = "rich_text"
    if heading and re.search(r"faq|frage|häufig", heading, re.I):
        sec_type = "faq"
    elif heading and re.search(r"kontakt|contact", heading, re.I):
        sec_type = "contact"
    elif heading and re.search(r"team|über uns|about", heading, re.I):
        sec_type = "about"
    elif heading and _SERVICE_WORDS.search(heading) and len(text) > 200:
        sec_type = "about"

    return LayoutSection(
        type=sec_type,  # type: ignore[arg-type]
        heading=heading,
        text=text,
        bullets=bullets,
        images=images,
        image=images[0] if images else "",
    )


def _as_grid(node: DomNode, cols: list[DomNode]) -> LayoutSection:
    items: list[LayoutItem] = []
    for c in cols:
        # Prefer innermost card content when column is a thin wrapper.
        peers = _deep_peers(c)
        target = c
        if len(peers) == 1 and len(peers[0].text) >= 8:
            target = peers[0]
        items.append(LayoutItem(
            heading=_first_heading(target),
            text=_paragraph_text(target, limit=3),
            image=(_collect_images(target) or [""])[0],
            bullets=_list_bullets(target),
            link=_first_link(target),
            button=_first_button_text(target),
        ))

    bodies = sum(1 for it in items if it.text or it.bullets)
    headings_only = bodies == 0 and sum(1 for it in items if it.heading) >= max(2, len(items) - 1)
    n = len(items)
    columns = 3 if n % 3 == 0 or n >= 5 else (2 if n % 2 == 0 else min(n, 3))

    if headings_only:
        stype: str = "services_grid"
    elif all(it.image for it in items) and bodies <= n // 2:
        stype = "gallery" if not any(it.heading for it in items) else "cards"
    else:
        stype = "features_grid" if n >= 3 else "cards"

    return LayoutSection(
        type=stype,  # type: ignore[arg-type]
        heading=_section_heading_outside(node, cols),
        columns=columns,
        items=items,
    )


def _as_two_column(node: DomNode, cols: list[DomNode]) -> LayoutSection:
    left, right = cols[0], cols[1]
    left_item = _node_to_item(left)
    right_item = _node_to_item(right)
    left_imgs = _collect_images(left)
    right_imgs = _collect_images(right)

    # Label | content  (short left heading-only, rich right)
    if _is_label_column(left) and not _is_label_column(right):
        return LayoutSection(
            type="labeled_sections",
            rows=[LayoutItem(
                heading=left_item.heading or left.text[:80],
                text=right_item.text,
                bullets=right_item.bullets,
                blocks=right_item.blocks,
                image=right_item.image,
            )],
        )
    if _is_label_column(right) and not _is_label_column(left):
        return LayoutSection(
            type="labeled_sections",
            rows=[LayoutItem(
                heading=right_item.heading or right.text[:80],
                text=left_item.text,
                bullets=left_item.bullets,
                blocks=left_item.blocks,
                image=left_item.image,
            )],
        )

    # Both sides have real text content → preserve as 50/50 (image stays IN its column)
    left_has_text = bool(left_item.heading or left_item.text or left_item.blocks)
    right_has_text = bool(right_item.heading or right_item.text or right_item.blocks)
    if left_has_text and right_has_text:
        return LayoutSection(
            type="two_column",
            layout="50_50",
            columns=2,
            items=[left_item, right_item],
        )

    # Pure image column + text column
    if left_imgs and not left_has_text and right_has_text:
        return LayoutSection(
            type="image_text",
            layout="image_left_text_right",
            heading=right_item.heading,
            text=right_item.text,
            image=left_imgs[0],
            bullets=right_item.bullets,
            blocks=right_item.blocks,
            button=right_item.button,
            button_url=right_item.link,
        )
    if right_imgs and not right_has_text and left_has_text:
        return LayoutSection(
            type="text_image",
            layout="text_left_image_right",
            heading=left_item.heading,
            text=left_item.text,
            image=right_imgs[0],
            bullets=left_item.bullets,
            blocks=left_item.blocks,
            button=left_item.button,
            button_url=left_item.link,
        )

    return LayoutSection(
        type="two_column",
        layout="50_50",
        columns=2,
        items=[left_item, right_item],
    )


def _labeled_row_from_cols(cols: list[DomNode]) -> LayoutItem | None:
    if len(cols) != 2:
        return None
    left, right = cols[0], cols[1]
    if _is_label_column(left):
        return LayoutItem(
            heading=_first_heading(left) or left.text[:80],
            text=_paragraph_text(right, limit=4),
            bullets=_list_bullets(right),
            image=(_collect_images(right) or [""])[0],
        )
    if _is_label_column(right):
        return LayoutItem(
            heading=_first_heading(right) or right.text[:80],
            text=_paragraph_text(left, limit=4),
            bullets=_list_bullets(left),
            image=(_collect_images(left) or [""])[0],
        )
    return None


def _is_label_column(node: DomNode) -> bool:
    h = _first_heading(node)
    if not h:
        # Short bold-only text without list can still be a label.
        t = (node.text or "").strip()
        return 3 <= len(t) <= 80 and not _list_bullets(node) and len(_collect_images(node)) == 0
    rest = node.text[len(h):].strip() if node.text.startswith(h) else node.text.replace(h, "", 1).strip()
    return len(h) <= 80 and len(rest) < 40 and not _list_bullets(node)


def _is_heading_card(node: DomNode) -> bool:
    """Short cell that is mostly a title (service name chip)."""
    h = _first_heading(node)
    t = (node.text or "").strip()
    if h and len(t) <= max(len(h) + 10, 60) and not _list_bullets(node):
        return True
    return 3 <= len(t) <= 60 and not _list_bullets(node) and not _collect_images(node)


def _mostly_heading_cards(nodes: list[DomNode]) -> bool:
    if not nodes:
        return False
    return sum(1 for n in nodes if _is_heading_card(n)) >= max(2, len(nodes) - 1)


def _detect_labeled_rows(node: DomNode) -> LayoutSection | None:
    kids = _deep_peers(node)
    rows: list[LayoutItem] = []
    for k in kids:
        cols = _deep_peers(k)
        row = _labeled_row_from_cols(cols) if len(cols) == 2 else None
        if row:
            rows.append(row)
    if len(rows) >= 2:
        return LayoutSection(type="labeled_sections", rows=rows)
    return None


def _detect_faq(node: DomNode) -> LayoutSection | None:
    headings = [(n.tag, n.text) for n in node.depth_first() if n.role == "heading" and n.text]
    if len(headings) < 3:
        return None
    items: list[LayoutItem] = []
    blocks = node.block_children()
    for i, b in enumerate(blocks):
        if b.role == "heading" and b.tag in ("h2", "h3", "h4"):
            answer = ""
            if i + 1 < len(blocks) and blocks[i + 1].role != "heading":
                answer = _paragraph_text(blocks[i + 1], limit=2)
            if answer or "?" in b.text:
                items.append(LayoutItem(heading=b.text, text=answer))
    if len(items) >= 3 and (
        any("?" in it.heading for it in items)
        or re.search(r"faq|frage", _first_heading(node) or "", re.I)
    ):
        return LayoutSection(type="faq", heading=_first_heading(node), items=items)
    return None


def _detect_cta(node: DomNode) -> LayoutSection | None:
    btn = _first_button_text(node)
    if not btn:
        return None
    if len(node.text) > 320:
        return None
    return LayoutSection(
        type="cta",
        heading=_first_heading(node),
        text=_paragraph_text(node, limit=2),
        button=btn,
        button_url=_first_link(node),
    )


def _looks_like_hero(node: DomNode) -> bool:
    h1 = next((n for n in node.depth_first() if n.tag == "h1"), None)
    if not h1:
        return False
    return len(node.text) < 1200


def _as_hero(node: DomNode) -> LayoutSection:
    headings = [n.text for n in node.depth_first() if n.tag in ("h1", "h2", "h3") and n.text]
    brand = headings[0] if headings else ""
    main = headings[1] if len(headings) > 1 else brand
    sub = ""
    for n in node.depth_first():
        if n.tag in ("h3", "p") and n.text and len(n.text) < 100 and n.text not in headings:
            sub = n.text
            break
    paras = _paragraph_text(node, limit=3)
    imgs = _collect_images(node)
    return LayoutSection(
        type="hero",
        heading=main or brand,
        subheading=sub if sub != main else "",
        text=paras,
        image=imgs[0] if imgs else "",
        images=imgs[:3],
        button=_first_button_text(node),
        button_url=_first_link(node),
        extras={"brand": brand} if brand and brand != main else {},
    )


def _are_similar(nodes: list[DomNode]) -> bool:
    if len(nodes) < 2:
        return False
    scores = []
    for n in nodes:
        scores.append((
            1 if _first_heading(n) else 0,
            1 if _collect_images(n) else 0,
            1 if _list_bullets(n) else 0,
            min(len(n.text) // 80, 5),
            1 if _is_heading_card(n) else 0,
        ))
    ctr = Counter(scores)
    return ctr.most_common(1)[0][1] >= max(2, (len(nodes) + 1) // 2)


# ── Post-process ───────────────────────────────────────────────────────────────

def _merge_adjacent(sections: list[LayoutSection]) -> list[LayoutSection]:
    if not sections:
        return sections
    out: list[LayoutSection] = []
    for sec in sections:
        if (
            out
            and sec.type == "labeled_sections"
            and out[-1].type == "labeled_sections"
        ):
            out[-1].rows.extend(sec.rows)
            continue
        if (
            out
            and sec.type == "services_grid"
            and out[-1].type == "services_grid"
            and not sec.heading
        ):
            out[-1].items.extend(sec.items)
            out[-1].columns = max(out[-1].columns, sec.columns)
            continue
        out.append(sec)
    return out


def _ensure_services_title_grid(sections: list[LayoutSection]) -> list[LayoutSection]:
    """If labeled service rows exist without a preceding title grid, synthesize one.

    Common pattern: a row of service names, then title|bullets detail rows.
    """
    out: list[LayoutSection] = []
    for i, sec in enumerate(sections):
        if (
            sec.type == "labeled_sections"
            and len(sec.rows) >= 3
            and all(r.heading for r in sec.rows)
            and (not out or out[-1].type != "services_grid")
        ):
            titles = [LayoutItem(heading=r.heading) for r in sec.rows if r.heading]
            out.append(LayoutSection(
                type="services_grid",
                columns=3 if len(titles) >= 3 else 2,
                items=titles,
            ))
        out.append(sec)
    return out


def _drop_empty_grid_items(sections: list[LayoutSection]) -> list[LayoutSection]:
    out: list[LayoutSection] = []
    for sec in sections:
        if sec.type in ("services_grid", "features_grid", "cards") and sec.items:
            kept = []
            for it in sec.items:
                heading = (it.heading or "").strip()
                text = (it.text or "").strip()
                if heading or len(text) >= 20 or it.bullets or it.image:
                    kept.append(it)
            if len(kept) < 2 and sec.type != "cards":
                # Demote broken grids to rich text snippets.
                for it in kept:
                    out.append(LayoutSection(
                        type="rich_text",
                        heading=it.heading,
                        text=it.text,
                        bullets=it.bullets,
                        image=it.image,
                    ))
                continue
            if not kept:
                continue
            # Heading-only grids with 2+ titles are services grids.
            if (
                sec.type in ("cards", "features_grid")
                and all(it.heading and not it.text and not it.bullets for it in kept)
            ):
                sec = sec.model_copy(update={
                    "type": "services_grid",
                    "items": kept,
                    "columns": 3 if len(kept) >= 3 else 2,
                })
            else:
                sec = sec.model_copy(update={
                    "items": kept,
                    "columns": min(sec.columns or 1, len(kept)) or 1,
                })
        out.append(sec)
    return out


def _normalize_heroes(sections: list[LayoutSection], doc: DomDocument) -> list[LayoutSection]:
    """Ensure a single leading hero; demote extras; promote first strong intro."""
    if not sections:
        return sections

    h1 = next((t for lvl, t in doc.headings if lvl == "h1"), "")
    out: list[LayoutSection] = []
    hero_seen = False

    for sec in sections:
        if sec.type == "hero":
            if hero_seen:
                out.append(sec.model_copy(update={"type": "rich_text"}))
            else:
                out.append(sec)
                hero_seen = True
            continue
        out.append(sec)

    if hero_seen:
        # Move first hero to front when it is not already first.
        idx = next(i for i, s in enumerate(out) if s.type == "hero")
        if idx > 0:
            hero = out.pop(idx)
            out.insert(0, hero)
        return out

    # Promote first strong intro block.
    for i, sec in enumerate(out):
        if sec.type in ("text_image", "image_text", "rich_text", "two_column") and (
            (h1 and (sec.heading == h1 or h1 in (sec.heading or "")))
            or (sec.image and sec.heading)
            or (i == 0 and sec.heading)
        ):
            hero = sec.model_copy(update={"type": "hero"})
            return [hero, *out[:i], *out[i + 1:]]

    if h1 or doc.title:
        return [LayoutSection(type="hero", heading=h1 or doc.title, text=""), *out]
    return out


# ── Text / media helpers ───────────────────────────────────────────────────────

def _first_heading(node: DomNode) -> str:
    for n in node.depth_first():
        if n.role == "heading" and n.text:
            return n.text
    return ""


def _section_heading_outside(node: DomNode, cols: list[DomNode]) -> str:
    for n in node.depth_first():
        if n.role == "heading" and n.text:
            if any(_is_descendant(n, c) for c in cols):
                continue
            return n.text
    return ""


def _is_descendant(node: DomNode, ancestor: DomNode) -> bool:
    return any(id(x) == id(node) for x in ancestor.depth_first())


def _paragraph_text(node: DomNode, limit: int = 4) -> str:
    paras: list[str] = []
    for n in node.depth_first():
        if n.tag == "p" and n.text and len(n.text) >= 20:
            if n.text not in paras:
                paras.append(n.text)
        if len(paras) >= limit:
            break
    if paras:
        return "\n\n".join(paras)
    text = node.text
    h = _first_heading(node)
    if h and text.startswith(h):
        text = text[len(h):].strip()
    # Drop list text from paragraph fallback when bullets exist separately.
    return text[:1200]


def _list_bullets(node: DomNode) -> list[str]:
    bullets: list[str] = []
    for n in node.depth_first():
        if n.tag == "li" and n.text:
            t = n.text.strip()
            if t and t not in bullets and len(t) >= 3:
                bullets.append(t[:400])
    return bullets[:20]


def _collect_images(node: DomNode) -> list[str]:
    out: list[str] = []
    for n in node.depth_first():
        if n.img_src and n.img_src not in out and not n.img_src.startswith("data:"):
            # Skip tiny tracking pixels when width/height attrs present.
            out.append(n.img_src)
    return out


def _first_link(node: DomNode) -> str:
    for n in node.depth_first():
        if n.link_href and not n.link_href.startswith("#"):
            return n.link_href
    return ""


def _first_button_text(node: DomNode) -> str:
    for n in node.depth_first():
        if n.role == "button" and n.text:
            return n.text[:60]
    return ""
