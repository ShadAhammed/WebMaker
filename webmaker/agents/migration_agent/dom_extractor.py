"""
webmaker.agents.migration_agent.dom_extractor
=============================================
Step 1 — DOM Extractor.

Reads raw HTML for a page and builds a hierarchy-preserving node tree.
Does **not** flatten to text. Does **not** use CMS-specific class semantics
beyond generic structural HTML (main, nav, header, footer, section, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("migration.dom_extractor")

_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "iframe", "template",
    "link", "meta", "head",
})

_INLINE_TAGS = frozenset({
    "span", "a", "strong", "b", "em", "i", "u", "small", "label", "br", "wbr",
})


@dataclass
class DomNode:
    """One node in the extracted DOM tree."""

    tag: str = ""
    classes: list[str] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""           # direct + descendant visible text (collapsed)
    own_text: str = ""       # text belonging only to this node (not children)
    children: list[DomNode] = field(default_factory=list)
    # Lightweight geometric / role hints (no CMS knowledge).
    role: str = ""           # nav | header | footer | main | section | content
    is_block: bool = True
    link_href: str = ""
    img_src: str = ""
    img_alt: str = ""
    child_count: int = 0
    similar_siblings: int = 0  # filled by analyzer helpers

    def depth_first(self) -> list[DomNode]:
        out = [self]
        for c in self.children:
            out.extend(c.depth_first())
        return out

    def block_children(self) -> list[DomNode]:
        return [c for c in self.children if c.is_block and (c.text or c.img_src or c.children)]


@dataclass
class DomDocument:
    """Extracted document for one page."""

    url: str = ""
    title: str = ""
    root: DomNode | None = None
    images: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (text, href)
    headings: list[tuple[str, str]] = field(default_factory=list)  # (level, text)


def extract_dom_from_html(html: str, *, url: str = "") -> DomDocument:
    """Parse HTML into a DomDocument. Prefer lxml via BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup, NavigableString, Tag
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for DOM extraction") from exc

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_SKIP_TAGS):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    body = soup.body or soup
    root = _convert(body)
    _annotate_roles(root)

    doc = DomDocument(url=url, title=title, root=root)
    if root:
        for n in root.depth_first():
            if n.img_src:
                doc.images.append(n.img_src)
            if n.link_href and n.own_text:
                doc.links.append((n.own_text, n.link_href))
            if n.tag in ("h1", "h2", "h3", "h4", "h5", "h6") and n.text:
                doc.headings.append((n.tag, n.text))
    log.info(
        "DOM extracted: title={t!r} images={i} headings={h} nodes={n}",
        t=title[:60], i=len(doc.images), h=len(doc.headings),
        n=len(root.depth_first()) if root else 0,
    )
    return doc


def extract_dom_from_file(path: Path, *, url: str = "") -> DomDocument:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    return extract_dom_from_html(html, url=url)


def _convert(tag: Any) -> DomNode:
    from bs4 import NavigableString, Tag

    if isinstance(tag, NavigableString):
        text = str(tag).strip()
        return DomNode(tag="#text", text=text, own_text=text, is_block=False)

    if not isinstance(tag, Tag):
        return DomNode(tag="unknown")

    name = (tag.name or "").lower()
    classes = [c for c in (tag.get("class") or []) if isinstance(c, str)]
    attrs: dict[str, str] = {}
    for k in ("id", "role", "href", "src", "alt", "type", "name"):
        v = tag.get(k)
        if v:
            attrs[k] = str(v)

    children: list[DomNode] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                children.append(DomNode(tag="#text", text=t, own_text=t, is_block=False))
        elif isinstance(child, Tag):
            if (child.name or "").lower() in _SKIP_TAGS:
                continue
            children.append(_convert(child))

    # Collapse trivial wrapper chains later in analyzer; keep hierarchy here.
    own_parts: list[str] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                own_parts.append(t)
    own_text = _collapse(" ".join(own_parts))

    full_text = _collapse(tag.get_text(" ", strip=True))
    is_block = name not in _INLINE_TAGS and name != "#text"

    node = DomNode(
        tag=name,
        classes=classes,
        attrs=attrs,
        text=full_text,
        own_text=own_text,
        children=children,
        is_block=is_block,
        link_href=str(tag.get("href") or "") if name == "a" else "",
        img_src=_abs_src(str(tag.get("src") or "")) if name == "img" else "",
        img_alt=str(tag.get("alt") or "") if name == "img" else "",
        child_count=len(children),
    )

    # Promote nested img when this is a figure/picture wrapper.
    if not node.img_src:
        for c in children:
            if c.img_src:
                node.img_src = c.img_src
                node.img_alt = c.img_alt
                break
    return node


def _abs_src(src: str) -> str:
    return src.strip()


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _annotate_roles(node: DomNode | None) -> None:
    if node is None:
        return
    tag = node.tag
    role_attr = (node.attrs.get("role") or "").lower()
    classes_l = " ".join(node.classes).lower()
    nid = (node.attrs.get("id") or "").lower()

    if tag == "nav" or role_attr == "navigation" or "menu" in classes_l:
        node.role = "nav"
    elif tag == "header" or role_attr == "banner":
        node.role = "header"
    elif tag == "footer" or role_attr == "contentinfo":
        node.role = "footer"
    elif tag == "main" or role_attr == "main" or nid in ("content", "main", "main-content"):
        node.role = "main"
    elif tag in ("section", "article") or role_attr == "region":
        node.role = "section"
    elif tag in ("ul", "ol"):
        node.role = "list"
    elif tag == "form":
        node.role = "form"
    elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        node.role = "heading"
    elif tag == "img":
        node.role = "image"
    elif tag in ("button",) or (tag == "a" and _looks_like_button(node)):
        node.role = "button"
    else:
        node.role = "content"

    for c in node.children:
        _annotate_roles(c)


def _looks_like_button(node: DomNode) -> bool:
    classes_l = " ".join(node.classes).lower()
    if any(k in classes_l for k in ("btn", "button", "cta")):
        return True
    return bool(node.text) and len(node.text) < 48
