"""
webmaker.agents.website_modernizer.design_system
================================================
Premium design tokens for Agent 1: fonts, colors, SVG icons, site chrome.

Weak crawl brand colors (e.g. grey-only) are replaced with a professional
local-service palette so demos never look washed-out.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any


# Google Fonts — expressive but service-industry appropriate (not Inter/Roboto).
_FONT_DISPLAY = "Sora"
_FONT_BODY = "Source Sans 3"
_FONT_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Sora:wght@500;600;700;800&"
    "family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&"
    "display=swap"
)

# Fallback when crawl brand is weak / grey-only
_SERVICE_PALETTE = {
    "accent": "#e85d04",       # energetic CTA (junk/haulage energy)
    "accent_dark": "#c44d00",
    "ink": "#0f172a",
    "muted": "#475569",
    "surface": "#f8fafc",
    "surface_alt": "#eef2f7",
    "navy": "#0b1f33",
    "navy_mid": "#143049",
}


@dataclass
class DesignTokens:
    company_name: str = ""
    short_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    hours: str = ""
    logo_src: str = ""
    accent: str = _SERVICE_PALETTE["accent"]
    accent_dark: str = _SERVICE_PALETTE["accent_dark"]
    ink: str = _SERVICE_PALETTE["ink"]
    muted: str = _SERVICE_PALETTE["muted"]
    surface: str = _SERVICE_PALETTE["surface"]
    surface_alt: str = _SERVICE_PALETTE["surface_alt"]
    navy: str = _SERVICE_PALETTE["navy"]
    navy_mid: str = _SERVICE_PALETTE["navy_mid"]
    font_display: str = _FONT_DISPLAY
    font_body: str = _FONT_BODY
    font_url: str = _FONT_URL
    nav_items: list[dict[str, str]] = field(default_factory=list)
    tagline: str = ""


def load_design_tokens(
    package_dir: Path | None,
    *,
    theme_id: str = "kadence",
) -> DesignTokens:
    """Build tokens from website_package business/brand/navigation."""
    tokens = DesignTokens()
    business: dict[str, Any] = {}
    brand: dict[str, Any] = {}
    nav: dict[str, Any] = {}

    if package_dir and Path(package_dir).is_dir():
        business = _load_json(Path(package_dir) / "business.json")
        brand = _load_json(Path(package_dir) / "brand.json")
        nav = _load_json(Path(package_dir) / "navigation.json")

    raw_name = str(business.get("name") or "").strip()
    tokens.company_name = _clean_company_name(raw_name) or "Ihr Unternehmen"
    # Prefer brand from email/domain when crawl title is SEO spam without brand
    tokens.short_name = _short_brand(tokens.company_name, business)
    if tokens.short_name and tokens.short_name not in tokens.company_name:
        # Keep company_name as crawled when short brand is only a compact label
        pass

    phones = business.get("phones") or []
    emails = business.get("emails") or []
    addrs = business.get("addresses") or []
    hours = business.get("opening_hours") or []
    tokens.phone = _best_phone(phones)
    tokens.email = str(emails[0]) if emails else ""
    tokens.address = _best_address(addrs)
    tokens.hours = _best_hours(hours)
    tokens.tagline = "Schnell · Zuverlässig · Festpreis"

    logo = business.get("logo") or {}
    if isinstance(logo, dict):
        tokens.logo_src = str(
            logo.get("source_url") or logo.get("local_path") or ""
        )

    # Prefer a cropped navbar mark when available (full lockups are unreadable at small size)
    if package_dir:
        images_dir = Path(package_dir).parent / "images"
        icon = images_dir / "logo-icon.png"
        if icon.is_file():
            from webmaker.agents.website_modernizer.image_bank import publish_local_for_wp
            published = publish_local_for_wp(icon)
            if published:
                tokens.logo_src = published

    # Colors — reject weak greys from crawl
    primary = ""
    colors = brand.get("primary_colors") or []
    if colors:
        primary = str(colors[0])
    if primary and _is_usable_brand_color(primary):
        tokens.accent = primary
        tokens.accent_dark = _darken(primary, 0.85)
    # else keep service palette

    tokens.nav_items = _build_nav(nav)
    return tokens


def css_variables(tokens: DesignTokens) -> str:
    """CSS custom properties + Google Fonts import for page HTML."""
    return f"""@import url('{tokens.font_url}');
