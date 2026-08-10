"""
webmaker.agents.website_modernizer.library_index
================================================
Index the WebMaker Design Library under ``Library/``.

Each section folder (hero, services, …) contains per-source reference folders
with ``screenshot.png``, ``content.txt``, and ``metadata.json``.

These are design inspiration only — never competitors, never copied.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("modernizer.library")

# Canonical section keys used by the Design Blueprint (Agent 1).
SECTION_KEYS: tuple[str, ...] = (
    "hero",
    "services",
    "about",
    "features",
    "process",
    "gallery",
    "testimonials",
    "faq",
    "cta",
    "contact",
    "footer",
)

# Folder name aliases → canonical key
_FOLDER_ALIASES: dict[str, str] = {
    "hero": "hero",
    "services": "services",
    "about": "about",
    "features": "features",
    "process": "process",
    "gallery": "gallery",
    "testimonials": "testimonials",
    "faq": "faq",
    "cta": "cta",
    "contact": "contact",
    "footer": "footer",
    "header": "header",  # kept for catalog but not primary blueprint slots
    "before_after": "before_after",
    "pricing": "pricing",
    "statistics": "statistics",
    "team": "team",
    "partners": "partners",
    "service_areas": "service_areas",
    "blog": "blog",
}

# Sources that are especially strong for junk/removal/local-service niches.
_JUNK_REMOVAL_SOURCES = frozenset({
    "1800gotjunk",
    "gotjunk",
    "collegehunkshaulingjunk",
})

_LOCAL_SERVICE_SOURCES = frozenset({
    "mrhandyman",
    "mollymaid",
    "servicemasterclean",
    "neat",
    "rainbowrestores",
    "servpro",
    "windowhero",
    "pauldavis",
    "trugreen",
    "maidbrigade",
    "zerorez",
    "handymanconnection",
})


@dataclass
class DesignReference:
    """One Design Library reference (section × source)."""

    section: str
    source: str
    ref_id: str                 # e.g. "hero/1800gotjunk"
    path: str                   # relative path from Library root
    screenshot: str = ""        # absolute path if present
    layout: str = ""
    headings: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    visible_text_excerpt: str = ""
    images_count: int = 0
    buttons_count: int = 0
    headings_count: int = 0
    niche_tags: list[str] = field(default_factory=list)

    def to_prompt_block(self, *, max_paras: int = 2) -> str:
        """Compact text block for Claude prompts."""
        lines = [
            f"### {self.ref_id}",
            f"Path: Library/{self.path}",
        ]
        if self.layout:
            lines.append(f"Layout: {self.layout}")
        if self.headings:
            lines.append("Headings: " + " | ".join(self.headings[:4]))
        if self.buttons:
            lines.append("Buttons: " + " | ".join(self.buttons[:4]))
        for p in self.paragraphs[:max_paras]:
            if p and len(p) > 15:
                lines.append(f"Copy sample: {p[:160]}")
        if self.visible_text_excerpt and not self.paragraphs:
            lines.append(f"Visible: {self.visible_text_excerpt[:200]}")
        if self.niche_tags:
            lines.append("Tags: " + ", ".join(self.niche_tags))
        lines.append(
            f"Counts: headings={self.headings_count} buttons={self.buttons_count} "
            f"images={self.images_count}"
        )
        return "\n".join(lines)


@dataclass
class DesignLibraryCatalog:
    """Full indexed Design Library."""

    library_root: str
    references: list[DesignReference] = field(default_factory=list)
    by_section: dict[str, list[DesignReference]] = field(default_factory=dict)

    def refs_for(self, section: str) -> list[DesignReference]:
        return list(self.by_section.get(section.lower(), []))

    def get(self, ref_id: str) -> DesignReference | None:
        for r in self.references:
            if r.ref_id == ref_id:
                return r
        return None

    def to_prompt_catalog(self, *, sections: tuple[str, ...] | None = None) -> str:
        """Serialize catalog for Claude (blueprint + mapping prompts)."""
        wanted = sections or SECTION_KEYS
        parts = [
            "## Design Library (inspiration only — NEVER copy branding or copy)",
            "Study layout, spacing, hierarchy, CTA placement, card arrangement.",
            "Pick the best reference per section for THIS client's niche.",
            "Prefer variety — do not pick the same source for every section.",
            "",
        ]
        for sec in wanted:
            refs = self.refs_for(sec)
            if not refs:
                continue
            parts.append(f"## Section folder: {sec}/ ({len(refs)} references)")
            for r in refs:
                parts.append(r.to_prompt_block())
                parts.append("")
        return "\n".join(parts)

    def summary_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in sorted(self.by_section.items()) if v}


def resolve_library_root(settings: object | None = None) -> Path:
    """Locate the Design Library directory."""
    candidates: list[Path] = []
    if settings is not None:
        for attr in ("design_library_dir", "library_dir", "webmaker_root"):
            val = getattr(settings, attr, None)
            if val:
                p = Path(val)
                candidates.append(p if p.name.lower() == "library" else p / "Library")
    # Package-relative: webmaker/agents/... → WebMaker/Library
    here = Path(__file__).resolve()
    candidates.append(here.parents[3] / "Library")  # WebMaker/Library
    candidates.append(Path.cwd() / "Library")
    candidates.append(Path.cwd() / "WebMaker" / "Library")

    for c in candidates:
        if c.is_dir():
            return c
    # Default even if empty — caller handles missing
    return here.parents[3] / "Library"


def index_library(library_root: Path | None = None, *, settings: object | None = None) -> DesignLibraryCatalog:
    """Scan ``Library/`` and return a typed catalog."""
    root = Path(library_root) if library_root else resolve_library_root(settings)
    catalog = DesignLibraryCatalog(library_root=str(root))
    if not root.is_dir():
        log.warning("Design Library not found at {p}", p=root)
        return catalog

    for section_dir in sorted(root.iterdir()):
        if not section_dir.is_dir():
            continue
        folder = section_dir.name
        if folder.lower() == "company":
            # Company dumps are supplementary; section folders are primary.
            continue
        section = _FOLDER_ALIASES.get(folder.lower(), folder.lower())

        for source_dir in sorted(section_dir.iterdir()):
            if not source_dir.is_dir():
                continue
            ref = _parse_reference(root, section, source_dir)
            if ref is None:
                continue
            catalog.references.append(ref)
            catalog.by_section.setdefault(section, []).append(ref)

    log.info(
        "Design Library indexed — {n} refs across {s} sections at {p}",
        n=len(catalog.references),
        s=len(catalog.by_section),
        p=root,
    )
    return catalog


def _parse_reference(root: Path, section: str, source_dir: Path) -> DesignReference | None:
    source = source_dir.name
    shot = source_dir / "screenshot.png"
    meta_path = source_dir / "metadata.json"
    content_path = source_dir / "content.txt"

    # Require at least one of screenshot / metadata / content
    if not (shot.is_file() or meta_path.is_file() or content_path.is_file()):
        return None

    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}

    headings, buttons, paragraphs, visible = _parse_content_txt(content_path)

    tags: list[str] = []
    src_key = source.lower().replace(" ", "")
    if src_key in {s.lower() for s in _JUNK_REMOVAL_SOURCES} or "junk" in src_key:
        tags.extend(["junk-removal", "local-service", "haulage"])
    if src_key in {s.lower() for s in _LOCAL_SERVICE_SOURCES}:
        tags.append("local-service")
    if "maid" in src_key or "clean" in src_key or "neat" in src_key:
        tags.append("cleaning")
    if "handyman" in src_key:
        tags.append("handyman")
    if "restore" in src_key or "servpro" in src_key:
        tags.append("restoration")

    rel = str(source_dir.relative_to(root)).replace("\\", "/")
    return DesignReference(
        section=section,
        source=source,
        ref_id=f"{section}/{source}",
        path=rel,
        screenshot=str(shot) if shot.is_file() else "",
        layout=str(meta.get("layout") or ""),
        headings=headings,
        buttons=buttons,
        paragraphs=paragraphs,
        visible_text_excerpt=visible,
        images_count=int(meta.get("images") or 0),
        buttons_count=int(meta.get("buttons") or len(buttons)),
        headings_count=int(meta.get("headings") or len(headings)),
        niche_tags=sorted(set(tags)),
    )


def _parse_content_txt(path: Path) -> tuple[list[str], list[str], list[str], str]:
    headings: list[str] = []
    buttons: list[str] = []
    paragraphs: list[str] = []
    visible = ""
    if not path.is_file():
        return headings, buttons, paragraphs, visible
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return headings, buttons, paragraphs, visible

    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("## headings"):
            section = "headings"
            continue
        if low.startswith("## paragraphs"):
            section = "paragraphs"
            continue
        if low.startswith("## buttons"):
            section = "buttons"
            continue
        if low.startswith("## visible"):
            section = "visible"
            continue
        if line.startswith("#"):
            section = ""
            continue
        if line.startswith("- "):
            item = line[2:].strip()
            if item.lower() in ("(none)", "none", ""):
                continue
            if section == "headings":
                headings.append(item)
            elif section == "buttons":
                buttons.append(item)
            elif section == "paragraphs":
                paragraphs.append(item)
        elif section == "visible":
            visible = (visible + " " + line).strip() if visible else line

    return headings, buttons, paragraphs, visible[:400]


def catalog_as_dict(catalog: DesignLibraryCatalog) -> dict[str, Any]:
    """JSON-serializable dump of the catalog."""
    return {
        "library_root": catalog.library_root,
        "counts": catalog.summary_counts(),
        "references": [asdict(r) for r in catalog.references],
    }
