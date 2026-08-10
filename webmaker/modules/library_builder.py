"""
webmaker.modules.library_builder
================================
Design Library Builder — deterministic, AI-free visual design extraction.

Given a website URL, opens the page with Playwright, waits for a full render,
captures a high-quality full-page PNG, detects semantic homepage sections
(without relying on CSS class names), and writes a reusable visual library::

    Library/<WebsiteName>/
        homepage.png
        report.json
        sections/
            02_hero/
                screenshot.png
                metadata.json
                content.txt
            ...

No AI analysis. No SEO. No competitor crawling.
Only visual design library construction.

Usage::

    python -m webmaker.modules.library_builder https://www.neat.com
    python webmaker/modules/library_builder.py https://www.neat.com

Primary class: LibraryBuilder
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Browser, Page, sync_playwright

# ── Paths ──────────────────────────────────────────────────────────────────────

_WEBMAKER_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_LIBRARY_ROOT = _WEBMAKER_ROOT / "Library"

# ── Section catalog (fixed order) ──────────────────────────────────────────────

SECTION_CATALOG: list[tuple[str, str, str]] = [
    # (folder_slug, report_key, display_name)
    ("01_header",        "Header",         "Header"),
    ("02_hero",          "Hero",           "Hero"),
    ("03_services",      "Services",       "Services"),
    ("04_about",         "About",          "About"),
    ("05_features",      "Features",       "Features"),
    ("06_process",       "Process",        "Process"),
    ("07_before_after",  "BeforeAfter",    "Before & After"),
    ("08_gallery",       "Gallery",        "Gallery"),
    ("09_testimonials",  "Testimonials",   "Testimonials"),
    ("10_statistics",    "Statistics",     "Statistics"),
    ("11_team",          "Team",           "Team"),
    ("12_faq",           "FAQ",            "FAQ"),
    ("13_pricing",       "Pricing",        "Pricing"),
    ("14_service_areas", "ServiceAreas",   "Service Areas"),
    ("15_blog",          "Blog",           "Blog"),
    ("16_partners",      "Partners",       "Partners"),
    ("17_cta",           "CTA",            "CTA"),
    ("18_contact",       "Contact",        "Contact"),
    ("19_footer",        "Footer",         "Footer"),
]

_REPORT_KEYS = [k for _, k, _ in SECTION_CATALOG]
_SLUG_BY_KEY = {k: s for s, k, _ in SECTION_CATALOG}
_DISPLAY_BY_KEY = {k: d for _, k, d in SECTION_CATALOG}
# Clean section folder names used for the cross-company index:
#   Library/hero/Neat/, Library/services/Mrhandyman/, …
_SECTION_INDEX_NAMES = [slug.split("_", 1)[1] for slug, _, _ in SECTION_CATALOG]
_INDEX_BY_SLUG = {slug: slug.split("_", 1)[1] for slug, _, _ in SECTION_CATALOG}
_SECTION_ARTIFACTS = ("screenshot.png", "metadata.json", "content.txt")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_VIEWPORT = {"width": 1440, "height": 900}

# Keyword patterns used for semantic classification (content / role — not CSS).
_PATTERNS: dict[str, re.Pattern[str]] = {
    "Services": re.compile(
        r"\b(our\s+services?|services?|what\s+we\s+(do|offer)|"
        r"solutions?|offerings?)\b",
        re.I,
    ),
    "About": re.compile(
        r"\b(about\s+us|who\s+we\s+are|our\s+story|our\s+mission|"
        r"about|company)\b",
        re.I,
    ),
    "Features": re.compile(
        r"\b(why\s+choose|features?|benefits?|advantages?|"
        r"what\s+sets\s+us|why\s+us|our\s+difference)\b",
        re.I,
    ),
    "Process": re.compile(
        r"\b(how\s+it\s+works|our\s+process|process|steps?|"
        r"how\s+we\s+work|workflow|get\s+started)\b",
        re.I,
    ),
    "BeforeAfter": re.compile(
        r"\b(before\s*(&|and)\s*after|transformations?|"
        r"results?)\b",
        re.I,
    ),
    "Gallery": re.compile(
        r"\b(gallery|portfolio|our\s+work|projects?|"
        r"case\s+studies|photos?)\b",
        re.I,
    ),
    "Testimonials": re.compile(
        r"\b(testimonials?|reviews?|what\s+(our\s+)?(clients?|customers?)\s+say|"
        r"customer\s+stories|ratings?|loved\s+by)\b",
        re.I,
    ),
    "Statistics": re.compile(
        r"\b(by\s+the\s+numbers|stats?|statistics|in\s+numbers|"
        r"milestones?|impact)\b",
        re.I,
    ),
    "Team": re.compile(
        r"\b(our\s+team|meet\s+the\s+team|team|leadership|"
        r"experts?|staff)\b",
        re.I,
    ),
    "FAQ": re.compile(
        r"\b(faq|frequently\s+asked|questions?\s*&?\s*answers?|"
        r"common\s+questions)\b",
        re.I,
    ),
    "Pricing": re.compile(
        r"\b(pricing|plans?|packages?|rates?|cost|how\s+much)\b",
        re.I,
    ),
    "ServiceAreas": re.compile(
        r"\b(service\s+areas?|areas?\s+we\s+serve|locations?|"
        r"where\s+we\s+(serve|work)|cities\s+we|coverage)\b",
        re.I,
    ),
    "Blog": re.compile(
        r"\b(blog|news|articles?|resources?|insights?|"
        r"latest\s+posts?)\b",
        re.I,
    ),
    "Partners": re.compile(
        r"\b(partners?|certifications?|accredited|trusted\s+by|"
        r"as\s+seen\s+in|affiliations?|brands?\s+we)\b",
        re.I,
    ),
    "CTA": re.compile(
        r"\b(get\s+(a\s+)?(free\s+)?quote|request\s+(a\s+)?quote|"
        r"book\s+now|schedule|call\s+now|contact\s+us\s+today|"
        r"get\s+started|free\s+estimate|ready\s+to)\b",
        re.I,
    ),
    "Contact": re.compile(
        r"\b(contact(\s+us)?|get\s+in\s+touch|reach\s+us|"
        r"send\s+(us\s+)?a\s+message)\b",
        re.I,
    ),
}


# ── Data models ────────────────────────────────────────────────────────────────


@dataclass
class SectionHit:
    """One detected semantic section with geometry and extracted text."""

    key: str
    label: str
    bounding_box: list[float]  # [x, y, width, height]
    layout: str = ""
    images: int = 0
    buttons: int = 0
    headings: int = 0
    paragraphs: int = 0
    headings_text: list[str] = field(default_factory=list)
    paragraphs_text: list[str] = field(default_factory=list)
    buttons_text: list[str] = field(default_factory=list)
    visible_text: str = ""
    detected: bool = True

    @property
    def folder_slug(self) -> str:
        return _SLUG_BY_KEY[self.key]


@dataclass
class LibraryResult:
    """Aggregate result of a library build run."""

    url: str
    website_name: str
    output_dir: Path
    homepage_png: Path
    report: dict[str, Any]
    sections: list[SectionHit]


# ── Browser helpers ────────────────────────────────────────────────────────────


class PageRenderer:
    """Opens a URL in Playwright Chromium and waits for a complete render."""

    def __init__(
        self,
        *,
        timeout_ms: int = 60_000,
        viewport: dict[str, int] | None = None,
        user_agent: str = _USER_AGENT,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.viewport = viewport or dict(_VIEWPORT)
        self.user_agent = user_agent

    def open(self, browser: Browser, url: str) -> Page:
        context = browser.new_context(
            viewport=self.viewport,
            user_agent=self.user_agent,
            device_scale_factor=2,  # crisp PNG screenshots
        )
        page = context.new_page()
        page.set_default_timeout(self.timeout_ms)
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._wait_until_settled(page)
        self._dismiss_overlays(page)
        self._wait_until_settled(page)
        self._scroll_full_page(page)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        return page

    def _wait_until_settled(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except Exception:
            page.wait_for_load_state("load", timeout=min(15_000, self.timeout_ms))
        page.wait_for_timeout(600)
        # Wait for web fonts so text metrics / layout stabilize.
        try:
            page.evaluate(
                """async () => {
                    if (document.fonts && document.fonts.ready) {
                        await document.fonts.ready;
                    }
                }"""
            )
        except Exception:
            pass

    def _scroll_full_page(self, page: Page) -> None:
        """Scroll through the document to trigger lazy-loaded media."""
        try:
            height = page.evaluate("() => document.body.scrollHeight") or 0
            step = max(self.viewport["height"] // 2, 400)
            y = 0
            while y < height:
                page.evaluate(f"window.scrollTo(0, {y})")
                page.wait_for_timeout(180)
                y += step
                height = page.evaluate("() => document.body.scrollHeight") or height
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(400)
        except Exception:
            pass

    @staticmethod
    def _dismiss_overlays(page: Page) -> None:
        labels = (
            "Accept all", "Accept All", "Allow all", "Allow All",
            "I agree", "Agree", "Got it", "OK", "Close",
            "Alle akzeptieren", "Alles akzeptieren", "Zustimmen",
            "Ich stimme zu", "Alle Cookies akzeptieren",
        )
        for label in labels:
            try:
                btn = page.get_by_role(
                    "button", name=re.compile(re.escape(label), re.I),
                )
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(350)
                    return
            except Exception:
                pass
            try:
                loc = page.locator(
                    f"button:has-text('{label}'), a:has-text('{label}'), "
                    f"[role='button']:has-text('{label}')"
                )
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    page.wait_for_timeout(350)
                    return
            except Exception:
                pass
        for sel in (
            "#onetrust-accept-btn-handler",
            "button[data-testid='uc-accept-all-button']",
            "a.cc-btn.cc-allow",
            ".cm-btn-accept-all",
            "#CookieBoxSaveButton",
            "#acceptAllButton",
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    page.wait_for_timeout(350)
                    return
            except Exception:
                pass


# ── Section detection (semantic — not CSS-class based) ─────────────────────────


_DETECT_CANDIDATES_JS = r"""
() => {
  const MIN_H = 48;
  const MIN_W = 200;
  const MAX_SECTION_H = 1400;

  const isVisible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width >= MIN_W && r.height >= MIN_H;
  };

  const absBox = (el) => {
    const r = el.getBoundingClientRect();
    const x = r.left + window.scrollX;
    const y = r.top + window.scrollY;
    return {
      x: Math.max(0, Math.round(x)),
      y: Math.max(0, Math.round(y)),
      width: Math.round(r.width),
      height: Math.round(r.height),
    };
  };

  const textOf = (el, limit = 4000) =>
    ((el && el.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, limit);

  const collect = (el) => {
    const headings = [...el.querySelectorAll('h1,h2,h3,h4,h5,h6')]
      .filter(isVisible)
      .map(h => (h.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .slice(0, 30);
    const paragraphs = [...el.querySelectorAll('p')]
      .filter(isVisible)
      .map(p => (p.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(t => t.length >= 12)
      .slice(0, 40);
    const buttons = [...el.querySelectorAll(
      'a, button, [role="button"], input[type="submit"], input[type="button"]'
    )]
      .filter(isVisible)
      .map(b => {
        const t = (b.innerText || b.value || b.getAttribute('aria-label') || '')
          .replace(/\s+/g, ' ').trim();
        return t;
      })
      .filter(t => t.length >= 1 && t.length <= 80)
      .slice(0, 20);
    const images = [...el.querySelectorAll('img, picture, video, svg')]
      .filter(n => {
        if (!isVisible(n)) return false;
        const r = n.getBoundingClientRect();
        return r.width >= 40 && r.height >= 40;
      }).length;
    const hasForm = !!el.querySelector('form');
    const hasNav = el.matches('nav, [role="navigation"]')
      || !!el.querySelector('nav, [role="navigation"]');
    const linkCount = el.querySelectorAll('a[href]').length;
    const listItems = el.querySelectorAll('li').length;
    const questionMarks = (textOf(el).match(/\?/g) || []).length;
    const bigNumbers = (textOf(el).match(
      /\b\d{1,3}(?:,\d{3})+\b|\b\d+\s*%|\b\d{2,}\+?\b/g
    ) || []).length;
    const tag = (el.tagName || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    return {
      box: absBox(el),
      tag, role, hasForm, hasNav, linkCount, listItems,
      questionMarks, bigNumbers, images,
      headings, paragraphs, buttons,
      visibleText: textOf(el),
    };
  };

  const seen = new Set();
  const candidates = [];

  const push = (el, hint) => {
    if (!el || seen.has(el) || !isVisible(el)) return;
    const data = collect(el);
    if (data.box.height < MIN_H || data.box.width < MIN_W) return;

    // Unwrap oversized / single-child wrapper chains into real bands.
    if (data.box.height > MAX_SECTION_H) {
      let node = el;
      for (let depth = 0; depth < 8; depth++) {
        const kids = [...node.children].filter(isVisible);
        if (kids.length === 1) {
          node = kids[0];
          continue;
        }
        if (kids.length >= 2) {
          kids.forEach(k => push(k, hint + '>child'));
          return;
        }
        break;
      }
      // Still one blob — keep it but mark as oversized.
    }

    seen.add(el);
    candidates.push({ ...data, hint: hint || '' });
  };

  // Fixed / sticky chrome often acts as the visual header (limit scan).
  const chromeProbe = [
    ...document.querySelectorAll('header, nav, [role="banner"], [role="navigation"]'),
    ...[...document.body.children].slice(0, 12),
    ...[...(document.querySelector('main') || document.body).children].slice(0, 8),
  ];
  for (const el of chromeProbe) {
    const st = window.getComputedStyle(el);
    if (!['fixed', 'sticky'].includes(st.position)) continue;
    const r = el.getBoundingClientRect();
    if (r.top > 80 || r.height < 40 || r.height > 160 || r.width < MIN_W) continue;
    if ((el.querySelectorAll('a[href]').length >= 2) || el.querySelector('nav') || el.matches('nav, header')) {
      push(el, 'sticky-header');
    }
  }

  // Landmarks first.
  document.querySelectorAll(
    'header, [role="banner"], nav, [role="navigation"], footer, [role="contentinfo"]'
  ).forEach(el => {
    const tag = (el.tagName || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    push(el, tag || role || 'landmark');
  });

  // Sectioning elements under main / body.
  const roots = [
    document.querySelector('main'),
    document.querySelector('[role="main"]'),
    document.body,
  ].filter(Boolean);

  const rootSet = new Set(roots);
  for (const root of roots) {
    root.querySelectorAll('section, article, [role="region"]').forEach(el => {
      push(el, 'section');
    });
    // Direct block children — skip body itself dumping giant wrappers when
    // main already exists.
    if (root === document.body && rootSet.size > 1) continue;
    [...root.children].forEach(el => push(el, 'root-child'));
  }

  // Heading-anchored bands: climb from each h1/h2 to a reasonable block.
  const headings = [...document.querySelectorAll('h1, h2')].filter(isVisible);
  for (const h of headings) {
    let block = h.closest('section, article, [role="region"]');
    if (!block) {
      block = h.parentElement;
      for (let i = 0; i < 5 && block && block !== document.body; i++) {
        const r = block.getBoundingClientRect();
        if (r.height >= 160 && r.width >= MIN_W && r.height <= MAX_SECTION_H) break;
        block = block.parentElement;
      }
    }
    if (block && block !== document.body) push(block, 'heading-band');
  }

  candidates.sort((a, b) => a.box.y - b.box.y || b.box.height - a.box.height);
  const kept = [];
  for (const c of candidates) {
    const overlap = kept.find(k => {
      const ax1 = c.box.x, ay1 = c.box.y;
      const ax2 = ax1 + c.box.width, ay2 = ay1 + c.box.height;
      const bx1 = k.box.x, by1 = k.box.y;
      const bx2 = bx1 + k.box.width, by2 = by1 + k.box.height;
      const ix = Math.max(0, Math.min(ax2, bx2) - Math.max(ax1, bx1));
      const iy = Math.max(0, Math.min(ay2, by2) - Math.max(ay1, by1));
      const inter = ix * iy;
      const smaller = Math.min(c.box.width * c.box.height, k.box.width * k.box.height);
      return smaller > 0 && inter / smaller > 0.72;
    });
    if (!overlap) kept.push(c);
  }

  const pageHeight = Math.max(
    document.body.scrollHeight,
    document.documentElement.scrollHeight
  );
  const pageWidth = Math.max(
    document.body.scrollWidth,
    document.documentElement.scrollWidth,
    window.innerWidth
  );

  return { candidates: kept, pageHeight, pageWidth };
}
"""


class SectionDetector:
    """
    Detect semantic homepage sections from live DOM geometry + content.

    Classification uses landmarks (header/footer/nav), heading text, forms,
    structural signals (questions, big numbers, image density), and vertical
    position. CSS class names are never consulted.
    """

    def detect(self, page: Page) -> list[SectionHit]:
        raw = page.evaluate(_DETECT_CANDIDATES_JS)
        candidates: list[dict[str, Any]] = raw.get("candidates") or []
        page_height = float(raw.get("pageHeight") or 1)
        page_width = float(raw.get("pageWidth") or 1440)

        assigned: dict[str, SectionHit] = {}
        used_indices: set[int] = set()

        # 1) Landmarks first — Header / Footer
        for i, c in enumerate(candidates):
            tag, role = c.get("tag", ""), c.get("role", "")
            if tag == "header" or role == "banner" or (
                tag == "nav" and c["box"]["y"] < page_height * 0.15
            ):
                if "Header" not in assigned:
                    assigned["Header"] = self._to_hit("Header", c, page_width)
                    used_indices.add(i)
            if tag == "footer" or role == "contentinfo":
                if "Footer" not in assigned:
                    assigned["Footer"] = self._to_hit("Footer", c, page_width)
                    used_indices.add(i)

        # Fallback Header: compact top bar with navigation (not a tall hero).
        if "Header" not in assigned and candidates:
            for i, c in enumerate(candidates):
                if i in used_indices:
                    continue
                box = c["box"]
                if (
                    box["y"] < page_height * 0.12
                    and box["height"] <= 180
                    and (c.get("hasNav") or c.get("linkCount", 0) >= 3)
                ):
                    assigned["Header"] = self._to_hit("Header", c, page_width)
                    used_indices.add(i)
                    break

        if "Footer" not in assigned and candidates:
            bottom_i = None
            bottom_extent = -1.0
            for i, c in enumerate(candidates):
                if i in used_indices:
                    continue
                extent = c["box"]["y"] + c["box"]["height"]
                if extent > bottom_extent:
                    bottom_extent = extent
                    bottom_i = i
            if bottom_i is not None and bottom_extent > page_height * 0.75:
                assigned["Footer"] = self._to_hit(
                    "Footer", candidates[bottom_i], page_width,
                )
                used_indices.add(bottom_i)

        # 2) Hero — first substantial near-top content block (prefer headed, mid-height).
        hero_i, hero_score = None, -1.0
        for i, c in enumerate(candidates):
            if i in used_indices:
                continue
            box = c["box"]
            near_top = box["y"] < page_height * 0.35
            if not near_top:
                continue
            headings = c.get("headings") or []
            score = 0.0
            if headings:
                score += 3.0
            if 240 <= box["height"] <= 1200:
                score += 2.0
            elif box["height"] > 1200:
                score -= 1.0  # oversized wrappers are poor hero crops
            if c.get("buttons"):
                score += 1.0
            if c.get("images"):
                score += 1.0
            if score > hero_score:
                hero_score, hero_i = score, i
        if hero_i is not None and hero_score >= 3.0:
            assigned["Hero"] = self._to_hit("Hero", candidates[hero_i], page_width)
            used_indices.add(hero_i)

        # 3) Keyword + structural classification for remaining keys
        priority = [
            "FAQ", "Contact", "Testimonials", "Pricing", "Team",
            "Statistics", "Process", "BeforeAfter", "Gallery",
            "ServiceAreas", "Blog", "Partners", "Services",
            "Features", "About", "CTA",
        ]
        for key in priority:
            if key in assigned:
                continue
            best_i, best_score = None, 0.0
            for i, c in enumerate(candidates):
                if i in used_indices:
                    continue
                score = self._score(key, c, page_height)
                if score > best_score:
                    best_score, best_i = score, i
            if best_i is not None and best_score >= 2.0:
                assigned[key] = self._to_hit(key, candidates[best_i], page_width)
                used_indices.add(best_i)

        # Stable catalog order (unpack: folder_slug, report_key, display_name)
        ordered = [
            assigned[key] for _, key, _ in SECTION_CATALOG if key in assigned
        ]
        return ordered

    def _score(self, key: str, c: dict[str, Any], page_height: float) -> float:
        text = " ".join([
            " ".join(c.get("headings") or []),
            " ".join((c.get("paragraphs") or [])[:4]),
            (c.get("visibleText") or "")[:800],
        ])
        heading_blob = " ".join(c.get("headings") or [])
        score = 0.0

        pat = _PATTERNS.get(key)
        if pat:
            if pat.search(heading_blob):
                score += 4.0
            elif pat.search(text):
                score += 2.0

        if key == "FAQ":
            if c.get("questionMarks", 0) >= 3:
                score += 3.0
            if re.search(r"\bfaq\b", heading_blob, re.I):
                score += 2.0
        elif key == "Contact":
            if c.get("hasForm"):
                score += 3.5
            if re.search(r"@|phone|email|address", text, re.I):
                score += 1.0
        elif key == "Team":
            # Require explicit team language in the heading — avoid logo strips.
            if not (pat and pat.search(heading_blob)):
                score = min(score, 0.0)
            elif c.get("images", 0) >= 3 and len(c.get("headings") or []) >= 3:
                name_like = sum(
                    1 for h in (c.get("headings") or [])
                    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", h.strip())
                )
                if name_like >= 2:
                    score += 2.5
        elif key == "Features":
            if c.get("images", 0) >= 2 and len(c.get("headings") or []) >= 2:
                score += 1.0
            if re.search(
                r"\b(manag\w*|automat\w*|track\w*|organiz\w*|built\s+for|designed\s+for)\b",
                heading_blob,
                re.I,
            ):
                score += 2.5
            if heading_blob and 40 <= c["box"]["height"] <= 900:
                score += 0.5
        elif key == "Partners":
            if c.get("images", 0) >= 4 and len(text) < 700:
                score += 1.0
            # Require partner/cert language in a heading when possible.
            if pat and pat.search(heading_blob):
                score += 1.0
            elif not (pat and pat.search(text)):
                score = min(score, 0.5)
            else:
                # Body-only partner language is weak without logos / heading.
                if c.get("images", 0) < 3 and not heading_blob:
                    score = min(score, 1.5)
        elif key == "Testimonials":
            if re.search(r"[“\"'].{20,}[”\"']", text):
                score += 2.0
            if c.get("images", 0) >= 1 and len(c.get("paragraphs") or []) >= 2:
                score += 1.0
            if c["box"]["height"] > 1200:
                score -= 2.0
            # Quote marks alone are weak without a testimonials heading.
            if not (pat and pat.search(heading_blob)) and score < 4.0:
                score -= 1.5
        elif key == "Statistics":
            if c.get("bigNumbers", 0) >= 3 and len(text) < 1200:
                score += 3.5
        elif key == "Gallery":
            if c.get("images", 0) >= 4 and len(c.get("paragraphs") or []) <= 3:
                score += 3.0
        elif key == "Services":
            if c.get("listItems", 0) >= 4 or len(c.get("headings") or []) >= 3:
                score += 1.5
            if not (pat and pat.search(text)):
                score -= 0.5
        elif key == "Process":
            if re.search(r"\b(step\s*[1-9]|1\.|2\.|3\.)\b", text, re.I):
                score += 2.5
        elif key == "BeforeAfter":
            if re.search(r"before|after", text, re.I) and c.get("images", 0) >= 2:
                score += 2.5
        elif key == "CTA":
            btns = c.get("buttons") or []
            if btns and len(text) < 500:
                score += 1.5
            if any(_PATTERNS["CTA"].search(b or "") for b in btns):
                score += 2.5
            if c["box"]["height"] > 900:
                score -= 2.0
        elif key == "Pricing":
            if re.search(r"\$|€|£|\bper\s+(month|year)\b", text, re.I):
                score += 2.0
        elif key == "Blog":
            if c.get("images", 0) >= 2 and len(c.get("headings") or []) >= 3:
                score += 1.2
        elif key == "ServiceAreas":
            if re.search(r"\b[A-Z][a-z]+,\s*[A-Z]{2}\b", text):
                score += 1.5

        # Soft position priors
        y_ratio = c["box"]["y"] / max(page_height, 1)
        if key in ("CTA", "Contact") and y_ratio > 0.55:
            score += 0.4
        if key == "Hero" and y_ratio < 0.25:
            score += 0.4

        # Prefer compact bands over page-wrapping containers.
        if c["box"]["height"] > page_height * 0.45 and key not in ("Footer",):
            score -= 1.5

        return score

    def _to_hit(self, key: str, c: dict[str, Any], page_width: float) -> SectionHit:
        box = c["box"]
        return SectionHit(
            key=key,
            label=_DISPLAY_BY_KEY[key],
            bounding_box=[
                float(box["x"]),
                float(box["y"]),
                float(box["width"]),
                float(box["height"]),
            ],
            layout=self._infer_layout(c, page_width),
            images=int(c.get("images") or 0),
            buttons=len(c.get("buttons") or []),
            headings=len(c.get("headings") or []),
            paragraphs=len(c.get("paragraphs") or []),
            headings_text=list(c.get("headings") or []),
            paragraphs_text=list(c.get("paragraphs") or []),
            buttons_text=list(c.get("buttons") or []),
            visible_text=(c.get("visibleText") or "")[:5000],
            detected=True,
        )

    @staticmethod
    def _infer_layout(c: dict[str, Any], page_width: float) -> str:
        imgs = int(c.get("images") or 0)
        headings = len(c.get("headings") or [])
        paras = len(c.get("paragraphs") or [])
        w = float(c["box"]["width"])
        h = float(c["box"]["height"])

        if c.get("hasForm"):
            return "Form Block"
        if imgs >= 4 and paras <= 2:
            return "Image Grid"
        if imgs >= 1 and (headings + paras) >= 1 and w > page_width * 0.7:
            # Heuristic split — text+image band
            if h < 520 and imgs <= 2:
                return "Text Left + Image Right"
            return "Split Content + Media"
        if headings >= 3 and imgs >= 2:
            return "Card Grid"
        if int(c.get("bigNumbers") or 0) >= 3:
            return "Statistics Row"
        if len(c.get("buttons") or []) >= 1 and (headings + paras) <= 3 and h < 420:
            return "Centered CTA"
        if w > page_width * 0.85 and h < 140:
            return "Full-width Bar"
        return "Stacked Blocks"


# ── Writers ────────────────────────────────────────────────────────────────────


class LibraryWriter:
    """Persists homepage shot, per-section artifacts, and report.json."""

    def __init__(self, library_root: Path) -> None:
        self.library_root = Path(library_root)

    def prepare_site_dir(self, website_name: str) -> Path:
        out = self.library_root / website_name
        (out / "sections").mkdir(parents=True, exist_ok=True)
        return out

    def ensure_section_index_dirs(self) -> None:
        """Create Library/<section>/ folders for every catalog section."""
        for name in _SECTION_INDEX_NAMES:
            (self.library_root / name).mkdir(parents=True, exist_ok=True)

    def write_section(self, site_dir: Path, hit: SectionHit, page: Page) -> Path:
        folder = site_dir / "sections" / hit.folder_slug
        folder.mkdir(parents=True, exist_ok=True)

        meta = {
            "section": hit.label,
            "bounding_box": hit.bounding_box,
            "layout": hit.layout,
            "images": hit.images,
            "buttons": hit.buttons,
            "headings": hit.headings,
            "paragraphs": hit.paragraphs,
            "detected": True,
        }
        (folder / "metadata.json").write_text(
            json.dumps(meta, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        def _bullets(items: list[str]) -> list[str]:
            return [f"- {x}" for x in items] if items else ["- (none)"]

        content_lines = [
            f"# {hit.label}",
            "",
            "## Headings",
            *_bullets(hit.headings_text),
            "",
            "## Paragraphs",
            *_bullets(hit.paragraphs_text),
            "",
            "## Buttons",
            *_bullets(hit.buttons_text),
            "",
            "## Visible text",
            hit.visible_text or "(none)",
            "",
        ]
        (folder / "content.txt").write_text(
            "\n".join(content_lines), encoding="utf-8",
        )

        shot = folder / "screenshot.png"
        self._clip_screenshot(page, hit.bounding_box, shot)

        # Mirror into the cross-company section index:
        #   Library/hero/<Company>/…
        self._mirror_section_to_index(site_dir.name, hit.folder_slug, folder)
        return folder

    def mirror_company_to_section_index(self, website_name: str) -> int:
        """Copy one company's sections/ into Library/<section>/<Company>/."""
        site_dir = self.library_root / website_name
        sections_dir = site_dir / "sections"
        if not sections_dir.is_dir():
            return 0
        copied = 0
        for sec_dir in sorted(sections_dir.iterdir()):
            if not sec_dir.is_dir():
                continue
            if sec_dir.name not in _INDEX_BY_SLUG:
                continue
            self._mirror_section_to_index(website_name, sec_dir.name, sec_dir)
            copied += 1
        return copied

    def rebuild_section_index(self) -> dict[str, list[str]]:
        """
        Rebuild the cross-company index from every company folder::

            Library/hero/Neat/
            Library/hero/Mrhandyman/
            Library/services/…
        """
        self.ensure_section_index_dirs()
        index: dict[str, list[str]] = {name: [] for name in _SECTION_INDEX_NAMES}

        # Clear stale company mirrors under each section folder first.
        for name in _SECTION_INDEX_NAMES:
            sec_root = self.library_root / name
            for child in list(sec_root.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child)

        for company_dir in sorted(self.library_root.iterdir()):
            if not company_dir.is_dir():
                continue
            if company_dir.name in _SECTION_INDEX_NAMES:
                continue
            if not (company_dir / "sections").is_dir() and not (company_dir / "report.json").is_file():
                continue
            n = self.mirror_company_to_section_index(company_dir.name)
            if n:
                # Record which sections this company contributed.
                for sec_dir in (company_dir / "sections").iterdir():
                    if sec_dir.is_dir() and sec_dir.name in _INDEX_BY_SLUG:
                        index[_INDEX_BY_SLUG[sec_dir.name]].append(company_dir.name)

        return index

    def _mirror_section_to_index(
        self, website_name: str, folder_slug: str, source_dir: Path,
    ) -> Path:
        section_name = _INDEX_BY_SLUG[folder_slug]
        dest = self.library_root / section_name / website_name
        dest.mkdir(parents=True, exist_ok=True)
        for fname in _SECTION_ARTIFACTS:
            src = source_dir / fname
            if src.is_file():
                shutil.copy2(src, dest / fname)
        return dest

    def write_report(self, site_dir: Path, hits: list[SectionHit]) -> dict[str, Any]:
        present = {h.key for h in hits}
        report: dict[str, Any] = {}
        for key in _REPORT_KEYS:
            report[key] = key in present

        detected_n = sum(1 for v in report.values() if v is True)
        total = len(_REPORT_KEYS)
        report["OverallCompleteness"] = int(round(100 * detected_n / total)) if total else 0

        (site_dir / "report.json").write_text(
            json.dumps(report, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return report

    @staticmethod
    def _clip_screenshot(page: Page, box: list[float], dest: Path) -> None:
        x, y, w, h = box
        # Clamp to page bounds; Playwright clip uses viewport-absolute coords
        # for element screenshots via full_page clip.
        page_box = page.evaluate(
            """() => ({
                width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
                height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
            })"""
        )
        max_w = float(page_box["width"])
        max_h = float(page_box["height"])
        x = max(0.0, min(x, max_w - 1))
        y = max(0.0, min(y, max_h - 1))
        w = max(1.0, min(w, max_w - x))
        h = max(1.0, min(h, max_h - y))

        # Clip is relative to the full scrollable page when full_page=True.
        page.screenshot(
            path=str(dest),
            type="png",
            full_page=True,
            clip={"x": x, "y": y, "width": w, "height": h},
        )


# ── Report printer ─────────────────────────────────────────────────────────────


class ReportPrinter:
    """Pretty-prints the library extraction report to stdout."""

    WIDTH = 49

    def print(self, url: str, report: dict[str, Any]) -> None:
        line = "=" * self.WIDTH
        ok = self._mark(True)
        bad = self._mark(False)
        print()
        print(line)
        print("Library Extraction Report")
        print()
        print("Website:")
        print(url)
        print()
        for _, key, display in SECTION_CATALOG:
            mark = ok if report.get(key) else bad
            print(f"{display:<20} {mark}")
        print()
        print("Overall")
        print()
        print(f"{report.get('OverallCompleteness', 0)}%")
        print(line)
        print()

    @staticmethod
    def _mark(present: bool) -> str:
        preferred = "✓" if present else "✗"
        fallback = "[OK]" if present else "[X]"
        try:
            preferred.encode(getattr(sys.stdout, "encoding", None) or "utf-8")
            return preferred
        except (UnicodeEncodeError, LookupError):
            return fallback


# ── Main builder ───────────────────────────────────────────────────────────────


class LibraryBuilder:
    """
    Build a reusable visual design library for one website homepage.

    Pipeline
    --------
    1. Open URL with Playwright and wait for complete render
    2. Save full-page ``homepage.png``
    3. Detect semantic sections (no CSS class reliance)
    4. For each hit: ``screenshot.png`` + ``metadata.json`` + ``content.txt``
    5. Write ``report.json`` and print the extraction report
    """

    def __init__(
        self,
        library_root: Path | str | None = None,
        *,
        timeout_ms: int = 60_000,
        headless: bool = True,
    ) -> None:
        self.library_root = Path(library_root) if library_root else _DEFAULT_LIBRARY_ROOT
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.renderer = PageRenderer(timeout_ms=timeout_ms)
        self.detector = SectionDetector()
        self.writer = LibraryWriter(self.library_root)
        self.printer = ReportPrinter()

    # ── public API ─────────────────────────────────────────────────────────────

    def build(self, url: str, *, website_name: str | None = None) -> LibraryResult:
        """Run the full library extraction for *url*."""
        url = self._normalize_url(url)
        name = website_name or self.company_name_from_url(url)
        site_dir = self.writer.prepare_site_dir(name)

        print(f"[LibraryBuilder] Target : {url}")
        print(f"[LibraryBuilder] Output : {site_dir}")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                page = self.renderer.open(browser, url)

                # STEP 1 — full-page homepage screenshot
                homepage = site_dir / "homepage.png"
                page.screenshot(path=str(homepage), full_page=True, type="png")
                print(f"[LibraryBuilder] Saved homepage.png ({homepage.stat().st_size:,} bytes)")

                # STEP 2 — semantic section detection
                hits = self.detector.detect(page)
                print(f"[LibraryBuilder] Detected {len(hits)} section(s)")

                # STEP 3 — per-section artifacts
                for hit in hits:
                    self.writer.write_section(site_dir, hit, page)
                    print(f"  → {hit.folder_slug}/  ({hit.label})")

                # STEP 4 — report.json
                report = self.writer.write_report(site_dir, hits)

            finally:
                browser.close()

        # Keep Library/<section>/<Company>/ index in sync
        self.writer.ensure_section_index_dirs()

        # STEP 5 — print report
        self.printer.print(url, report)

        return LibraryResult(
            url=url,
            website_name=name,
            output_dir=site_dir,
            homepage_png=site_dir / "homepage.png",
            report=report,
            sections=hits,
        )

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def company_name_from_url(url: str) -> str:
        """Derive a folder-friendly company name from the host.

        ``https://www.neat.com`` → ``Neat``
        ``https://collegehunkshaulingjunk.com`` → ``Collegehunkshaulingjunk``
        """
        host = urlparse(url).hostname or url
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        label = host.split(".")[0] if host else "website"
        label = re.sub(r"[^a-z0-9]+", "", label)
        return label[:1].upper() + label[1:] if label else "Website"

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip()
        if not url:
            raise ValueError("URL must not be empty")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        return url


# ── CLI ────────────────────────────────────────────────────────────────────────


def _parse_websites_table(path: Path) -> list[str]:
    """Extract URLs from Library/Websites.txt markdown table."""
    if not path.is_file():
        return []
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.search(r"\[(https?://[^\]]+)\]", line)
        if m:
            urls.append(m.group(1))
    return urls


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    library_root = _DEFAULT_LIBRARY_ROOT

    if not argv:
        print(
            "Usage:\n"
            "  python webmaker/modules/library_builder.py <url>\n"
            "  python webmaker/modules/library_builder.py --all\n"
            "  python webmaker/modules/library_builder.py --from-list\n"
            "  python webmaker/modules/library_builder.py --index\n"
        )
        return 1

    builder = LibraryBuilder(library_root=library_root)

    if argv[0] == "--index":
        index = builder.writer.rebuild_section_index()
        print("[LibraryBuilder] Section index rebuilt:\n")
        for name, companies in index.items():
            print(f"  {name:<16} {len(companies):2d}  {', '.join(companies) or '—'}")
        return 0

    urls: list[str] = []
    if argv[0] in ("--all", "--from-list"):
        urls = _parse_websites_table(library_root / "Websites.txt")
        if not urls:
            print("No URLs found in Library/Websites.txt")
            return 1
    else:
        urls = [argv[0]]

    failures = 0
    for url in urls:
        try:
            builder.build(url)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[LibraryBuilder] FAILED {url}: {exc}", file=sys.stderr)

    # Refresh cross-company section folders after any build run.
    builder.writer.rebuild_section_index()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