:root{{
  --wm-font-display:'{tokens.font_display}',system-ui,sans-serif;
  --wm-font-body:'{tokens.font_body}',system-ui,sans-serif;
  --wm-accent:{tokens.accent};
  --wm-accent-dark:{tokens.accent_dark};
  --wm-ink:{tokens.ink};
  --wm-muted:{tokens.muted};
  --wm-surface:{tokens.surface};
  --wm-surface-alt:{tokens.surface_alt};
  --wm-navy:{tokens.navy};
  --wm-navy-mid:{tokens.navy_mid};
  --wm-radius:16px;
  --wm-radius-sm:12px;
  --wm-radius-lg:18px;
  --wm-shadow:0 16px 42px rgba(15,23,42,.11);
  --wm-shadow-card:0 10px 28px rgba(15,23,42,.08);
  --wm-shadow-soft:0 6px 18px rgba(15,23,42,.05);
  --wm-ease:.28s cubic-bezier(.22,1,.36,1);
  --wm-space-section:clamp(2.5rem,4.2vw,3.35rem);
  --wm-space-head:1.55rem;
  --wm-img-grade:saturate(1.06) brightness(1.02) contrast(1.03) sepia(.06);
}}
body,.wm3-section,.wm3-hero-overlay,.wm3-site{{
  font-family:var(--wm-font-body);
  color:var(--wm-ink);
}}
h1,h2,h3,h4,.wm3-hero-card__title,.wm3-section-title,
.wm3-brand,.wm3-step__title,.wm3-icon-col__title{{
  font-family:var(--wm-font-display);
  letter-spacing:-0.02em;
}}
p{{line-height:1.65}}
a{{transition:color var(--wm-ease),opacity var(--wm-ease)}}
@media (prefers-reduced-motion:reduce){{
  *,*::before,*::after{{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
}}
"""


def premium_polish_css() -> str:
    """Creative-Director final polish — additive override layer.

    Refinement only (no redesign): breathing room, unified radius/shadow/icon
    system, softer elevation, subtle background depth. Loaded last so it
    overrides base layout without editing content, structure, or type scale.
    """
    return r"""
/* ══════════════════════════════════════════════════════════════════
   PREMIUM POLISH — final 5% (spacing · hierarchy · elevation · calm)
   Additive. No content / structure / type-scale changes.
   ══════════════════════════════════════════════════════════════════ */

:root{
  --wm-space-section:clamp(3.6rem,6vw,5rem);
  --wm-space-head:2.25rem;
  --wm-radius:18px;
  --wm-radius-sm:14px;
  --wm-radius-lg:22px;
  --wm-shadow:0 18px 48px rgba(15,23,42,.07);
  --wm-shadow-card:0 12px 36px rgba(15,23,42,.055);
  --wm-shadow-soft:0 8px 24px rgba(15,23,42,.04);
  --wm-shadow-hover:0 16px 40px rgba(15,23,42,.08);
  --wm-shadow-lift:0 20px 48px rgba(15,23,42,.1);
  --wm-ease:.2s cubic-bezier(.22,1,.36,1);
  --wm-hairline:rgba(15,23,42,.045);
  --wm-icon-size:1.35rem;
}

/* Global section breathing + card system */
.wm3-section{
  padding-top:var(--wm-space-section)!important;
  padding-bottom:var(--wm-space-section)!important;
}
.wm3-svc-card,
.wm3-process__card,
.wm3-trust-live__card,
.mba-card,
.mba-trust__item,
.wm3-warum__panel{
  border-radius:var(--wm-radius)!important;
  transition:transform var(--wm-ease),box-shadow var(--wm-ease)!important;
}
.wm3-svc-card:hover,
.wm3-process__card:hover,
.wm3-trust-live__card:hover,
.mba-card:hover,
.mba-trust__item:hover{
  transform:translateY(-5px)!important;
  box-shadow:var(--wm-shadow-hover)!important;
}
.wm3-ico{
  width:var(--wm-icon-size)!important;
  height:var(--wm-icon-size)!important;
}
.wm3-ico *,.mba-trust__svg *{
  stroke-width:1.7px!important;
  stroke-linecap:round!important;
  stroke-linejoin:round!important;
}

/* Head rhythm: label → heading → paragraph */
.wm3-svc-photo__eyebrow,.wm3-process__label,.wm3-ba__label,.wm3-warum__eyebrow,
.mba-eyebrow,.wm3-trust-live__eyebrow,.mba-promises__eyebrow{
  margin-bottom:.9rem!important;
}
.wm3-svc-photo__title,.wm3-process__title,.wm3-trust-live__title,
.wm3-warum__title,.mba-title,.mba-promises__title{
  margin-bottom:1.05rem!important;
}

/* ── 1. HERO ── */
.wm3-hero-overlay,.wm3-hero-bleed{
  min-height:min(92vh,780px)!important;
  padding:clamp(5.2rem,8vw,6.5rem) 1.5rem!important;
}
.wm3-hero-bleed__shade,.wm3-hero-overlay__shade{
  background:linear-gradient(to right,rgba(4,18,36,.38) 0%,rgba(4,18,36,.24) 36%,rgba(4,18,36,.08) 64%,transparent 100%)!important;
}
.wm3-hero-bleed__vignette{
  background:linear-gradient(to top,rgba(2,8,20,.2) 0%,transparent 100%)!important;
}
.wm3-hero-bleed__title{
  margin:0 0 1.7rem!important;
}
.wm3-hero-bleed__sub{
  margin:0 0 2.95rem!important;
  line-height:1.6!important;
  max-width:38ch!important;
}
.wm3-hero-bleed__actions{
  gap:1.15rem!important;
  margin:0 0 2.45rem!important;
}
.wm3-hero-bleed__badges{
  gap:.7rem .8rem!important;
}
.wm3-hero-bleed__badges li{
  padding:.78rem 1.2rem!important;
  border-radius:12px!important;
  gap:.55rem!important;
}

/* ── 2. TRUST ── */
.wm3-trust-live{
  padding:clamp(4.2rem,6.8vw,5.8rem) 0 clamp(3.8rem,6vw,5.2rem)!important;
  background-color:#fff!important;
}
.wm3-trust-live__pattern{opacity:.65!important}
.wm3-trust-live__wash{
  background:linear-gradient(180deg,rgba(255,255,255,.42) 0%,rgba(255,255,255,.18) 45%,rgba(255,255,255,.48) 100%)!important;
}
.wm3-trust-live__head{margin-bottom:clamp(2.6rem,4.2vw,3.4rem)!important}
.wm3-trust-live__eyebrow::after{display:none!important}
.wm3-trust-live__title{font-weight:700!important;margin-bottom:1.1rem!important}
.wm3-trust-live__sub{color:#64748b!important;font-weight:400!important}
.wm3-trust-live__row{
  gap:clamp(.7rem,1.2vw,.95rem)!important;
  align-items:stretch!important;
}
.wm3-trust-live__support{
  gap:clamp(.7rem,1.2vw,.95rem)!important;
  align-items:stretch!important;
}
.wm3-trust-live__card{
  display:flex!important;
  flex-direction:column!important;
  align-items:center!important;
  justify-content:center!important;
  text-align:center!important;
  height:100%!important;
  box-sizing:border-box!important;
  padding:clamp(1.7rem,2.3vw,2.05rem) clamp(1rem,1.4vw,1.25rem) clamp(1.75rem,2.3vw,2.1rem)!important;
  min-height:0!important;
  border:none!important;
  border-radius:var(--wm-radius-lg)!important;
  background:#FCFCFA!important;
  background-image:none!important;
  box-shadow:var(--wm-shadow-soft)!important;
  backdrop-filter:none!important;
}
.wm3-trust-live__card--hero{
  background:#FFE8D6!important;
  padding:clamp(1.9rem,2.5vw,2.35rem) 1.25rem clamp(1.95rem,2.5vw,2.4rem)!important;
  box-shadow:0 14px 40px rgba(232,93,4,.12),0 6px 18px rgba(15,23,42,.04)!important;
  transform:none!important;
  z-index:2;
}
.wm3-trust-live__card--hero:hover{
  transform:translateY(-5px)!important;
  box-shadow:0 18px 48px rgba(232,93,4,.15),0 8px 22px rgba(15,23,42,.05)!important;
}
.wm3-trust-live__card--t1,
.wm3-trust-live__card--t2,
.wm3-trust-live__card--t3,
.wm3-trust-live__card--t4,
.wm3-trust-live__card--t5{background:#FCFCFA!important}
.wm3-trust-live__icon{
  width:2.75rem!important;height:2.75rem!important;
  margin:0 auto 1.25rem!important;
  background:rgba(232,93,4,.09)!important;
  box-shadow:none!important;
  color:var(--wm-accent)!important;
  flex-shrink:0!important;
}
.wm3-trust-live__stat-icon{
  width:2.85rem!important;height:2.85rem!important;
  margin:0 auto 1rem!important;
  background:#fff!important;
  box-shadow:0 6px 16px rgba(232,93,4,.14)!important;
}
.wm3-trust-live__value{
  color:var(--wm-navy)!important;font-weight:700!important;
  margin:0 0 .7rem!important;
  font-size:clamp(.84rem,1.05vw,.95rem)!important;
  line-height:1.3!important;
}
.wm3-trust-live__label{
  color:#64748b!important;font-weight:400!important;
  font-size:clamp(.76rem,.95vw,.84rem)!important;
  line-height:1.45!important;
  margin:0!important;
}
.wm3-trust-live__stat-num{
  color:var(--wm-accent)!important;text-shadow:none!important;
  font-size:clamp(2.65rem,4.2vw,3.45rem)!important;
  margin:0 0 .45rem!important;
  line-height:1!important;
}
.wm3-trust-live__stat-label{
  color:var(--wm-navy)!important;
  margin:0!important;
  line-height:1.3!important;
}
.wm3-trust-live__divider{margin:1.15rem auto 1.05rem!important}
.wm3-trust-live__stat-note{
  color:#64748b!important;
  line-height:1.5!important;
  margin:0!important;
}

/* ── 3. LEISTUNGEN ── */
.wm3-svc-photo{
  background-color:#FBFBF9!important;
  background-image:repeating-linear-gradient(135deg,rgba(15,23,42,.02) 0,rgba(15,23,42,.02) 1px,transparent 1px,transparent 26px)!important;
  padding:clamp(3.6rem,6vw,5rem) 0!important;
}
.wm3-svc-photo__head{margin-bottom:clamp(2.4rem,3.8vw,3rem)!important}
.wm3-svc-photo__grid{gap:clamp(1.6rem,2.4vw,2.15rem)!important}
.wm3-svc-card{
  border:1px solid var(--wm-hairline)!important;
  box-shadow:var(--wm-shadow-soft)!important;
  overflow:hidden!important;
}
.wm3-svc-card:hover{
  box-shadow:var(--wm-shadow-hover)!important;
}
.wm3-svc-card__media{
  aspect-ratio:16/10!important;
  overflow:hidden!important;
}
.wm3-svc-card__img{
  width:100%!important;height:100%!important;
  object-fit:cover!important;object-position:center top!important;
}
.wm3-svc-card__body{
  padding:clamp(1.35rem,2vw,1.65rem) clamp(1.25rem,1.8vw,1.55rem) clamp(1.4rem,2vw,1.7rem)!important;
}
.wm3-svc-card__icon{
  width:2.7rem!important;height:2.7rem!important;
  margin-bottom:.85rem!important;
}

/* ── 4. 3 SCHRITTE ── */
.wm3-process{
  padding:clamp(3.8rem,6.2vw,5.2rem) 0!important;
  background-color:#fff!important;
  background-image:repeating-linear-gradient(135deg,rgba(15,23,42,.016) 0,rgba(15,23,42,.016) 1px,transparent 1px,transparent 30px)!important;
}
.wm3-process__head{margin-bottom:clamp(2.3rem,3.6vw,2.9rem)!important}
.wm3-process__grid{gap:clamp(1.5rem,2.4vw,2rem)!important}
.wm3-process__card{
  border:1px solid var(--wm-hairline)!important;
  box-shadow:var(--wm-shadow-card)!important;
  padding:clamp(1.45rem,2.2vw,1.85rem) clamp(1.25rem,2vw,1.6rem)!important;
}
.wm3-process__num{margin-bottom:1.15rem!important}
.wm3-process__visual{margin-bottom:1.15rem!important}

/* Process card entrance — left ← · middle ↑ · right → · 3.5s total sequence */
.wm3-process--js:not(.is-inview) .wm3-process__card{
  opacity:0;
  pointer-events:none;
  transition:none!important;
  will-change:transform,opacity,filter;
}
.wm3-process--js:not(.is-inview) .wm3-process__card:nth-child(1){
  transform:translate3d(-4.75rem,.35rem,0) rotate(-2.2deg) scale(.94);
  filter:blur(2.5px);
}
.wm3-process--js:not(.is-inview) .wm3-process__card:nth-child(2){
  transform:translate3d(0,4.5rem,0) scale(.94);
  filter:blur(2.5px);
}
.wm3-process--js:not(.is-inview) .wm3-process__card:nth-child(3){
  transform:translate3d(4.75rem,.35rem,0) rotate(2.2deg) scale(.94);
  filter:blur(2.5px);
}
.wm3-process.is-inview .wm3-process__card:nth-child(1){
  animation:wm3-proc-in-left 1.75s cubic-bezier(.16,1,.3,1) 0s both;
}
.wm3-process.is-inview .wm3-process__card:nth-child(2){
  animation:wm3-proc-in-mid 1.75s cubic-bezier(.16,1,.3,1) .875s both;
}
.wm3-process.is-inview .wm3-process__card:nth-child(3){
  animation:wm3-proc-in-right 1.75s cubic-bezier(.16,1,.3,1) 1.75s both;
}
@keyframes wm3-proc-in-left{
  0%{opacity:0;transform:translate3d(-4.75rem,.35rem,0) rotate(-2.2deg) scale(.94);filter:blur(2.5px)}
  72%{opacity:1;filter:blur(0)}
  86%{transform:translate3d(.28rem,-.12rem,0) rotate(.35deg) scale(1.015)}
  100%{opacity:1;transform:none;filter:none}
}
@keyframes wm3-proc-in-mid{
  0%{opacity:0;transform:translate3d(0,4.5rem,0) scale(.94);filter:blur(2.5px)}
  72%{opacity:1;filter:blur(0)}
  86%{transform:translate3d(0,-.28rem,0) scale(1.015)}
  100%{opacity:1;transform:none;filter:none}
}
@keyframes wm3-proc-in-right{
  0%{opacity:0;transform:translate3d(4.75rem,.35rem,0) rotate(2.2deg) scale(.94);filter:blur(2.5px)}
  72%{opacity:1;filter:blur(0)}
  86%{transform:translate3d(-.28rem,-.12rem,0) rotate(-.35deg) scale(1.015)}
  100%{opacity:1;transform:none;filter:none}
}
@media(prefers-reduced-motion:reduce){
  .wm3-process--js:not(.is-inview) .wm3-process__card,
  .wm3-process.is-inview .wm3-process__card{
    opacity:1!important;transform:none!important;filter:none!important;
    animation:none!important;pointer-events:auto!important;
  }
}

/* ── 5. BEFORE / AFTER ── */
.wm3-mba{
  background-color:#fff!important;
  background-image:url('/wp-content/uploads/webmaker/mba-texture.png')!important;
  background-repeat:repeat!important;
  background-size:130px 118px!important;
  background-position:center top!important;
  padding:clamp(4rem,6.5vw,5.4rem) 0 clamp(3.2rem,5vw,4rem)!important;
}
.mba-head{margin-bottom:clamp(2.3rem,3.8vw,3rem)!important;padding-top:.35rem!important}
.mba-grid{gap:clamp(1.5rem,2.4vw,2rem)!important}
.mba-card{
  border:none!important;
  border-radius:var(--wm-radius)!important;
  box-shadow:var(--wm-shadow-card)!important;
  overflow:hidden!important;
}
.mba-card:hover,.mba-card:focus-visible,.mba-card.mba-active,.mba-card.mba-open{
  border:none!important;
  box-shadow:var(--wm-shadow-lift)!important;
}
.mba-media{border-radius:0!important}

/* ── 6. GARANTIEN ── */
.mba-promises{
  padding-top:clamp(3.6rem,5.8vw,4.8rem)!important;
  padding-bottom:clamp(3.6rem,5.8vw,4.8rem)!important;
}
.mba-promises__head{margin-bottom:clamp(2.1rem,3.4vw,2.75rem)!important}
.mba-trust{gap:clamp(1.25rem,2vw,1.6rem)!important}
.mba-trust__item{
  padding:clamp(1.4rem,2vw,1.7rem) clamp(1.25rem,1.8vw,1.5rem)!important;
  border-radius:var(--wm-radius)!important;
  box-shadow:0 12px 36px rgba(0,0,0,.18)!important;
  border-color:rgba(255,255,255,.4)!important;
}
.mba-trust__item:hover{
  box-shadow:0 18px 44px rgba(0,0,0,.24)!important;
}
.mba-trust__mark{
  width:2.85rem!important;height:2.85rem!important;
  margin:0 0 1.05rem!important;
}
.mba-trust__svg{
  width:var(--wm-icon-size)!important;
  height:var(--wm-icon-size)!important;
}
.mba-trust__title{margin:0 0 .65rem!important}

/* ── 7. WARUM UNS ── */
.wm3-features,.wm3-warum{
  background-color:#FCFCFA!important;
  background-image:repeating-linear-gradient(135deg,rgba(15,23,42,.018) 0,rgba(15,23,42,.018) 1px,transparent 1px,transparent 28px)!important;
  padding-top:clamp(3.6rem,6vw,5rem)!important;
  padding-bottom:clamp(3.6rem,6vw,5rem)!important;
}
.wm3-warum__panel{
  padding:clamp(2.1rem,3.6vw,3rem) clamp(1.55rem,3vw,2.35rem)!important;
  box-shadow:var(--wm-shadow-soft)!important;
}
.wm3-warum__frame{
  grid-template-columns:minmax(0,.88fr) minmax(0,1.22fr)!important;
  gap:clamp(1.5rem,3vw,2.5rem)!important;
}
.wm3-warum__grid{
  grid-template-columns:repeat(4,minmax(0,1fr))!important;
  column-gap:.55rem!important;
  row-gap:1.35rem!important;
}
.wm3-warum__col{
  min-width:0;
  padding:0 .65rem!important;
}
.wm3-warum__icon{
  width:2.75rem!important;height:2.75rem!important;
  margin-bottom:.95rem!important;
}
.wm3-warum__col-title{
  margin-bottom:.55rem!important;
  hyphens:none;
  -webkit-hyphens:none;
  overflow-wrap:normal;
  word-break:keep-all;
}

/* ── 8. FOOTER ── */
.wm3-footer{
  padding-top:clamp(3rem,4.5vw,3.8rem)!important;
}
.wm3-footer__grid{
  gap:2.75rem 3.15rem!important;
  padding-bottom:3rem!important;
}
.wm3-footer__lockup{margin:0 0 1.25rem!important}
.wm3-footer__slogan{margin:0 0 1rem!important;line-height:1.45!important}
.wm3-footer__about{margin:0 0 1.55rem!important;line-height:1.8!important}
.wm3-footer__usps{gap:.7rem .45rem!important}
.wm3-footer__heading{margin:0 0 1.35rem!important}
.wm3-footer__menu a{
  padding:1.1rem 0!important;
  line-height:1.45!important;
}
.wm3-footer__clist li{
  margin:0 0 1.35rem!important;
  gap:1rem!important;
  align-items:center!important;
  line-height:1.55!important;
}
.wm3-footer__clist a{
  align-items:center!important;
  gap:1rem!important;
  line-height:1.55!important;
}
.wm3-footer__cicon{
  width:36px!important;height:36px!important;
  display:inline-flex!important;
  align-items:center!important;justify-content:center!important;
  flex-shrink:0!important;
}
.wm3-footer__bar{
  padding:1.25rem 0 1.55rem!important;
  line-height:1.5!important;
}

@media(max-width:1100px){
  .wm3-trust-live__card--hero{
    max-width:20rem;
    margin:0 auto;
  }
}
@media(prefers-reduced-motion:reduce){
  .wm3-svc-card:hover,
  .wm3-process__card:hover,
  .wm3-trust-live__card:hover,
  .mba-card:hover,
  .mba-trust__item:hover,
  .wm3-trust-live__card--hero:hover{
    transform:none!important;
  }
}
"""



def site_header_html(tokens: DesignTokens) -> str:
    """Premium sticky header: in-view info bar + logo/nav/CTA."""
    nav_links = []
    for item in tokens.nav_items:
        label = item.get("label") or ""
        href = item.get("href") or "#"
        if not label:
            continue
        nav_links.append(
            f'<a class="wm3-nav__link" href="{escape(href, quote=True)}">{escape(label)}</a>'
        )

    logo = ""
    if tokens.logo_src:
        logo = (
            f'<img class="wm3-brand__logo" src="{escape(tokens.logo_src, quote=True)}" '
            f'alt="{escape(tokens.short_name)}" />'
        )
    else:
        logo = '<span class="wm3-brand__mark" aria-hidden="true">' + escape(
            (tokens.short_name or "U")[:1].upper()
        ) + "</span>"

    # Split brand for stronger hierarchy: two uppercase wordmark lines
    brand_name = tokens.short_name or tokens.company_name or "Ihr Unternehmen"
    brand_main, brand_sub = brand_name, ""
    if " " in brand_name:
        parts = brand_name.split(" ", 1)
        brand_main, brand_sub = parts[0], parts[1]
    brand = (
        f'<a class="wm3-brand" href="/">'
        f"{logo}"
        f'<span class="wm3-brand__stack">'
        f'<span class="wm3-brand__name">{escape(brand_main.upper())}</span>'
        f'<span class="wm3-brand__sub">{escape(brand_sub.upper())}</span>'
        f"</span></a>"
    )

    tel = ""
    if tokens.phone:
        tel = "tel:" + re.sub(r"[^\d+]", "", tokens.phone)

    hours_raw = _clean_hours(tokens.hours) or "Mo–Fr 8–20 Uhr"
    # Compact for a 4-item in-view bar
    hours_raw = re.sub(
        r"Montag\s+bis\s+Freitag\s+von\s+",
        "Mo–Fr ",
        hours_raw,
        flags=re.I,
    )
    hours = escape(hours_raw)

    items: list[str] = [
        f'<span class="wm3-topbar__item">'
        f'<span class="wm3-topbar__ico">{_icon("clock", 15)}</span>'
        f'<span class="wm3-topbar__meta">'
        f'<span class="wm3-topbar__label">Öffnungszeiten</span>'
        f'<span class="wm3-topbar__value">{hours}</span>'
        f"</span></span>"
    ]
    if tokens.email:
        items.append(
            f'<a class="wm3-topbar__item" href="mailto:{escape(tokens.email, quote=True)}">'
            f'<span class="wm3-topbar__ico">{_icon("mail", 15)}</span>'
            f'<span class="wm3-topbar__meta">'
            f'<span class="wm3-topbar__label">E-Mail</span>'
            f'<span class="wm3-topbar__value">{escape(tokens.email)}</span>'
            f"</span></a>"
        )
    if tokens.phone:
        items.append(
            f'<a class="wm3-topbar__item wm3-topbar__item--phone" '
            f'href="{escape(tel, quote=True)}">'
            f'<span class="wm3-topbar__ico">{_icon("phone", 15)}</span>'
            f'<span class="wm3-topbar__meta">'
            f'<span class="wm3-topbar__label">Telefon</span>'
            f'<span class="wm3-topbar__value">{escape(tokens.phone)}</span>'
            f"</span></a>"
        )
    if tokens.tagline:
        items.append(
            f'<span class="wm3-topbar__item wm3-topbar__item--tag">'
            f'<span class="wm3-topbar__meta">'
            f'<span class="wm3-topbar__label">Service</span>'
            f'<span class="wm3-topbar__value">{escape(tokens.tagline)}</span>'
            f"</span></span>"
        )

    sep = '<span class="wm3-topbar__sep" aria-hidden="true"></span>'
    row = sep.join(items)

    html = f"""
<header class="wm3-site-header">
  <div class="wm3-topbar">
    <div class="wm3-topbar__rail" aria-hidden="true"></div>
    <div class="wm3-topbar__sheen" aria-hidden="true"></div>
    <div class="wm3-topbar__inner">
      <div class="wm3-topbar__row">{row}</div>
    </div>
  </div>
  <div class="wm3-navbar">
    <div class="wm3-navbar__inner">
      {brand}
      <nav class="wm3-nav" aria-label="Hauptnavigation">
        {''.join(nav_links)}
      </nav>
    </div>
  </div>
</header>
"""
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def site_footer_html(tokens: DesignTokens) -> str:
    """Premium 3-column footer for the local demo site."""
    phone = (tokens.phone or "").strip()
    email = (tokens.email or "").strip()
    address = (tokens.address or "").strip()
    hours = (tokens.hours or "Montag bis Freitag von 8–20 Uhr").strip()
    company = (tokens.company_name or tokens.short_name or "Ihr Unternehmen").strip()
    brand_name = (tokens.short_name or company).strip()
    if " " in brand_name:
        brand_main, brand_sub = brand_name.split(" ", 1)
    else:
        brand_main, brand_sub = brand_name, ""

    tel_href = "tel:" + re.sub(r"[^\d+]", "", phone)
    logo = ""
    if tokens.logo_src:
        logo = (
            f'<img class="wm3-footer__logo-img" src="{escape(tokens.logo_src, quote=True)}" '
            f'alt="{escape(tokens.short_name or tokens.company_name or "Logo")} Logo" '
            f'width="52" height="52" loading="lazy" />'
        )
    else:
        logo = (
            '<span class="wm3-footer__logo-mark" aria-hidden="true">'
            + escape((brand_main or "U")[:1].upper())
            + "</span>"
        )

    # Mockup nav order (exactly)
    footer_nav = [
        {"label": "Startseite", "href": "/"},
        {"label": "Leistungen", "href": "/services/"},
        {"label": "Kontakt", "href": "/contact/"},
        {"label": "Bewertungen", "href": "/about/"},
    ]
    # Prefer matching labels from tokens when available
    by_label = {
        (it.get("label") or "").strip().lower(): it
        for it in (tokens.nav_items or [])
        if isinstance(it, dict)
    }
    nav_lis: list[str] = []
    for item in footer_nav:
        src = by_label.get(item["label"].lower()) or item
        href = (src.get("href") or item["href"]).strip() or item["href"]
        label = item["label"]
        nav_lis.append(
            f'<li><a href="{escape(href, quote=True)}"><span>{escape(label)}</span>'
            f'<span class="wm3-footer__chev" aria-hidden="true">{_icon("chevron", 14)}</span>'
            f"</a></li>"
        )

    contact_lis = [
        (
            f'<li><a href="{escape(tel_href, quote=True)}">'
            f'<span class="wm3-footer__cicon" aria-hidden="true">{_icon("phone", 16)}</span>'
            f"<span>{escape(phone)}</span></a></li>"
        ),
        (
            f'<li><a href="mailto:{escape(email, quote=True)}">'
            f'<span class="wm3-footer__cicon" aria-hidden="true">{_icon("mail", 16)}</span>'
            f"<span>{escape(email)}</span></a></li>"
        ),
        (
            f'<li><span class="wm3-footer__cicon" aria-hidden="true">{_icon("pin", 16)}</span>'
            f"<span>{escape(address)}</span></li>"
        ),
        (
            f'<li><span class="wm3-footer__cicon" aria-hidden="true">{_icon("clock", 16)}</span>'
            f"<span>{escape(hours)}</span></li>"
        ),
    ]

    usps = [
        ("shield_check", "Festpreisgarantie"),
        ("calendar", "Termintreu"),
        ("leaf", "Umweltgerecht"),
        ("user", "Erfahrenes Team"),
    ]
    usp_html = "".join(
        f'<div class="wm3-footer__usp">'
        f'<div class="wm3-footer__usp-icon" aria-hidden="true">{_icon(name, 20)}</div>'
        f'<span>{escape(label)}</span></div>'
        for name, label in usps
    )

    html = f"""
<footer class="wm3-site-footer">
  <div class="wm3-footer__inner">
    <div class="wm3-footer__grid">
      <div class="wm3-footer__brand">
        <div class="wm3-footer__lockup">
          {logo}
          <div class="wm3-footer__wordmark">
            <span class="wm3-footer__brand-top">{escape(brand_main.upper())}</span>
            <span class="wm3-footer__brand-sub">{escape((brand_sub or company).upper())}</span>
          </div>
        </div>
        <p class="wm3-footer__slogan">{escape(tokens.tagline or "Platz schaffen. Sorgen nehmen.")}</p>
        <p class="wm3-footer__about">{escape(company)} — Ihr zuverlässiger Partner vor Ort. Schnell, diskret und zum fairen Festpreis.</p>
        <div class="wm3-footer__usps" role="list">{usp_html}</div>
      </div>
      <nav class="wm3-footer__nav" aria-label="Fußzeilen-Navigation">
        <div class="wm3-footer__heading">Navigation</div>
        <ul class="wm3-footer__menu">{''.join(nav_lis)}</ul>
      </nav>
      <div class="wm3-footer__contact">
        <div class="wm3-footer__heading">Kontakt</div>
        <ul class="wm3-footer__clist">{''.join(contact_lis)}</ul>
      </div>
    </div>
    <div class="wm3-footer__bar">
      <span>© 2026 {escape(company)}. Alle Rechte vorbehalten.</span>
      <span class="wm3-footer__legal">
        <a href="/impressum/">Impressum</a>
        <span aria-hidden="true">|</span>
        <a href="/datenschutz/">Datenschutz</a>
      </span>
    </div>
  </div>
</footer>
"""
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def chrome_css(tokens: DesignTokens) -> str:
    """Header/footer CSS using design tokens."""
    return f"""
/* Site chrome */
.wm3-site-header{{position:sticky;top:0;z-index:50;font-family:var(--wm-font-body)}}

/* Premium info bar — 4 items always in view, gentle in-bounds motion */
.wm3-topbar{{
  position:relative;overflow:hidden;
  background:linear-gradient(105deg,#1b3a52 0%,#244a66 48%,#1f4058 100%);
  color:rgba(255,255,255,.92);
  border-bottom:1px solid rgba(255,255,255,.08);
}}
.wm3-topbar__sheen{{
  position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(105deg,rgba(255,255,255,.07) 0%,transparent 42%,rgba(255,255,255,.04) 100%);
  animation:wm3-sheen 7s ease-in-out infinite;
}}
.wm3-topbar__rail{{
  position:absolute;left:0;right:0;bottom:0;height:2px;pointer-events:none;
  background:linear-gradient(90deg,transparent 0%,{tokens.accent} 30%,#ffb703 50%,{tokens.accent} 70%,transparent 100%);
  background-size:42% 100%;background-repeat:no-repeat;
  animation:wm3-rail 3.4s linear infinite;opacity:.95;
}}
.wm3-topbar__inner{{
  max-width:1140px;margin:0 auto;padding:.55rem 1.25rem;
  overflow:hidden;
}}
.wm3-topbar__row{{
  display:flex;align-items:center;justify-content:space-between;gap:.85rem;
  width:100%;min-width:0;
  animation:wm3-drift 9s ease-in-out infinite;
}}
.wm3-topbar__item{{
  display:inline-flex;align-items:center;gap:.55rem;min-width:0;
  color:inherit;text-decoration:none;flex:1 1 0;
  padding:.15rem .85rem;
}}
.wm3-topbar__item:hover .wm3-topbar__value{{color:#fff}}
.wm3-topbar__ico{{
  flex-shrink:0;width:28px;height:28px;border-radius:8px;
  display:inline-flex;align-items:center;justify-content:center;
  background:rgba(255,255,255,.1);color:#ffd08a;
}}
.wm3-topbar__ico .wm3-ico{{margin:0;display:block}}
.wm3-topbar__meta{{display:flex;flex-direction:column;gap:.08rem;min-width:0}}
.wm3-topbar__label{{
  font-family:var(--wm-font-display);font-size:.68rem;font-weight:600;
  letter-spacing:.06em;text-transform:uppercase;color:rgba(255,255,255,.55);line-height:1;
}}
.wm3-topbar__value{{
  font-size:.9rem;font-weight:600;color:rgba(255,255,255,.95);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2;
}}
.wm3-topbar__item--phone .wm3-topbar__value{{color:#ffd08a;font-weight:700}}
.wm3-topbar__item--phone .wm3-topbar__ico{{background:rgba(232,93,4,.22);color:#ffd08a}}
.wm3-topbar__sep{{
  width:1px;align-self:stretch;min-height:1.6rem;flex-shrink:0;
  background:linear-gradient(180deg,transparent,rgba(255,255,255,.28),transparent);
}}
@keyframes wm3-rail{{0%{{background-position:-40% 0}}100%{{background-position:140% 0}}}}
@keyframes wm3-sheen{{0%,100%{{opacity:.55}}50%{{opacity:1}}}}
@keyframes wm3-drift{{
  0%,100%{{transform:translateX(0)}}
  50%{{transform:translateX(6px)}}
}}
@media (prefers-reduced-motion:reduce){{
  .wm3-topbar__rail,.wm3-topbar__sheen,.wm3-topbar__row{{animation:none}}
}}
@media (max-width:900px){{
  .wm3-topbar__item--tag{{display:none}}
  .wm3-topbar__inner{{padding:.5rem .85rem}}
  .wm3-topbar__label{{display:none}}
  .wm3-topbar__value{{font-size:.82rem}}
  .wm3-topbar__row{{animation:none}}
}}
@media (max-width:640px){{
  .wm3-topbar__row{{flex-wrap:wrap;justify-content:center;gap:.5rem 1rem}}
  .wm3-topbar__sep{{display:none}}
  .wm3-topbar__item{{flex:0 1 auto;padding:.15rem .55rem}}
}}

.wm3-navbar{{
  position:relative;
  background:linear-gradient(180deg,#ffffff 0%,#f7fafc 100%);
  border-bottom:1px solid rgba(15,23,42,.06);
  box-shadow:0 10px 30px rgba(15,23,42,.05);
}}
.wm3-navbar::after{{
  content:"";position:absolute;left:0;right:0;bottom:0;height:2px;
  background:linear-gradient(90deg,transparent 0%,{tokens.accent} 20%,#ffb703 50%,{tokens.accent} 80%,transparent 100%);
  opacity:.55;
}}
.wm3-navbar__inner{{
  max-width:1140px;margin:0 auto;padding:.95rem 1.25rem;
  display:flex;align-items:center;gap:1.5rem;
}}
.wm3-brand{{
  display:flex;align-items:center;gap:.85rem;text-decoration:none;color:var(--wm-ink);min-width:0;
}}
.wm3-brand__logo{{
  width:3.85rem;height:3.85rem;object-fit:contain;flex-shrink:0;
  border-radius:12px;background:#0b1f33;
  box-shadow:0 6px 16px rgba(15,23,42,.12);
}}
.wm3-brand__mark{{
  width:3.85rem;height:3.85rem;border-radius:12px;flex-shrink:0;
  display:inline-flex;align-items:center;justify-content:center;
  background:linear-gradient(145deg,{tokens.accent},#c44d00);
  color:#fff;font-family:var(--wm-font-display);font-weight:800;font-size:1.5rem;
  box-shadow:0 8px 20px rgba(232,93,4,.28);
}}
.wm3-brand__stack{{display:flex;flex-direction:column;justify-content:center;gap:.18rem;line-height:1;min-width:0}}
.wm3-brand__name{{
  font-family:var(--wm-font-display);font-weight:800;font-size:.95rem;
  letter-spacing:.14em;text-transform:uppercase;color:#0b1f33;
}}
.wm3-brand__sub{{
  font-family:var(--wm-font-display);font-weight:700;font-size:.78rem;
  letter-spacing:.16em;text-transform:uppercase;color:{tokens.accent};
}}
.wm3-brand__text{{font-family:var(--wm-font-display);font-weight:800;font-size:1.12rem}}
.wm3-nav{{
  display:flex;flex-wrap:wrap;align-items:center;gap:.15rem 1.5rem;margin-left:auto;
}}
.wm3-nav__link{{
  position:relative;color:#334155;text-decoration:none;
  font-family:var(--wm-font-display);font-weight:600;font-size:.92rem;
  letter-spacing:-0.01em;padding:.4rem 0;
  transition:color var(--wm-ease,.22s ease);
}}
.wm3-nav__link::after{{
  content:"";position:absolute;left:0;bottom:0;width:100%;height:2px;
  background:{tokens.accent};border-radius:2px;
  transform:scaleX(0);transform-origin:left;transition:transform var(--wm-ease,.22s ease);
}}
.wm3-nav__link:hover{{color:{tokens.accent}}}
.wm3-nav__link:hover::after{{transform:scaleX(1)}}
.wm3-header-cta{{
  display:inline-flex;align-items:center;gap:.4rem;
  background:linear-gradient(135deg,{tokens.accent},#c44d00);
  color:#fff!important;text-decoration:none;font-weight:700;font-size:.88rem;
  padding:.68rem 1.05rem;border-radius:999px;white-space:nowrap;margin-left:.15rem;
  box-shadow:0 10px 24px rgba(232,93,4,.28);
  transition:transform var(--wm-ease,.22s ease),box-shadow var(--wm-ease,.22s ease);
}}
.wm3-header-cta .wm3-ico{{margin:0;display:block}}
.wm3-header-cta:hover{{transform:translateY(-2px);box-shadow:0 14px 28px rgba(232,93,4,.36);color:#fff!important}}

.wm3-site-footer{{background:#0a1425;color:rgba(255,255,255,.88);padding:3.75rem 1.25rem 0;font-family:var(--wm-font-body)}}
.wm3-footer__inner{{max-width:1140px;margin:0 auto}}
.wm3-footer__grid{{display:grid;grid-template-columns:1.45fr 1fr 1.15fr;gap:2.5rem 2.75rem;padding-bottom:2.6rem;align-items:start}}
.wm3-footer__lockup{{display:flex;align-items:center;gap:.85rem;margin:0 0 1.05rem}}
.wm3-footer__logo-img{{width:52px;height:52px;object-fit:contain;display:block;flex-shrink:0;filter:brightness(1.05)}}
.wm3-footer__logo-mark{{width:52px;height:52px;border-radius:12px;background:rgba(255,255,255,.08);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1.35rem;flex-shrink:0}}
.wm3-footer__wordmark{{display:flex;flex-direction:column;line-height:1.05}}
.wm3-footer__brand-top{{font-family:var(--wm-font-display);font-weight:800;font-size:1.55rem;letter-spacing:.04em;color:#fff}}
.wm3-footer__brand-sub{{font-family:var(--wm-font-display);font-weight:700;font-size:.92rem;letter-spacing:.08em;color:var(--wm-accent);margin-top:.12rem}}
.wm3-footer__slogan{{margin:0 0 .85rem;font-family:var(--wm-font-display);font-weight:700;font-size:1.05rem;color:#fff;line-height:1.35}}
.wm3-footer__slogan::before{{content:"";display:block;width:2.4rem;height:2px;background:var(--wm-accent);margin:0 0 .7rem}}
.wm3-footer__about{{margin:0 0 1.35rem;line-height:1.7;color:rgba(255,255,255,.82);font-size:1.08rem;max-width:38ch}}
.wm3-footer__usps{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.55rem .35rem}}
.wm3-footer__usp{{display:flex;flex-direction:column;align-items:center;gap:.4rem;text-align:center}}
.wm3-footer__usp-icon{{color:var(--wm-accent);display:flex;align-items:center;justify-content:center}}
.wm3-footer__usp-icon .wm3-ico{{margin:0;display:block}}
.wm3-footer__usp span{{font-size:.72rem;line-height:1.25;color:rgba(255,255,255,.9);font-weight:600}}
.wm3-footer__heading{{font-family:var(--wm-font-display);font-weight:700;color:#fff;margin:0 0 1.15rem;font-size:.95rem;letter-spacing:.12em;text-transform:uppercase}}
.wm3-footer__heading::after{{content:"";display:block;width:2.35rem;height:3px;background:var(--wm-accent);margin:.55rem 0 0;border-radius:2px}}
.wm3-footer__menu,.wm3-footer__clist{{list-style:none;padding:0;margin:0}}
.wm3-footer__menu li{{border-bottom:1px solid rgba(255,255,255,.12)}}
.wm3-footer__menu a{{display:flex;align-items:center;justify-content:space-between;gap:.75rem;padding:.85rem 0;color:rgba(255,255,255,.9);text-decoration:none;font-weight:500;transition:color var(--wm-ease,.22s ease),padding-left var(--wm-ease,.22s ease)}}
.wm3-footer__menu a:hover{{color:#fff;padding-left:.2rem}}
.wm3-footer__chev{{color:rgba(255,255,255,.75);display:inline-flex;transition:transform var(--wm-ease,.22s ease)}}
.wm3-footer__menu a:hover .wm3-footer__chev{{transform:translateX(3px)}}
.wm3-footer__chev .wm3-ico{{margin:0;display:block}}
.wm3-footer__clist li{{display:flex;align-items:flex-start;gap:.8rem;margin:0 0 1rem;color:rgba(255,255,255,.9);line-height:1.4}}
.wm3-footer__clist a{{display:flex;align-items:flex-start;gap:.8rem;color:inherit;text-decoration:none;transition:color var(--wm-ease,.22s ease)}}
.wm3-footer__clist a:hover{{color:#fff}}
.wm3-footer__cicon{{width:34px;height:34px;border-radius:50%;border:1.5px solid rgba(232,93,4,.55);color:var(--wm-accent);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0}}
.wm3-footer__cicon .wm3-ico{{margin:0;display:block}}
.wm3-footer__bar{{border-top:1px solid rgba(255,255,255,.12);padding:1.05rem 0 1.35rem;display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;font-size:.86rem;color:rgba(255,255,255,.58)}}
.wm3-footer__legal{{display:inline-flex;align-items:center;gap:.55rem}}
.wm3-footer__legal a{{color:rgba(255,255,255,.72);text-decoration:none}}
.wm3-footer__legal a:hover{{color:#fff}}
.wm3-ico{{display:inline-block;vertical-align:-2px;margin-right:.15rem}}

@media(max-width:900px){{
  .wm3-nav{{display:none}}
  .wm3-footer__grid{{grid-template-columns:1fr;gap:2rem}}
  .wm3-footer__usps{{grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem .75rem;max-width:18rem}}
  .wm3-navbar__inner{{padding:.7rem 1rem}}
}}
@media(max-width:560px){{
  .wm3-footer__bar{{flex-direction:column;align-items:flex-start}}
}}
"""


# ── SVG icons ─────────────────────────────────────────────────────────────────

_ICONS: dict[str, str] = {
    "home": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-9.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    "building": '<path d="M4 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16M14 9h5a1 1 0 0 1 1 1v11M8 8h2M8 12h2M8 16h2M17 13h1M17 17h1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    "box": '<path d="M12 3 3.5 7.5v9L12 21l8.5-4.5v-9L12 3zm0 0v18M3.5 7.5 12 12l8.5-4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    "truck": '<path d="M1 7h11v10H1V7zm11 3h4l3 3v4h-7v-7zM5 20a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm11 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    "recycle": '<path d="M7 19h4M3 13l2.2-4M9 5l3 1M17 5l2.5 4M21 13l-2 4M14 19l-1.5-1M8.5 9.5 7 19M15.5 9.5 17 5M18.5 17 14 19" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    "check": '<path d="M5 12.5 9.5 17 19 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "phone": '<path d="M6.5 3.5h3l1.5 4-2 1.5a12 12 0 0 0 5.5 5.5l1.5-2 4 1.5v3A2 2 0 0 1 18 19 14.5 14.5 0 0 1 3.5 4.5a2 2 0 0 1 3-1z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    "mail": '<path d="M3.5 6.5h17v11h-17v-11zm0 0 8.5 6.5 8.5-6.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    "pin": '<path d="M12 21s6-5.2 6-11a6 6 0 1 0-12 0c0 5.8 6 11 6 11zm0-8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" fill="none" stroke="currentColor" stroke-width="1.7"/>',
    "clock": '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    "arrow": '<path d="M5 12h14M14 7l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round"/>',
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5H19.5A1.5 1.5 0 0 1 21 10v7a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17V7.5z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    "shield": '<path d="M12 3 5 6v6c0 4.5 3.2 7.8 7 9 3.8-1.2 7-4.5 7-9V6l-7-3z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    "shield_check": '<path d="M12 3 5 6v6c0 4.5 3.2 7.8 7 9 3.8-1.2 7-4.5 7-9V6l-7-3z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M9.2 12.2 11 14l3.8-3.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M8 3.5v3M16 3.5v3M3.5 9.5h17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    "user": '<circle cx="12" cy="8" r="3.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M5 19.5c0-3.1 2.9-5.2 7-5.2s7 2.1 7 5.2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    "chevron": '<path d="M9 6.5 14.5 12 9 17.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "users": '<path d="M8.5 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7 0a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3.5 19c0-2.8 2.2-4.5 5-4.5s5 1.7 5 4.5M13.5 14.8c1.1-.5 2.3-.7 3.5-.7 2.3 0 4 1.4 4 3.4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    "spark": '<path d="M12 3v4M12 17v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M3 12h4M17 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    "door": '<path d="M5 21V4.5A1.5 1.5 0 0 1 6.5 3H15v18H5zm10 0h3.5A1.5 1.5 0 0 0 20 19.5V6H15M12.5 12.5h.01" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    "leaf": '<path d="M5 19C5 10 12 4 20 4c0 8-6 15-15 15-1 0-2-.3-3-1 3-1 5-3 6-6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    "tag": '<path d="M3.5 12.5V5.5A2 2 0 0 1 5.5 3.5h7l8 8-7 7-8-8z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/>',
    "headset": '<path d="M4 14v-2a8 8 0 1 1 16 0v2M4 14a2.2 2.2 0 0 0 2.2 2.2H7.5V12H6.2A2.2 2.2 0 0 0 4 14.2zm16-.2a2.2 2.2 0 0 1-2.2 2.2H16.5V12h1.3A2.2 2.2 0 0 1 20 13.8zM12 19h2.2A2.3 2.3 0 0 0 16.5 16.7V15.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    "broom": '<path d="M9 3.5h2.2M10.1 3.5v8.2M6.2 21l1.8-8.2h4.2L14 21M7.2 14.2h5.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    "whatsapp": '<path d="M12 3.5a8.5 8.5 0 0 0-7.3 12.8L4 20.5l4.3-.7A8.5 8.5 0 1 0 12 3.5z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M9.2 9.4c.2-.4.4-.4.6-.4h.5c.2 0 .4 0 .5.4l.7 1.7c.1.2 0 .4-.1.6l-.4.5c-.1.1-.1.3 0 .4.3.5.8 1.1 1.3 1.5.5.4 1 .7 1.5.9.2.1.3 0 .4-.1l.6-.7c.2-.2.4-.2.6-.1l1.7.7c.3.1.4.3.4.5v.5c0 .2 0 .4-.2.6-.3.4-.8.7-1.3.8-.5.1-1.1 0-1.8-.2-1.5-.5-3.1-1.7-4.3-3.2-1.1-1.4-1.8-2.9-1.9-4.2 0-.6.1-1.1.4-1.5z" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/>',
    "facebook": '<path d="M14 8.5h2.2V6H14c-1.9 0-3.2 1.4-3.2 3.3V11H9v2.5h1.8V20h2.6v-6.5H16L16.5 11h-2.1V9.5c0-.6.3-1 1-1z" fill="currentColor"/>',
    "linkedin": '<path d="M6.2 9.2H3.8V20h2.4V9.2zM5 3.8a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8zM20.2 20h-2.4v-5.6c0-1.5-.6-2.4-1.8-2.4-1 0-1.5.7-1.8 1.3-.1.2-.1.6-.1.9V20h-2.4s.1-8.7 0-9.6h2.4v1.5c.4-.6 1.2-1.7 3.1-1.7 2.2 0 3.8 1.4 3.8 4.6V20z" fill="currentColor"/>',
}


_SERVICE_ICON_CYCLE = (
    "home", "door", "building", "box", "truck", "recycle",
    "spark", "shield", "users", "check", "leaf",
)


def icon(name: str, size: int = 28) -> str:
    """Public helper: inline SVG icon HTML."""
    return _icon(name, size)


def icon_for_label(label: str, index: int = 0, size: int = 28) -> str:
    """Pick a semantic icon from a German/English service label."""
    low = (label or "").lower()
    if any(k in low for k in ("haushalt",)):
        key = "door"
    elif any(k in low for k in ("wohnung", "home", "wohn")):
        key = "home"
    elif any(k in low for k in ("gewerb", "büro", "buero", "office", "lager")):
        key = "building"
    elif any(k in low for k in ("keller", "dach", "speicher")):
        key = "box"
    elif any(k in low for k in ("sperrmüll", "sperrmull", "entsorg", "müll", "truck")):
        key = "truck"
    elif any(k in low for k in ("besenrein", "übergabe", "uebergabe", "sauber", "clean", "broom")):
        key = "broom"
    elif any(k in low for k in ("festpreis", "preis", "kosten", "tag", "transparent")):
        key = "tag"
    elif any(k in low for k in ("service", "kundenorient", "beratung", "headset")):
        key = "headset"
    elif any(k in low for k in ("termin", "schnell", "zeit", "clock")):
        key = "clock"
    elif any(k in low for k in ("recycl", "umwelt", "öko", "oeko", "grün", "leaf")):
        key = "leaf" if "umwelt" in low or "leaf" in low else "recycle"
    elif any(k in low for k in ("tür", "tur", "door")):
        key = "door"
    elif any(k in low for k in ("team", "wir", "kunde", "trust", "erfahren")):
        key = "users"
    elif any(k in low for k in ("sicher", "versichert", "schutz")):
        key = "shield"
    else:
        key = _SERVICE_ICON_CYCLE[index % len(_SERVICE_ICON_CYCLE)]
    return _icon(key, size)


def _icon(name: str, size: int) -> str:
    path = _ICONS.get(name) or _ICONS["check"]
    return (
        f'<svg class="wm3-ico" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'aria-hidden="true" focusable="false">{path}</svg>'
    )


def wp_additional_css(tokens: DesignTokens) -> str:
    """Site-wide Additional CSS for WordPress Customizer.

    Hides Kadence chrome/title so Agent 1 header + full-bleed hero own the
    first viewport (avoids the empty \"Startseite\" hero band).
    """
    return f"""/* WebMaker Agent 1 design system */
@import url('{tokens.font_url}');
:root{{
  --wm-accent:{tokens.accent};
  --wm-navy:{tokens.navy};
}}
body{{
  font-family:'{tokens.font_body}',system-ui,sans-serif !important;
}}
h1,h2,h3,h4,h5,.entry-title{{
  font-family:'{tokens.font_display}',system-ui,sans-serif !important;
  letter-spacing:-0.02em;
}}
.wp-block-button__link,.button,button[type=submit]{{
  background:{tokens.accent} !important;
  border-color:{tokens.accent} !important;
}}

/* Hide theme shell — custom wm3 chrome + sections replace it */
#masthead,
header.site-header,
.site-header-wrap,
#colophon,
footer.site-footer,
.site-footer-wrap{{
  display:none !important;
}}
.entry-hero,
.page-hero-section,
.entry-hero-container-inner,
.entry-header.page-title,
.entry-header .entry-title,
.kadence-breadcrumbs{{
  display:none !important;
  min-height:0 !important;
  padding:0 !important;
  margin:0 !important;
  height:0 !important;
  overflow:hidden !important;
}}

/* Full-bleed content — break out of Kadence content width */
.content-container,
.content-wrap,
.entry-content-wrap,
.site-container,
.content-bg .content-container,
body.page .content-container{{
  max-width:100% !important;
  width:100% !important;
  padding-left:0 !important;
  padding-right:0 !important;
}}
.entry-content,
.entry-content-wrap .entry-content{{
  max-width:100% !important;
  margin:0 !important;
  padding:0 !important;
}}
.entry-content > .wp-block-html,
.entry-content > *:first-child{{
  margin-top:0 !important;
}}
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _clean_company_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    # Drop duplicated SEO spam tails
    if "Entrümpelung" in name:
        # Prefer first meaningful chunk
        parts = re.split(r"\s{2,}|\s::\s", name)
        name = parts[0].strip()
    if len(name) > 48:
        name = name[:45].rsplit(" ", 1)[0]
    return name


def _short_brand(name: str, business: dict[str, Any] | None = None) -> str:
    cleaned = (name or "").strip()
    if not cleaned and business:
        emails = business.get("emails") or []
        # Prefer local-part domain brand only when no company name exists
        for e in emails:
            s = str(e)
            if "@" in s:
                domain = s.split("@", 1)[1].split(".", 1)[0]
                if domain and domain.lower() not in {"gmail", "yahoo", "outlook", "web", "gmx"}:
                    return domain[:40].title()
    return cleaned[:40]


def _best_phone(phones: list) -> str:
    for p in phones:
        s = str(p).strip()
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 10:
            return s
    return str(phones[0]).strip() if phones else ""


def _best_address(addrs: list) -> str:
    for a in addrs:
        s = str(a).strip()
        if "Telefon" in s:
            continue
        if re.search(r"\d{5}", s):
            return s.replace(" Deutschland", "").strip()
    return str(addrs[0]).strip() if addrs else ""


def _best_hours(hours: list) -> str:
    for h in hours:
        cleaned = _clean_hours(str(h))
        if cleaned:
            return cleaned
    return "Montag bis Freitag von 8–20 Uhr"


def _clean_hours(raw: str) -> str:
    """Extract a clean opening-hours phrase; strip crawl SEO spam / emails."""
    s = re.sub(r"\s+", " ", (raw or "")).strip()
    if not s:
        return ""
    s = re.sub(r"\S+@\S+", "", s).strip()
    m = re.search(
        r"((?:Mo(?:ntag)?|Montag)\s*(?:bis|-|–)\s*(?:Fr(?:eitag)?|Freitag)"
        r"[^.]{0,28}?Uhr)",
        s,
        re.I,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    if "Uhr" in s and len(s) <= 48 and "@" not in s:
        return s
    return ""


def _is_usable_brand_color(hex_color: str) -> bool:
    """Reject near-grey / near-white / near-black crawl noise."""
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        return False
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return False
    # grey if channels close
    if max(r, g, b) - min(r, g, b) < 25:
        return False
    lum = (r * 299 + g * 587 + b * 114) / 1000
    return 40 < lum < 210


def _darken(hex_color: str, factor: float) -> str:
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _build_nav(nav: dict[str, Any]) -> list[dict[str, str]]:
    """Map package nav + standard WP slugs into short premium labels."""
    standard = [
        {"label": "Startseite", "href": "/"},
        {"label": "Leistungen", "href": "/services/"},
        {"label": "Über uns", "href": "/about/"},
        {"label": "Kontakt", "href": "/contact/"},
        {"label": "FAQ", "href": "/faq/"},
    ]
    package_items = []
    for it in nav.get("items") or []:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or it.get("label") or "").strip()
        url = str(it.get("url") or "").strip()
        if not text or text.lower() in ("facebook", "instagram", "x"):
            continue
        if "facebook" in url or "instagram" in url or "x.com" in url:
            continue
        low = text.lower()
        if "start" in low:
            href, label = "/", "Startseite"
        elif "kontakt" in low:
            href, label = "/contact/", "Kontakt"
        elif "ablauf" in low or "bild" in low or "leistung" in low:
            href, label = "/services/", "Leistungen"
        elif "bewertung" in low:
            href, label = "/about/", "Bewertungen"
        elif "über" in low or "about" in low:
            href, label = "/about/", "Über uns"
        elif "faq" in low:
            href, label = "/faq/", "FAQ"
        else:
            href = url or "#"
            label = text if len(text) <= 16 else text.split("/")[0].strip()[:16]
        package_items.append({"label": label, "href": href})

    # Prefer clean standard IA for the local WP demo when package nav is thin/spammy
    if len(package_items) < 3:
        return standard
    return package_items[:5]
