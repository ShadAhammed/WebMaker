"""
webmaker.agents.live_demo_renderer.materialize_content
======================================================
Apply approved OP-Content tips onto existing ``optimized_*.json`` pages.

CRITICAL: never rebuild the page from scratch. Tips are merged into the
current body HTML (hero title, CTA label, text replace, small HTML insert).
A full snapshot is saved under ``json/_backups/last_render/`` before changes
so the operator can Undo.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger
from webmaker.core.schema import write_versioned_json
from webmaker.schemas.render import RenderRequest

log = get_logger("renderer.materialize")

_BACKUP_REL = Path("_backups") / "last_render"
_MANIFEST = "manifest.json"

_HERO_SECTIONS = {
    "hero", "überschrift", "uberschrift", "headline", "h1", "titel", "start",
}
_CTA_SECTIONS = {
    "cta", "button", "knopf", "anrufen", "kontakt-button", "call", "angebot",
}


def materialize_optimized_pages(
    data_dir: Path,
    request: RenderRequest,
) -> list[Path]:
    """Patch existing optimized pages with approved tips (backup first)."""
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    # Only pages that actually have approved tips
    by_slug: dict[str, list] = {}
    for rec in request.approved or []:
        slug = (getattr(rec, "page_slug", "") or "").strip().lower()
        if not slug:
            continue
        by_slug.setdefault(slug, []).append(rec)

    if not by_slug:
        log.warning("No approved recommendations with page_slug — nothing to materialize")
        return []

    # Snapshot current files before mutating
    _backup_pages(json_dir, list(by_slug.keys()))

    written: list[Path] = []
    for slug, recs in by_slug.items():
        path = json_dir / f"optimized_{slug}.json"
        existing = _load_page(path)
        if not existing:
            log.warning(
                "No existing {f} — skipping tip apply (will not invent a bare page)",
                f=path.name,
            )
            continue
        before_len = len(str(existing.get("body_html") or ""))
        patched = _apply_tips(existing, recs)
        after_len = len(str(patched.get("body_html") or ""))
        # Safety: refuse catastrophic shrink (< 40% of prior rich page)
        if before_len > 2000 and after_len < before_len * 0.4:
            log.error(
                "Refusing to write {f}: body shrank {a}→{b} chars (would destroy page)",
                f=path.name, a=before_len, b=after_len,
            )
            continue
        write_versioned_json(path, patched)
        written.append(path)
        log.info(
            "Patched {n} tip(s) into {f} (body {a}→{b} chars)",
            n=len(recs), f=path.name, a=before_len, b=after_len,
        )
    return written


def backup_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "json" / _BACKUP_REL


def has_render_backup(data_dir: Path) -> bool:
    man = backup_dir(data_dir) / _MANIFEST
    return man.is_file()


def restore_last_render_backup(data_dir: Path) -> list[str]:
    """Restore ``optimized_*.json`` from the last render backup. Returns slugs."""
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    bdir = backup_dir(data_dir)
    man_path = bdir / _MANIFEST
    if not man_path.is_file():
        raise FileNotFoundError("No last-render backup found")

    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FileNotFoundError(f"Corrupt backup manifest: {exc}") from exc

    slugs = list(manifest.get("slugs") or [])
    restored: list[str] = []
    for slug in slugs:
        src = bdir / f"optimized_{slug}.json"
        dst = json_dir / f"optimized_{slug}.json"
        if not src.is_file():
            log.warning("Backup missing for {s}", s=slug)
            continue
        shutil.copy2(src, dst)
        restored.append(slug)
        log.info("Restored {f} from last-render backup", f=dst.name)
    return restored


def _backup_pages(json_dir: Path, slugs: list[str]) -> None:
    bdir = json_dir / _BACKUP_REL
    if bdir.exists():
        shutil.rmtree(bdir, ignore_errors=True)
    bdir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for slug in slugs:
        src = json_dir / f"optimized_{slug}.json"
        if src.is_file():
            shutil.copy2(src, bdir / src.name)
            saved.append(slug)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "slugs": saved,
    }
    (bdir / _MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Render backup saved → {p} ({n} page(s))", p=bdir, n=len(saved))


def _load_page(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _apply_tips(page: dict[str, Any], recs: list) -> dict[str, Any]:
    out = dict(page)
    body = str(out.get("body_html") or "")
    hero = dict(out.get("hero") or {}) if isinstance(out.get("hero"), dict) else {}
    applied = 0

    for rec in recs:
        section = (getattr(rec, "section", "") or "").strip().lower()
        current = (getattr(rec, "current", "") or "").strip()
        proposed = (getattr(rec, "proposed_html", "") or "").strip()
        tip = (getattr(rec, "recommendation", "") or "").strip()

        ready = proposed or _extract_ready_phrase(tip)
        if not ready:
            log.warning(
                "Tip has no ready German copy — skipped ({sec})",
                sec=section or "?",
            )
            continue

        # 1) Exact replace of "current" snippet when present
        if current and len(current) >= 4 and current in body:
            body = body.replace(current, ready, 1)
            applied += 1
            if _is_hero_section(section):
                hero["heading"] = _plain(ready)[:160]
            continue

        # 2) Hero / headline → replace first visible H1 / hero title
        if _is_hero_section(section) or _looks_like_headline(ready, section):
            title = _sanitize_hero_title(ready)
            if not title:
                continue
            new_body, ok = _replace_hero_title(body, title)
            if ok:
                body = new_body
                hero["heading"] = title[:160]
                headings = list(out.get("headings") or [])
                if headings:
                    headings[0] = title[:160]
                else:
                    headings = [title[:160]]
                out["headings"] = headings
                applied += 1
                continue

        # 3) CTA / button label
        if _is_cta_section(section) or _looks_like_cta(ready):
            new_body, ok = _replace_primary_cta(body, ready)
            if ok:
                body = new_body
                hero["cta_primary"] = _plain(ready)[:80]
                applied += 1
                continue

        # 4) Subheading / trust line
        if any(k in section for k in ("sub", "trust", "unterzeile", "vertrauen")):
            new_body, ok = _replace_class_text(
                body,
                ("wm3-hero-card__sub", "wm3-hero-card__trust", "hero-sub", "trust"),
                ready,
            )
            if ok:
                body = new_body
                if "sub" in section or "unter" in section:
                    hero["subheading"] = _plain(ready)[:160]
                applied += 1
                continue

        # 5) Soft insert: append a small HTML note after hero, keep rest intact
        if ready.startswith("<"):
            fragment = ready
        else:
            fragment = (
                f'<!-- wp:html -->\n'
                f'<div class="wm3-op-tip"><p>{escape(_plain(ready))}</p></div>\n'
                f'<!-- /wp:html -->'
            )
        body = _insert_after_hero(body, fragment)
        applied += 1

    out["body_html"] = body
    if hero:
        out["hero"] = hero
    out["op_tips_applied"] = applied
    return out


def _is_hero_section(section: str) -> bool:
    return any(k in section for k in _HERO_SECTIONS)


def _is_cta_section(section: str) -> bool:
    return any(k in section for k in _CTA_SECTIONS)


def _looks_like_headline(text: str, section: str) -> bool:
    plain = _plain(text)
    return len(plain) <= 100 and (
        _is_hero_section(section) or plain[:1].isupper()
    ) and not _looks_like_cta(plain)


def _looks_like_cta(text: str) -> bool:
    plain = _plain(text).lower()
    if len(plain) > 70:
        return False
    return any(
        k in plain
        for k in ("jetzt", "anruf", "angebot", "kontakt", "whatsapp", "anfragen")
    )


def _plain(text: str) -> str:
    t = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", t).strip()


def _sanitize_hero_title(text: str) -> str:
    """Strip CTA phrases glued onto headlines (KostenJetzt anfragen…)."""
    t = _plain(text)
    t = re.sub(
        r"(Kosten|Festpreis|Siegen)(Jetzt|jetzt)\b",
        r"\1",
        t,
    )
    t = re.sub(
        r"\s*(Jetzt\s+(kostenlos\s+)?(anfragen|anrufen).*)\s*$",
        "",
        t,
        flags=re.I,
    )
    t = t.strip(" –-")
    return t or _plain(text)


def _extract_ready_phrase(tip: str) -> str:
    """Pull a quoted German phrase from a tip, else empty."""
    if not tip:
        return ""
    quotes = re.findall(r"[„\"']([^\"'„”]{5,160})[\"'”]", tip)
    for q in quotes:
        q = q.strip()
        if q and not _looks_like_instruction(q):
            return q
    if not _looks_like_instruction(tip) and (
        re.search(r"[äöüÄÖÜß]", tip)
        or re.search(r"\b(Jetzt|Festpreis|Entrümpelung|Kontakt|Siegen)\b", tip, re.I)
    ):
        return tip.strip()
    return ""


def _looks_like_instruction(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    markers = (
        "rewrite ", "add a ", "place a ", "create a ", "rename ", "improve ",
        "change the ", "update the ", "should ", "consider ",
        "schreibe ", "ändere ", "füge ", "ersetze ",
    )
    return any(low.startswith(m) for m in markers)


def _replace_hero_title(body: str, new_title: str) -> tuple[str, bool]:
    plain = escape(_sanitize_hero_title(new_title))
    patterns = [
        r'(<h1 class="wm3-hero-split__title">)(.*?)(</h1>)',
        r'(<h1 class="wm3-hero-card__title">)(.*?)(</h1>)',
        r'(<h1\b[^>]*>)(.*?)(</h1>)',
        r'(class="wm3-hero-card__title"[^>]*>)(.*?)(</)',
        r'(<!-- wp:heading[^>]*-->\s*<h1\b[^>]*>)(.*?)(</h1>)',
    ]
    for pat in patterns:
        if re.search(pat, body, flags=re.I | re.S):
            updated = re.sub(pat, rf"\1{plain}\3", body, count=1, flags=re.I | re.S)
            return updated, updated != body
    return body, False


def _replace_primary_cta(body: str, new_label: str) -> tuple[str, bool]:
    plain = escape(_plain(new_label))
    patterns = [
        r'(<a class="wm3-btn wm3-btn--primary"[^>]*>)(.*?)(</a>)',
        r'(<a class="wm3-header-cta"[^>]*>)(.*?)(</a>)',
        r'(<a class="wp-block-button__link[^"]*"[^>]*>)(.*?)(</a>)',
    ]
    for pat in patterns:
        if re.search(pat, body, flags=re.I | re.S):
            updated = re.sub(pat, rf"\1{plain}\3", body, count=1, flags=re.I | re.S)
            return updated, updated != body
    return body, False


def _replace_class_text(
    body: str,
    class_names: tuple[str, ...],
    new_text: str,
) -> tuple[str, bool]:
    plain = escape(_plain(new_text))
    for cls in class_names:
        pat = rf'(<[^>]*class="[^"]*{re.escape(cls)}[^"]*"[^>]*>)(.*?)(</[^>]+>)'
        if re.search(pat, body, flags=re.I | re.S):
            updated = re.sub(pat, rf"\1{plain}\3", body, count=1, flags=re.I | re.S)
            if updated != body:
                return updated, True
    return body, False


def _insert_after_hero(body: str, fragment: str) -> str:
    # After first hero section /wp:html or first </section>
    markers = (
        "<!-- /wp:html -->",
        "</section>",
        "wm3-hero-overlay",
    )
    # Prefer closing of first wp:html block that follows a hero
    hero_idx = body.find("wm3-hero")
    if hero_idx >= 0:
        close = body.find("<!-- /wp:html -->", hero_idx)
        if close >= 0:
            at = close + len("<!-- /wp:html -->")
            return body[:at] + "\n\n" + fragment + body[at:]
    for m in markers:
        idx = body.find(m)
        if idx >= 0:
            at = idx + len(m)
            return body[:at] + "\n\n" + fragment + body[at:]
    return body + "\n\n" + fragment
