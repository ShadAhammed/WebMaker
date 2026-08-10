"""
Leistungen (services) page — premium service guide.

Distinct from the homepage: no photo-card grid, process strip, trust bar,
before/after, or generic homepage CTA. SEO-oriented German service content.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from webmaker.utils.project_paths import project_path
from typing import Any

from webmaker.agents.website_modernizer.design_system import icon, icon_for_label
from webmaker.agents.website_modernizer.image_bank import publish_local_for_wp

_PHONE_DISPLAY = ""
_PHONE_TEL = ""
_CONTACT = "/contact/"


def _slugify(text: str) -> str:
    t = (text or "").lower().strip()
    rep = (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
    )
    for a, b in rep:
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t or "section"


def _img_src(raw: str) -> str:
    """Resolve local project image path or pass through absolute/WP URLs."""
    src = (raw or "").strip()
    if not src:
        return ""
    if src.startswith(("http://", "https://", "/")):
        # still cache-bust published webmaker assets
        if "/uploads/webmaker/" in src and "v=leistung" not in src:
            sep = "&" if "?" in src else "?"
            return f"{src.split('?', 1)[0]}{sep}v=leistung1"
        return src
    roots = [
        project_path("images"),
    ]
    name = Path(src).name
    for root in roots:
        for cand in (
            root / src,
            root / name,
            root / "services" / name,
            root / "Leistung" / name,
            root / "Leistung" / src,
        ):
            if cand.is_file():
                published = publish_local_for_wp(cand)
                if published:
                    sep = "&" if "?" in published else "?"
                    return f"{published}{sep}v=leistung1"
    return src


def render_services_section(sec: dict) -> str | None:
    """Return HTML for a Leistungen-only section type, or None if not handled."""
    t = (sec.get("type") or "").lower()
    v = (sec.get("layout_variant") or "").lower()

    if t == "hero" and v in ("services_premium", "services_hero", "premium_services"):
        return _hero_services(sec)
    if t == "intro_split":
        return _intro_split(sec)
    if t == "service_nav":
        return _service_nav(sec)
    if t == "service_details":
        return _service_details(sec)
    if t == "benefits_grid":
        return _benefits_grid(sec)
    if t == "sustainability":
        return _sustainability(sec)
    if t == "tax_info":
        return _tax_info(sec)
    if t == "service_areas":
        return _service_areas(sec)
    if t == "faq" and v in ("accordion", "accordion_list", "services_faq"):
        return _faq_accordion(sec)
    if t == "cta_banner" and v in ("photo_final", "photo_cta", "final_cta"):
        return _final_cta(sec)
    return None


def services_guide_css() -> str:
    """CSS for Leistungen guide sections (homepage tokens reuse)."""
    return r"""
/* ═══ Leistungen guide ═══ */
.wm3-svc-hero{
  position:relative;isolation:isolate;overflow:hidden;min-height:min(76vh,700px);
  display:flex;align-items:center;padding:clamp(4.2rem,9vw,6.2rem) 0 clamp(2.6rem,5vw,3.4rem);
  background:#0b1f33 center/cover no-repeat;color:#fff;
}
.wm3-svc-hero__shade{
  position:absolute;inset:0;z-index:0;pointer-events:none;
  background:linear-gradient(105deg,rgba(8,14,22,.9) 0%,rgba(8,14,22,.72) 42%,rgba(8,14,22,.32) 72%,rgba(8,14,22,.14) 100%),
    linear-gradient(180deg,rgba(8,14,22,.12) 0%,rgba(8,14,22,.42) 100%);
}
.wm3-svc-hero__inner{
  position:relative;z-index:1;width:100%;max-width:min(100%,1180px);margin:0 auto;
  padding:0 clamp(1.15rem,3.5vw,2rem);box-sizing:border-box;
}
.wm3-svc-hero__copy{max-width:40rem}
.wm3-svc-hero__eyebrow{
  margin:0 0 1.05rem;color:rgba(255,255,255,.88);font-family:var(--wm-font-display);
  font-size:.74rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
}
.wm3-svc-hero__eyebrow::after{
  content:"";display:block;width:2.4rem;height:2px;margin:.6rem 0 0;background:var(--wm-accent);border-radius:1px;
}
.wm3-svc-hero__title{
  margin:0 0 .75rem!important;max-width:11.5em;
  font-family:var(--wm-font-display)!important;font-size:clamp(2.45rem,5.6vw,3.85rem)!important;
  font-weight:800!important;line-height:1.06!important;letter-spacing:-.03em;color:#fff!important;
}
.wm3-svc-hero__title-accent{color:var(--wm-accent)!important;display:inline}
.wm3-svc-hero__tagline{
  margin:0 0 .9rem;font-size:clamp(1.05rem,1.55vw,1.2rem);line-height:1.4;
  color:rgba(255,255,255,.94);font-weight:600;letter-spacing:.005em;max-width:34rem;
}
.wm3-svc-hero__sub{
  margin:0 0 1.7rem;max-width:33rem;font-size:clamp(.98rem,1.35vw,1.08rem);
  line-height:1.65;color:rgba(255,255,255,.82);font-weight:500;
}
.wm3-svc-hero__sub strong{color:#fff;font-weight:700}
.wm3-svc-hero__actions{display:flex;flex-wrap:wrap;gap:.75rem;margin:0 0 2.15rem;align-items:center}
.wm3-svc-hero__btn{
  display:inline-flex;align-items:center;justify-content:center;gap:.45rem;
  padding:.92rem 1.35rem;border-radius:12px;font-weight:700;
  font-size:.98rem;letter-spacing:.01em;text-decoration:none!important;line-height:1.25;
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),background var(--wm-ease),border-color var(--wm-ease);
}
.wm3-svc-hero__btn--primary{
  background:var(--wm-accent);color:#fff!important;box-shadow:0 10px 24px rgba(232,93,4,.3);
}
.wm3-svc-hero__btn--primary:hover{transform:translateY(-1px);background:var(--wm-accent-dark);box-shadow:0 12px 26px rgba(232,93,4,.38)}
.wm3-svc-hero__btn--ghost{
  background:transparent;color:#fff!important;border:1.5px solid rgba(255,255,255,.55);
  font-weight:600;
}
.wm3-svc-hero__btn--ghost:hover{transform:translateY(-1px);background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.9)}
.wm3-svc-hero__usps{
  list-style:none;margin:0;padding:1.35rem 0 0;
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1.1rem 1.4rem;
  max-width:54rem;border-top:1px solid rgba(255,255,255,.16);
}
.wm3-svc-hero__usp{
  display:grid;grid-template-columns:auto 1fr;gap:.65rem;align-items:start;
  margin:0;padding:.1rem 0;
}
.wm3-svc-hero__usp-ico{
  width:1.7rem;height:1.7rem;display:inline-flex;align-items:center;justify-content:center;
  color:var(--wm-accent);flex-shrink:0;margin-top:.08rem;opacity:.95;
}
.wm3-svc-hero__usp-title{
  display:block;margin:0 0 .12rem;font-size:.9rem;font-weight:700;line-height:1.25;color:#fff;
}
.wm3-svc-hero__usp-desc{
  display:block;margin:0;font-size:.78rem;line-height:1.35;color:rgba(255,255,255,.68);font-weight:500;
}
@media(max-width:900px){
  .wm3-svc-hero__usps{grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem 1.1rem;max-width:100%}
}
@media(max-width:560px){
  .wm3-svc-hero{align-items:flex-end;min-height:min(84vh,740px);padding-top:5rem}
  .wm3-svc-hero__usps{grid-template-columns:1fr}
  .wm3-svc-hero__actions{flex-direction:column;align-items:stretch}
  .wm3-svc-hero__btn{width:100%}
}

.wm3-svc-intro{background:#fff;padding:var(--wm-space-section,2.5rem) 0}
.wm3-svc-intro__inner{
  max-width:min(100%,1180px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem);
  display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr);gap:clamp(1.5rem,3vw,2.5rem);align-items:center;
}
.wm3-svc-intro__eyebrow{
  margin:0 0 .4rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
}
.wm3-svc-intro__title{
  margin:0 0 1rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.55rem,2.4vw,2.05rem)!important;font-weight:800!important;
  color:var(--wm-navy)!important;line-height:1.15!important;letter-spacing:-.02em;
}
.wm3-svc-intro__copy p{margin:0 0 .85rem;color:var(--wm-muted);font-size:1.02rem;line-height:1.65;max-width:36rem}
.wm3-svc-intro__copy p:last-child{margin-bottom:0}
.wm3-svc-intro__card{
  background:linear-gradient(165deg,#fff 0%,#f8fafc 100%);
  border:1px solid rgba(15,23,42,.06);border-radius:var(--wm-radius-lg,18px);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  padding:1.45rem 1.35rem 1.35rem;
}
.wm3-svc-intro__card-title{
  margin:0 0 1rem;font-family:var(--wm-font-display);font-size:1.05rem;font-weight:800;
  color:var(--wm-navy);letter-spacing:-.01em;
}
.wm3-svc-intro__list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.7rem}
.wm3-svc-intro__list li{
  display:flex;align-items:flex-start;gap:.65rem;margin:0;color:var(--wm-ink);
  font-size:.98rem;font-weight:600;line-height:1.35;
}
.wm3-svc-intro__list .wm3-ico{color:var(--wm-accent);flex-shrink:0;margin-top:.1rem}

.wm3-svc-nav{background:var(--wm-surface-alt,#eef2f7);padding:var(--wm-space-section,2.5rem) 0}
.wm3-svc-nav__inner{max-width:min(100%,1180px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem)}
.wm3-svc-nav__head{text-align:center;max-width:40rem;margin:0 auto 1.55rem}
.wm3-svc-nav__eyebrow{
  margin:0 0 .4rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
}
.wm3-svc-nav__title{
  margin:0!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.45rem,2.3vw,1.95rem)!important;font-weight:800!important;
  color:var(--wm-navy)!important;line-height:1.15!important;
}
.wm3-svc-nav__grid{
  list-style:none;margin:0;padding:0;
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.85rem;
}
.wm3-svc-nav__card{
  display:flex;flex-direction:column;align-items:flex-start;gap:.55rem;
  padding:1.15rem 1.05rem 1.2rem;min-height:100%;box-sizing:border-box;
  background:#fff;border:1px solid rgba(15,23,42,.05);border-radius:var(--wm-radius,16px);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  text-decoration:none!important;color:inherit;
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),border-color var(--wm-ease);
}
.wm3-svc-nav__card:hover{
  transform:translateY(-3px);border-color:rgba(232,93,4,.2);
  box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));
}
.wm3-svc-nav__icon{
  width:2.65rem;height:2.65rem;border-radius:12px;display:flex;align-items:center;justify-content:center;
  background:#fff4ec;color:var(--wm-accent);transition:transform var(--wm-ease);
}
.wm3-svc-nav__card:hover .wm3-svc-nav__icon{transform:scale(1.06)}
.wm3-svc-nav__label{
  font-family:var(--wm-font-display);font-size:.98rem;font-weight:700;color:var(--wm-navy);line-height:1.25;
}
.wm3-svc-nav__hint{margin:0;font-size:.82rem;color:var(--wm-muted);line-height:1.4}

.wm3-svc-detail{padding:clamp(2.35rem,4vw,3.15rem) 0;background:#fff}
.wm3-svc-detail:nth-of-type(even),.wm3-svc-detail--alt{background:var(--wm-surface-alt,#eef2f7)}
.wm3-svc-detail__inner{
  max-width:min(100%,1488px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem);
  display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);
  gap:clamp(1.35rem,3vw,2.35rem);align-items:stretch;box-sizing:border-box;
}
.wm3-svc-detail--rtl .wm3-svc-detail__inner{direction:rtl}
.wm3-svc-detail--rtl .wm3-svc-detail__inner > *{direction:ltr}
.wm3-svc-detail__media{
  margin:0;min-width:0;min-height:0;align-self:stretch;position:relative;
  border-radius:var(--wm-radius-lg,18px);overflow:hidden;
  background:transparent!important;box-shadow:none!important;border:0!important;padding:0!important;
}
.wm3-svc-detail__img{
  position:absolute;inset:0;margin:auto;
  display:block;width:auto;height:auto;
  max-width:100%;max-height:100%;
  object-fit:contain;object-position:center;
  background:transparent!important;box-shadow:none!important;border:0!important;border-radius:var(--wm-radius-lg,18px);
  filter:saturate(1.04) brightness(1.01) contrast(1.02);
}
.wm3-svc-detail__copy{min-width:0}
.wm3-svc-detail__title{
  margin:0 0 .55rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.5rem,2.4vw,2.05rem)!important;font-weight:800!important;
  color:var(--wm-navy)!important;line-height:1.12!important;letter-spacing:-.02em;
}
.wm3-svc-detail__intro{
  margin:0 0 1.1rem;color:var(--wm-muted);font-size:1.02rem;line-height:1.5;
  max-width:36rem;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
}
.wm3-svc-mod{margin:0 0 1rem}
.wm3-svc-mod:last-of-type{margin-bottom:1.1rem}
.wm3-svc-mod__label{
  margin:0 0 .55rem;font-family:var(--wm-font-display);font-size:.72rem;font-weight:700;
  letter-spacing:.12em;text-transform:uppercase;color:var(--wm-muted);
}
.wm3-svc-mod__grid{
  list-style:none;margin:0;padding:0;
  display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.55rem;
}
.wm3-svc-mod__card{
  display:flex;align-items:flex-start;gap:.55rem;margin:0;padding:.75rem .8rem;
  background:#fff;border:1px solid rgba(15,23,42,.06);border-radius:var(--wm-radius-sm,12px);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
}
.wm3-svc-detail--alt .wm3-svc-mod__card{background:rgba(255,255,255,.92)}
.wm3-svc-mod__card-ico{
  width:2.1rem;height:2.1rem;border-radius:10px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;background:#fff4ec;color:var(--wm-accent);
}
.wm3-svc-mod__card-title{
  margin:0;font-family:var(--wm-font-display);font-size:.9rem;font-weight:700;color:var(--wm-navy);line-height:1.25;
}
.wm3-svc-mod__card-desc{margin:.2rem 0 0;font-size:.8rem;line-height:1.4;color:var(--wm-muted)}
.wm3-svc-mod__chips{
  list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:.45rem;
}
.wm3-svc-mod__chip{
  display:inline-flex;align-items:center;gap:.35rem;margin:0;padding:.42rem .75rem .42rem .5rem;
  background:rgba(11,31,51,.04);border:1px solid rgba(15,23,42,.06);border-radius:999px;
  font-size:.86rem;font-weight:600;color:var(--wm-ink);line-height:1.2;
}
.wm3-svc-mod__chip .wm3-ico{color:var(--wm-accent);flex-shrink:0}
.wm3-svc-mod__check{
  list-style:none;margin:0;padding:.85rem .95rem;
  background:linear-gradient(165deg,#fffaf5 0%,#fff 60%);
  border:1px solid rgba(232,93,4,.14);border-radius:var(--wm-radius,16px);
  display:flex;flex-direction:column;gap:.4rem;
}
.wm3-svc-detail--alt .wm3-svc-mod__check{background:linear-gradient(165deg,#fff 0%,#f8fafc 100%)}
.wm3-svc-mod__check li{
  display:flex;align-items:flex-start;gap:.45rem;margin:0;
  font-size:.9rem;font-weight:600;color:var(--wm-ink);line-height:1.35;
}
.wm3-svc-mod__check .wm3-ico{color:var(--wm-accent);flex-shrink:0;margin-top:.08rem}
.wm3-svc-mod__stats{
  list-style:none;margin:0;padding:0;
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.55rem;
}
.wm3-svc-mod__stat{
  margin:0;padding:.85rem .7rem;text-align:center;
  background:#fff;border:1px solid rgba(15,23,42,.06);border-radius:var(--wm-radius-sm,12px);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
}
.wm3-svc-detail--alt .wm3-svc-mod__stat{background:rgba(255,255,255,.92)}
.wm3-svc-mod__stat-val{
  display:block;font-family:var(--wm-font-display);font-size:1.25rem;font-weight:800;
  color:var(--wm-accent);line-height:1.1;letter-spacing:-.02em;
}
.wm3-svc-mod__stat-lbl{display:block;margin-top:.25rem;font-size:.78rem;font-weight:600;color:var(--wm-muted);line-height:1.3}
.wm3-svc-mod__steps{
  list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.45rem;counter-reset:wm3step;
}
.wm3-svc-mod__step{
  counter-increment:wm3step;display:flex;align-items:flex-start;gap:.65rem;margin:0;
  padding:.65rem .75rem;background:#fff;border:1px solid rgba(15,23,42,.06);
  border-radius:var(--wm-radius-sm,12px);font-size:.9rem;font-weight:600;color:var(--wm-ink);line-height:1.35;
}
.wm3-svc-detail--alt .wm3-svc-mod__step{background:rgba(255,255,255,.92)}
.wm3-svc-mod__step::before{
  content:counter(wm3step);flex-shrink:0;width:1.55rem;height:1.55rem;border-radius:999px;
  display:flex;align-items:center;justify-content:center;
  background:var(--wm-accent);color:#fff;font-size:.75rem;font-weight:800;line-height:1;
}
.wm3-svc-detail__more{
  margin:0 0 1.15rem;border:1px solid rgba(15,23,42,.07);border-radius:var(--wm-radius,16px);
  background:rgba(255,255,255,.55);overflow:hidden;
}
.wm3-svc-detail__more[open]{border-color:rgba(232,93,4,.18);background:#fff}
.wm3-svc-detail__more-q{
  cursor:pointer;list-style:none;padding:.9rem 1.05rem;font-family:var(--wm-font-display);
  font-size:.95rem;font-weight:700;color:var(--wm-navy);line-height:1.3;
  display:flex;align-items:center;justify-content:space-between;gap:1rem;
}
.wm3-svc-detail__more-q::-webkit-details-marker{display:none}
.wm3-svc-detail__more-q::after{
  content:"";width:.5rem;height:.5rem;border-right:2px solid var(--wm-accent);border-bottom:2px solid var(--wm-accent);
  transform:rotate(45deg);transition:transform var(--wm-ease);flex-shrink:0;margin-top:-.15rem;
}
.wm3-svc-detail__more[open] .wm3-svc-detail__more-q::after{transform:rotate(-135deg);margin-top:.15rem}
.wm3-svc-detail__more-body{padding:0 1.05rem 1.05rem}
.wm3-svc-detail__more-body p{
  margin:0 0 .7rem;color:var(--wm-muted);font-size:.92rem;line-height:1.6;
}
.wm3-svc-detail__more-body p:last-child{margin-bottom:0}
.wm3-svc-detail__more-body ul{
  margin:0;padding:0 0 0 1.05rem;color:var(--wm-muted);font-size:.92rem;line-height:1.55;
}
.wm3-svc-detail__more-body li{margin:0 0 .28rem}
.wm3-svc-detail__cta{
  display:inline-flex;align-items:center;gap:.4rem;padding:.88rem 1.35rem;
  border-radius:var(--wm-radius-sm,12px);background:var(--wm-accent);color:#fff!important;
  font-weight:700;font-size:.98rem;text-decoration:none!important;
  box-shadow:0 8px 20px rgba(232,93,4,.28);
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),background var(--wm-ease);
}
.wm3-svc-detail__cta:hover{transform:translateY(-2px);background:var(--wm-accent-dark);box-shadow:0 12px 26px rgba(232,93,4,.36)}

.wm3-svc-why{background:#fff;padding:var(--wm-space-section,2.5rem) 0}
.wm3-svc-why__inner{max-width:min(100%,1180px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem)}
.wm3-svc-why__head{text-align:center;max-width:40rem;margin:0 auto 2rem}
.wm3-svc-why__eyebrow{
  margin:0 0 .45rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.74rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
}
.wm3-svc-why__title{
  margin:0 0 .55rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.5rem,2.4vw,2rem)!important;font-weight:800!important;color:var(--wm-navy)!important;
  letter-spacing:-.02em;
}
.wm3-svc-why__lead{
  margin:0 auto;max-width:34rem;color:var(--wm-muted);font-size:1.02rem;line-height:1.55;font-weight:500;
}
.wm3-svc-why__featured{
  list-style:none;margin:0 0 1.75rem;padding:0;
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1.1rem;
}
.wm3-svc-why__card{
  margin:0;padding:1.45rem 1.2rem 1.5rem;background:#fff;
  border:1px solid rgba(15,23,42,.08);border-radius:var(--wm-radius,16px);
  box-shadow:0 1px 0 rgba(15,23,42,.02);
  transition:border-color var(--wm-ease),box-shadow var(--wm-ease);
}
.wm3-svc-why__card:hover{
  border-color:rgba(15,23,42,.14);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
}
.wm3-svc-why__card--featured{
  padding:1.55rem 1.25rem 1.6rem;
  background:linear-gradient(180deg,#fff 0%,var(--wm-surface,#f8fafc) 100%);
  border-color:rgba(15,23,42,.09);
}
.wm3-svc-why__icon{
  width:2.55rem;height:2.55rem;border-radius:11px;display:flex;align-items:center;justify-content:center;
  background:var(--wm-surface-alt,#eef2f7);color:var(--wm-accent);margin:0 0 .85rem;
}
.wm3-svc-why__card--featured .wm3-svc-why__icon{
  width:2.75rem;height:2.75rem;background:#fff;
  box-shadow:inset 0 0 0 1px rgba(15,23,42,.06);
}
.wm3-svc-why__card-title{
  margin:0 0 .45rem;font-family:var(--wm-font-display);font-size:1.05rem;font-weight:700;color:var(--wm-navy);
  letter-spacing:-.01em;line-height:1.25;
}
.wm3-svc-why__card--featured .wm3-svc-why__card-title{font-size:1.12rem}
.wm3-svc-why__card-desc{margin:0;color:var(--wm-muted);font-size:.9rem;line-height:1.55}
.wm3-svc-why__card--featured .wm3-svc-why__card-desc{font-size:.92rem;line-height:1.58}
.wm3-svc-why__support{
  list-style:none;margin:0;padding:1.35rem 0 0;
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1rem 1.35rem;
  border-top:1px solid rgba(15,23,42,.08);
}
.wm3-svc-why__support-item{
  margin:0;padding:.15rem 0 .15rem 0;display:grid;grid-template-columns:auto 1fr;gap:.7rem;align-items:start;
}
.wm3-svc-why__support-ico{
  width:1.55rem;height:1.55rem;display:inline-flex;align-items:center;justify-content:center;
  color:var(--wm-accent);margin-top:.12rem;opacity:.9;
}
.wm3-svc-why__support-title{
  margin:0 0 .2rem;font-family:var(--wm-font-display);font-size:.92rem;font-weight:700;color:var(--wm-navy);
  line-height:1.25;
}
.wm3-svc-why__support-desc{
  margin:0;color:var(--wm-muted);font-size:.84rem;line-height:1.45;
}

.wm3-svc-green{
  position:relative;isolation:isolate;overflow:hidden;
  padding:var(--wm-space-section,2.5rem) 0;background:#f4f7f4;
}
.wm3-svc-green__inner{
  max-width:min(100%,1180px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem);
  display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);
  gap:clamp(1.25rem,2.5vw,2rem) clamp(1.5rem,3vw,2.5rem);align-items:start;
}
.wm3-svc-green__media{
  grid-column:1;grid-row:1 / span 2;margin:0;border-radius:var(--wm-radius-lg,18px);overflow:hidden;
  align-self:stretch;min-height:100%;aspect-ratio:auto;
  box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));background:#1a3a2a;
}
.wm3-svc-green__img{width:100%;height:100%;object-fit:cover;display:block;filter:saturate(1.04) brightness(1.01) contrast(1.02)}
.wm3-svc-hero,.wm3-svc-final{image-rendering:auto}
.wm3-svc-green__copy{grid-column:2;grid-row:1;min-width:0}
.wm3-svc-green__eyebrow{
  margin:0 0 .4rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
}
.wm3-svc-green__title{
  margin:0 0 .75rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.45rem,2.3vw,1.95rem)!important;font-weight:800!important;color:var(--wm-navy)!important;
}
.wm3-svc-green__text{margin:0;color:var(--wm-muted);font-size:1.02rem;line-height:1.6}
.wm3-svc-green__cards{
  grid-column:2;grid-row:2;list-style:none!important;margin:.15rem 0 0!important;padding:0!important;
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;align-items:stretch;width:100%;
  max-width:100%;box-sizing:border-box;
}
.wm3-svc-green__card{
  margin:0;padding:1.2rem 1.15rem 1.25rem;background:#fff;border-radius:var(--wm-radius,16px);
  border:1px solid rgba(15,23,42,.05);box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  display:flex;flex-direction:column;min-width:0;height:100%;box-sizing:border-box;
}
.wm3-svc-green__card-title{
  margin:0 0 .45rem;display:flex;flex-direction:column;align-items:flex-start;gap:.45rem;
  font-family:var(--wm-font-display);font-size:.98rem;font-weight:700;color:var(--wm-navy);
  line-height:1.25;hyphens:none;-webkit-hyphens:none;overflow-wrap:normal;word-break:normal;
}
.wm3-svc-green__card-title span{display:inline-block;max-width:100%}
.wm3-svc-green__card-title .wm3-ico{color:var(--wm-accent);flex-shrink:0}
.wm3-svc-green__card-desc{margin:0;font-size:.88rem;line-height:1.5;color:var(--wm-muted);flex:1}

/* ═══ Tax / Steuervorteil — premium benefit band (no photo) ═══ */
.wm3-svc-tax{
  background:linear-gradient(180deg,#fbfbfa 0%,#fff 42%,#fff 100%);
  padding:clamp(3.6rem,6.5vw,5.4rem) 0;
}
.wm3-svc-tax__inner{
  max-width:min(100%,1080px);margin:0 auto;padding:0 clamp(1.2rem,3.5vw,2rem);
}
.wm3-svc-tax__head{
  max-width:40rem;margin:0 auto 0;text-align:center;
}
.wm3-svc-tax__eyebrow{
  margin:0 0 1rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.75rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
}
.wm3-svc-tax__title{
  margin:0 0 1.35rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.55rem,2.7vw,2.2rem)!important;font-weight:800!important;
  color:var(--wm-navy)!important;line-height:1.18!important;letter-spacing:-.025em;
}
.wm3-svc-tax__lead{
  color:var(--wm-muted);font-size:1.02rem;line-height:1.7;
  margin:0 auto 2.5rem;max-width:36rem;
}
.wm3-svc-tax__lead p{margin:0 0 .85rem}
.wm3-svc-tax__lead p:last-child{margin-bottom:0}
.wm3-svc-tax__cards{
  list-style:none;margin:0 0 2.5rem;padding:0;width:100%;
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:clamp(1.1rem,2vw,1.5rem);
}
.wm3-svc-tax__card{
  display:flex;flex-direction:column;align-items:flex-start;gap:0;
  margin:0;padding:clamp(1.55rem,2.4vw,1.95rem) clamp(1.35rem,2vw,1.7rem) clamp(1.6rem,2.4vw,1.95rem);
  background:#fff;
  border:1px solid rgba(15,23,42,.045);
  border-radius:22px;
  box-shadow:0 12px 36px rgba(15,23,42,.04);
  transition:transform .35s cubic-bezier(.22,1,.36,1),box-shadow .35s ease;
}
.wm3-svc-tax__card:hover{
  transform:translateY(-3px);
  box-shadow:0 18px 44px rgba(15,23,42,.06);
}
.wm3-svc-tax__card-ico{
  width:2.75rem;height:2.75rem;border-radius:14px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  margin:0 0 1.15rem;
  background:linear-gradient(160deg,#fff7f0 0%,#ffeede 100%);
  color:var(--wm-accent);
  box-shadow:inset 0 0 0 1px rgba(232,93,4,.1);
}
.wm3-svc-tax__card-title{
  display:block;margin:0 0 .65rem;padding:0;
  font-family:var(--wm-font-display);font-size:1.02rem;
  font-weight:700;color:var(--wm-navy);line-height:1.3;letter-spacing:-.015em;
}
.wm3-svc-tax__card-desc{
  display:block;margin:0;padding:0;
  font-size:.92rem;line-height:1.6;color:var(--wm-muted);font-weight:500;
}
.wm3-svc-tax__highlight{
  margin:0 auto;padding:clamp(1.55rem,2.6vw,2rem) clamp(1.45rem,2.4vw,1.9rem);
  max-width:44rem;
  background:linear-gradient(155deg,#FFF8F1 0%,#FFEFE3 55%,#FFF6EE 100%);
  border:1px solid rgba(232,93,4,.14);
  border-radius:24px;
  box-shadow:0 16px 40px rgba(232,93,4,.08);
  display:flex;gap:1.1rem;align-items:flex-start;
}
.wm3-svc-tax__hl-ico{
  width:2.7rem;height:2.7rem;border-radius:14px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  background:rgba(255,255,255,.78);color:var(--wm-accent);
  box-shadow:inset 0 0 0 1px rgba(232,93,4,.12);
}
.wm3-svc-tax__hl-body{min-width:0;flex:1}
.wm3-svc-tax__hl-title{
  margin:0 0 .7rem;font-family:var(--wm-font-display);font-size:.78rem;
  font-weight:700;color:var(--wm-accent);letter-spacing:.12em;text-transform:uppercase;
}
.wm3-svc-tax__hl-text{
  margin:0;font-size:1.05rem;line-height:1.6;color:var(--wm-navy);font-weight:600;
  letter-spacing:-.01em;
}
.wm3-svc-tax__pct{
  font-weight:800;font-family:var(--wm-font-display);
  color:var(--wm-accent);letter-spacing:-.02em;
}
.wm3-svc-tax__hl-support{margin:.85rem 0 0;font-size:.92rem;line-height:1.55;color:var(--wm-muted)}
.wm3-svc-tax__actions{
  display:flex;justify-content:center;margin-top:2.35rem;
}
.wm3-svc-tax__cta{
  display:inline-flex;align-items:center;justify-content:center;gap:.5rem;
  margin:0;padding:1rem 1.7rem;
  border-radius:14px;background:var(--wm-accent);color:#fff!important;
  font-weight:700;font-size:1rem;text-decoration:none!important;
  box-shadow:0 10px 28px rgba(232,93,4,.28);
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),background var(--wm-ease);
}
.wm3-svc-tax__cta:hover{transform:translateY(-2px);background:var(--wm-accent-dark);box-shadow:0 14px 32px rgba(232,93,4,.34)}
.wm3-svc-tax__legal{
  margin:2.35rem auto 0;padding-top:1.35rem;
  border-top:1px solid rgba(15,23,42,.05);
  max-width:34rem;text-align:center;
}
.wm3-svc-tax__legal-label{
  margin:0 0 .45rem;font-size:.65rem;font-weight:600;letter-spacing:.12em;
  text-transform:uppercase;color:#c5cdd4;
}
.wm3-svc-tax__legal p{margin:0 0 .35rem;font-size:.74rem;line-height:1.55;color:#b8c2cb}
.wm3-svc-tax__legal p:last-child{margin-bottom:0}
@media(max-width:900px){
  .wm3-svc-tax__cards{grid-template-columns:1fr;gap:1rem;max-width:28rem;margin-left:auto;margin-right:auto}
}
@media(max-width:560px){
  .wm3-svc-tax{padding:3rem 0 2.75rem}
  .wm3-svc-tax__highlight{flex-direction:column;gap:.85rem;padding:1.35rem 1.2rem}
  .wm3-svc-tax__cta{width:100%;box-sizing:border-box}
}

.wm3-svc-areas{background:var(--wm-surface-alt,#eef2f7);padding:var(--wm-space-section,2.5rem) 0}
.wm3-svc-areas__inner{max-width:min(100%,1180px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem)}
.wm3-svc-areas__head{text-align:center;max-width:42rem;margin:0 auto 1.55rem}
.wm3-svc-areas__eyebrow{
  margin:0 0 .4rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
}
.wm3-svc-areas__title{
  margin:0 0 .55rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.45rem,2.3vw,1.95rem)!important;font-weight:800!important;color:var(--wm-navy)!important;
}
.wm3-svc-areas__sub{margin:0;color:var(--wm-muted);font-size:1.02rem;line-height:1.55}
.wm3-svc-areas__grid{
  list-style:none;margin:0;padding:0;
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;
}
.wm3-svc-areas__city{
  display:flex;align-items:center;gap:.55rem;margin:0;padding:.95rem 1rem;
  background:#fff;border:1px solid rgba(15,23,42,.05);border-radius:var(--wm-radius,16px);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  font-family:var(--wm-font-display);font-size:.98rem;font-weight:700;color:var(--wm-navy);
  transition:transform var(--wm-ease),border-color var(--wm-ease),box-shadow var(--wm-ease);
}
.wm3-svc-areas__city:hover{
  transform:translateY(-2px);border-color:rgba(232,93,4,.22);
  box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));
}
.wm3-svc-areas__city .wm3-ico{color:var(--wm-accent);flex-shrink:0}
.wm3-svc-areas__foot{
  margin:1.25rem 0 0;text-align:center;color:var(--wm-muted);font-size:.95rem;line-height:1.55;
}

.wm3-svc-faq{background:#fff;padding:var(--wm-space-section,2.5rem) 0}
.wm3-svc-faq__inner{max-width:min(100%,860px);margin:0 auto;padding:0 clamp(1.15rem,3.5vw,2rem)}
.wm3-svc-faq__head{text-align:center;margin:0 auto 1.55rem}
.wm3-svc-faq__eyebrow{
  margin:0 0 .4rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
}
.wm3-svc-faq__title{
  margin:0!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.45rem,2.3vw,1.95rem)!important;font-weight:800!important;color:var(--wm-navy)!important;
}
.wm3-svc-faq__item{
  margin:0 0 .65rem;border:1px solid rgba(15,23,42,.06);border-radius:var(--wm-radius,16px);
  background:#fff;box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));overflow:hidden;
}
.wm3-svc-faq__item[open]{border-color:rgba(232,93,4,.2)}
.wm3-svc-faq__q{
  cursor:pointer;list-style:none;padding:1.05rem 1.2rem;font-family:var(--wm-font-display);
  font-size:1.02rem;font-weight:700;color:var(--wm-navy);line-height:1.35;
  display:flex;align-items:center;justify-content:space-between;gap:1rem;
}
.wm3-svc-faq__q::-webkit-details-marker{display:none}
.wm3-svc-faq__q::after{
  content:"";width:.55rem;height:.55rem;border-right:2px solid var(--wm-accent);border-bottom:2px solid var(--wm-accent);
  transform:rotate(45deg);transition:transform var(--wm-ease);flex-shrink:0;margin-top:-.2rem;
}
.wm3-svc-faq__item[open] .wm3-svc-faq__q::after{transform:rotate(-135deg);margin-top:.15rem}
.wm3-svc-faq__a{
  margin:0;padding:0 1.2rem 1.15rem;color:var(--wm-muted);font-size:.98rem;line-height:1.65;
}

.wm3-svc-final{
  position:relative;isolation:isolate;overflow:hidden;
  padding:clamp(3rem,6vw,4.25rem) 0;color:#fff;text-align:center;
  background:#0b1f33 center/cover no-repeat;
}
.wm3-svc-final__shade{
  position:absolute;inset:0;z-index:0;pointer-events:none;
  background:linear-gradient(180deg,rgba(11,31,51,.72) 0%,rgba(11,31,51,.86) 100%);
}
.wm3-svc-final__inner{
  position:relative;z-index:1;max-width:min(100%,720px);margin:0 auto;
  padding:0 clamp(1.15rem,3.5vw,2rem);
}
.wm3-svc-final__title{
  margin:0 0 .75rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.65rem,3vw,2.35rem)!important;font-weight:800!important;
  color:#fff!important;line-height:1.15!important;letter-spacing:-.02em;
}
.wm3-svc-final__sub{margin:0 0 1.35rem;color:rgba(255,255,255,.88);font-size:1.05rem;line-height:1.55}
.wm3-svc-final__actions{display:flex;flex-wrap:wrap;gap:.75rem;justify-content:center;margin:0 0 1rem}
.wm3-svc-final__trust{margin:0;font-size:.9rem;color:rgba(255,255,255,.78);font-weight:500}

@media(max-width:980px){
  .wm3-svc-intro__inner,.wm3-svc-detail__inner,.wm3-svc-green__inner{grid-template-columns:1fr}
  .wm3-svc-detail--rtl .wm3-svc-detail__inner{direction:ltr}
  .wm3-svc-detail__media{
    position:relative;aspect-ratio:16/10;width:100%;min-height:14rem;
  }
  .wm3-svc-detail__img{
    position:absolute;inset:0;margin:auto;
    max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;
  }
  .wm3-svc-nav__grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wm3-svc-why__featured,.wm3-svc-why__support{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wm3-svc-areas__grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wm3-svc-green__media,.wm3-svc-green__copy,.wm3-svc-green__cards{grid-column:1;grid-row:auto}
  .wm3-svc-green__media{grid-row:auto;aspect-ratio:16/10;min-height:0;align-self:auto}
  .wm3-svc-green__cards{grid-template-columns:1fr;margin-top:0!important}
}
@media(max-width:560px){
  .wm3-svc-hero{min-height:0;align-items:center;padding:3.25rem 0 2.35rem}
  .wm3-svc-hero__actions{flex-direction:column;align-items:stretch}
  .wm3-svc-hero__btn{width:100%;justify-content:center}
  .wm3-svc-detail__badges{grid-template-columns:1fr}
  .wm3-svc-mod__stats{grid-template-columns:1fr}
  .wm3-svc-mod__grid{grid-template-columns:1fr}
  .wm3-svc-detail__cta{width:100%;justify-content:center}
  .wm3-svc-nav__grid,.wm3-svc-why__featured,.wm3-svc-why__support,.wm3-svc-areas__grid{grid-template-columns:1fr}
  .wm3-svc-final__actions{flex-direction:column;align-items:stretch}
}
"""


# ── Section renderers ─────────────────────────────────────────────────────────

def _hero_services(sec: dict) -> str:
    heading = (sec.get("heading") or "Wir übernehmen").strip()
    accent = (sec.get("heading_accent") or "Ihre Entrümpelung.").strip()
    tagline = (sec.get("tagline") or "").strip()
    sub = (sec.get("subheading") or "").strip()
    eyebrow = (sec.get("label") or sec.get("eyebrow") or "Ihr Unternehmen · Region").strip()
    image = _img_src(sec.get("image") or "")
    primary_label = (sec.get("cta_label") or "Kostenlose Besichtigung vereinbaren").strip()
    primary_url = (sec.get("cta_url") or _CONTACT).strip()
    secondary_label = (sec.get("cta_secondary_label") or "Leistungen ansehen").strip()
    secondary_url = (sec.get("cta_secondary_url") or "#leistungen-uebersicht").strip()
    usps = sec.get("usps") or sec.get("badges") or []

    sub_html = escape(sub)
    for term in ("Festpreis", "besenreine Übergabe", "diskret", "termintreu"):
        if term in sub_html:
            sub_html = sub_html.replace(term, f"<strong>{term}</strong>", 1)

    title_html = escape(heading)
    if accent:
        title_html = f'{escape(heading)} <span class="wm3-svc-hero__title-accent">{escape(accent)}</span>'

    usp_bits: list[str] = []
    for idx, raw in enumerate(usps[:4]):
        if isinstance(raw, dict):
            title = (raw.get("title") or raw.get("label") or "").strip()
            desc = (raw.get("description") or raw.get("text") or "").strip()
            ico = (raw.get("icon") or "").strip()
        else:
            title = str(raw).strip()
            desc = ""
            ico = ""
        if not title:
            continue
        svg = icon(ico, 18) if ico else icon_for_label(title, idx, 18)
        usp_bits.append(
            f'<li class="wm3-svc-hero__usp">'
            f'<span class="wm3-svc-hero__usp-ico" aria-hidden="true">{svg}</span>'
            f'<span><span class="wm3-svc-hero__usp-title">{escape(title)}</span>'
            + (f'<span class="wm3-svc-hero__usp-desc">{escape(desc)}</span>' if desc else "")
            + "</span></li>"
        )

    bg = f' style="background-image:url(\'{escape(image, quote=True)}\')"' if image else ""
    html = (
        f'<section class="wm3-svc-hero" aria-label="{escape((heading + " " + accent).strip())}"{bg}>'
        '<div class="wm3-svc-hero__shade" aria-hidden="true"></div>'
        '<div class="wm3-svc-hero__inner">'
        '<div class="wm3-svc-hero__copy">'
        f'<p class="wm3-svc-hero__eyebrow">{escape(eyebrow)}</p>'
        f'<h1 class="wm3-svc-hero__title">{title_html}</h1>'
        + (f'<p class="wm3-svc-hero__tagline">{escape(tagline)}</p>' if tagline else "")
        + (f'<p class="wm3-svc-hero__sub">{sub_html}</p>' if sub else "")
        + '<div class="wm3-svc-hero__actions">'
        f'<a class="wm3-svc-hero__btn wm3-svc-hero__btn--primary" href="{escape(primary_url, quote=True)}">'
        f'{escape(primary_label)}{icon("arrow", 18)}</a>'
        f'<a class="wm3-svc-hero__btn wm3-svc-hero__btn--ghost" href="{escape(secondary_url, quote=True)}">'
        f"{escape(secondary_label)}</a>"
        "</div>"
        + (
            f'<ul class="wm3-svc-hero__usps" aria-label="Ihre Vorteile">{"".join(usp_bits)}</ul>'
            if usp_bits
            else ""
        )
        + "</div></div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _intro_split(sec: dict) -> str:
    label = (sec.get("label") or "Über unsere Arbeit").strip()
    title = (sec.get("heading") or "Was Sie von uns erwarten können").strip()
    paragraphs = sec.get("paragraphs") or []
    if not paragraphs and (sec.get("text") or "").strip():
        paragraphs = [p.strip() for p in str(sec["text"]).split("\n\n") if p.strip()][:3]
    checks = sec.get("checks") or sec.get("items") or []
    card_title = (sec.get("card_title") or "In jedem Auftrag enthalten").strip()

    paras = "".join(f"<p>{escape(str(p))}</p>" for p in paragraphs[:4] if str(p).strip())
    checks_html = "".join(
        f"<li>{icon('check', 20)}<span>{escape(str(it.get('title') if isinstance(it, dict) else it))}</span></li>"
        for it in checks
        if (it.get("title") if isinstance(it, dict) else it)
    )
    html = (
        '<section class="wm3-section wm3-svc-intro" aria-labelledby="wm3-svc-intro-title">'
        '<div class="wm3-svc-intro__inner">'
        '<div class="wm3-svc-intro__copy">'
        f'<p class="wm3-svc-intro__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-intro-title" class="wm3-svc-intro__title">{escape(title)}</h2>'
        f"{paras}"
        "</div>"
        '<aside class="wm3-svc-intro__card">'
        f'<p class="wm3-svc-intro__card-title">{escape(card_title)}</p>'
        f'<ul class="wm3-svc-intro__list">{checks_html}</ul>'
        "</aside>"
        "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _service_nav(sec: dict) -> str:
    label = (sec.get("label") or "Leistungsübersicht").strip()
    title = (sec.get("heading") or "Finden Sie Ihren Service").strip()
    items = sec.get("items") or []
    cards = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        name = (it.get("title") or "").strip()
        if not name:
            continue
        href = (it.get("url") or f"#{_slugify(name)}").strip()
        hint = (it.get("description") or it.get("hint") or "").strip()
        cards.append(
            f'<li><a class="wm3-svc-nav__card" href="{escape(href, quote=True)}">'
            f'<span class="wm3-svc-nav__icon" aria-hidden="true">{icon_for_label(name, idx, 22)}</span>'
            f'<span class="wm3-svc-nav__label">{escape(name)}</span>'
            + (f'<p class="wm3-svc-nav__hint">{escape(hint)}</p>' if hint else "")
            + "</a></li>"
        )
    html = (
        '<section id="leistungen-uebersicht" class="wm3-section wm3-svc-nav" aria-labelledby="wm3-svc-nav-title">'
        '<div class="wm3-svc-nav__inner">'
        '<header class="wm3-svc-nav__head">'
        f'<p class="wm3-svc-nav__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-nav-title" class="wm3-svc-nav__title">{escape(title)}</h2>'
        "</header>"
        f'<ul class="wm3-svc-nav__grid">{"".join(cards)}</ul>'
        "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _chip_icon(label: str) -> str:
    low = (label or "").lower()
    if any(k in low for k in ("haus", "einfamilien", "villa", "dachboden")):
        return icon("home", 16)
    if any(k in low for k in ("wohnung", "mieter", "vermiet", "verkauf", "erbe", "pflegeheim")):
        return icon("door", 16)
    if any(k in low for k in ("keller", "dach", "speicher", "garage", "abstell")):
        return icon("box", 16)
    if any(k in low for k in ("gewerb", "büro", "buero", "laden", "lager", "firma", "hotel", "gast", "werkstatt", "halle")):
        return icon("building", 16)
    if any(k in low for k in ("garten", "außen", "aussen", "hecke", "rasen", "grundstück", "grundstueck")):
        return icon("leaf", 16)
    if any(k in low for k in ("akte", "datenschutz", "dsgvo", "archiv", "scan", "digital")):
        return icon("shield", 16)
    if any(k in low for k in ("sperr", "müll", "muell", "möbel", "moebel", "gerät", "geraet", "renov")):
        return icon("truck", 16)
    if any(k in low for k in ("übergabe", "uebergabe", "streich", "renovier", "boden")):
        return icon("spark", 16)
    return icon_for_label(label, 0, 16)


def _mod_items(mod: dict) -> list[dict]:
    out: list[dict] = []
    for raw in mod.get("items") or []:
        if isinstance(raw, dict):
            title = (raw.get("title") or raw.get("label") or raw.get("value") or "").strip()
            desc = (raw.get("description") or raw.get("text") or raw.get("hint") or "").strip()
            ico = (raw.get("icon") or "").strip()
            if title:
                out.append({"title": title, "description": desc, "icon": ico})
        elif str(raw).strip():
            out.append({"title": str(raw).strip(), "description": "", "icon": ""})
    return out


def _render_module(mod: dict) -> str:
    if not isinstance(mod, dict):
        return ""
    kind = (mod.get("type") or "").lower().strip()
    title = (mod.get("title") or mod.get("label") or "").strip()
    items = _mod_items(mod)
    label_html = f'<p class="wm3-svc-mod__label">{escape(title)}</p>' if title else ""

    if kind in ("accordion", "more", "seo"):
        paras = [str(p).strip() for p in (mod.get("paragraphs") or []) if str(p).strip()]
        if isinstance(mod.get("text"), str) and mod["text"].strip():
            paras = [mod["text"].strip()] + paras
        bullets = [str(x).strip() for x in (mod.get("bullets") or mod.get("list") or []) if str(x).strip()]
        body: list[str] = [f"<p>{escape(p)}</p>" for p in paras[:4]]
        if bullets:
            body.append("<ul>" + "".join(f"<li>{escape(b)}</li>" for b in bullets[:8]) + "</ul>")
        if not body:
            return ""
        summary = title or "Weitere Informationen"
        return (
            '<details class="wm3-svc-detail__more">'
            f'<summary class="wm3-svc-detail__more-q">{escape(summary)}</summary>'
            f'<div class="wm3-svc-detail__more-body">{"".join(body)}</div>'
            "</details>"
        )

    if not items and kind not in ("stats",):
        return ""

    if kind in ("icon_grid", "features", "feature_cards", "cards"):
        cards = []
        for idx, it in enumerate(items[:6]):
            ico_name = it["icon"]
            svg = icon(ico_name, 18) if ico_name else icon_for_label(it["title"], idx, 18)
            cards.append(
                f'<li class="wm3-svc-mod__card">'
                f'<span class="wm3-svc-mod__card-ico" aria-hidden="true">{svg}</span>'
                f'<span><span class="wm3-svc-mod__card-title">{escape(it["title"])}</span>'
                + (f'<p class="wm3-svc-mod__card-desc">{escape(it["description"])}</p>' if it["description"] else "")
                + "</span></li>"
            )
        return (
            f'<div class="wm3-svc-mod">{label_html}'
            f'<ul class="wm3-svc-mod__grid">{"".join(cards)}</ul></div>'
        )

    if kind in ("chips", "tags"):
        chip_bits = []
        for it in items[:8]:
            chip_bits.append(
                f'<li class="wm3-svc-mod__chip">{_chip_icon(it["title"])}'
                f'<span>{escape(it["title"])}</span></li>'
            )
        return (
            f'<div class="wm3-svc-mod">{label_html}'
            f'<ul class="wm3-svc-mod__chips">{"".join(chip_bits)}</ul></div>'
        )

    if kind in ("checklist", "checks", "why", "benefits"):
        lis = "".join(
            f"<li>{icon('check', 16)}<span>{escape(it['title'])}</span></li>"
            for it in items[:5]
        )
        return f'<div class="wm3-svc-mod">{label_html}<ul class="wm3-svc-mod__check">{lis}</ul></div>'

    if kind in ("stats", "statistics"):
        stats = []
        for it in items[:4]:
            stats.append(
                f'<li class="wm3-svc-mod__stat">'
                f'<span class="wm3-svc-mod__stat-val">{escape(it["title"])}</span>'
                + (f'<span class="wm3-svc-mod__stat-lbl">{escape(it["description"])}</span>' if it["description"] else "")
                + "</li>"
            )
        if not stats:
            return ""
        return f'<div class="wm3-svc-mod">{label_html}<ul class="wm3-svc-mod__stats">{"".join(stats)}</ul></div>'

    if kind in ("process", "steps", "workflow"):
        steps = "".join(
            f'<li class="wm3-svc-mod__step"><span>{escape(it["title"])}</span></li>'
            for it in items[:5]
        )
        return f'<div class="wm3-svc-mod">{label_html}<ol class="wm3-svc-mod__steps">{steps}</ol></div>'

    # fallback: checklist
    lis = "".join(
        f"<li>{icon('check', 16)}<span>{escape(it['title'])}</span></li>" for it in items[:5]
    )
    return f'<div class="wm3-svc-mod">{label_html}<ul class="wm3-svc-mod__check">{lis}</ul></div>'


def _service_details(sec: dict) -> str:
    items = sec.get("items") or []
    blocks: list[str] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        sid = (it.get("id") or _slugify(title)).strip()
        intro = (it.get("intro") or it.get("description") or "").strip()
        modules = it.get("modules") or []
        image = _img_src(it.get("image") or "")
        alt = (it.get("alt") or title).strip()
        cta_label = (it.get("cta_label") or "Kostenlose Besichtigung").strip()
        cta_url = (it.get("cta_url") or _CONTACT).strip()
        rtl = idx % 2 == 1
        cls = "wm3-svc-detail wm3-svc-detail--rtl" if rtl else "wm3-svc-detail"
        if idx % 2 == 1:
            cls += " wm3-svc-detail--alt"

        modules_html = "".join(_render_module(m) for m in modules if isinstance(m, dict))

        # Legacy fallback if no modules defined
        if not modules_html:
            included = [str(x).strip() for x in (it.get("included") or []) if str(x).strip()]
            process = (it.get("process") or "").strip()
            chips = it.get("chips") or it.get("situations") or []
            trust = it.get("trust") or it.get("why") or it.get("benefits") or []
            legacy_mods: list[dict] = []
            if chips:
                legacy_mods.append({"type": "chips", "title": "Einsatzbereiche", "items": chips})
            if trust:
                legacy_mods.append({"type": "checklist", "title": "Warum wir?", "items": trust})
            more: dict = {"type": "accordion", "title": "Weitere Informationen"}
            if process:
                more["paragraphs"] = [process]
            if included:
                more["bullets"] = included
            legacy_mods.append(more)
            modules_html = "".join(_render_module(m) for m in legacy_mods)

        media = ""
        if image:
            media = (
                f'<figure class="wm3-svc-detail__media">'
                f'<img class="wm3-svc-detail__img" src="{escape(image, quote=True)}" '
                f'alt="{escape(alt)}" loading="lazy" decoding="async" /></figure>'
            )
        copy = (
            '<div class="wm3-svc-detail__copy">'
            f'<h2 id="{escape(sid, quote=True)}" class="wm3-svc-detail__title">{escape(title)}</h2>'
            + (f'<p class="wm3-svc-detail__intro">{escape(intro)}</p>' if intro else "")
            + modules_html
            + f'<a class="wm3-svc-detail__cta" href="{escape(cta_url, quote=True)}">{escape(cta_label)}</a>'
            + "</div>"
        )
        blocks.append(
            f'<section class="{cls}" aria-labelledby="{escape(sid, quote=True)}">'
            f'<div class="wm3-svc-detail__inner">{media}{copy}</div></section>'
        )
    return "\n".join(f"<!-- wp:html -->\n{b}\n<!-- /wp:html -->" for b in blocks)


def _benefits_grid(sec: dict) -> str:
    label = (sec.get("label") or "Warum wir").strip()
    title = (sec.get("heading") or "Warum Kunden uns wählen").strip()
    lead = (sec.get("subheading") or "").strip()
    items = [it for it in (sec.get("items") or []) if isinstance(it, dict)]
    featured = [it for it in items if it.get("featured")]
    support = [it for it in items if not it.get("featured")]
    # Fallback: first four as featured when no flags are set.
    if not featured and items:
        featured, support = items[:4], items[4:]

    feat_cards: list[str] = []
    for idx, it in enumerate(featured[:4]):
        t = (it.get("title") or "").strip()
        d = (it.get("description") or "").strip()
        if not t:
            continue
        ico = it.get("icon")
        svg = icon(str(ico), 22) if ico else icon_for_label(t, idx, 22)
        feat_cards.append(
            f'<li class="wm3-svc-why__card wm3-svc-why__card--featured">'
            f'<div class="wm3-svc-why__icon" aria-hidden="true">{svg}</div>'
            f'<h3 class="wm3-svc-why__card-title">{escape(t)}</h3>'
            + (f'<p class="wm3-svc-why__card-desc">{escape(d)}</p>' if d else "")
            + "</li>"
        )

    support_cards: list[str] = []
    for idx, it in enumerate(support[:4]):
        t = (it.get("title") or "").strip()
        d = (it.get("description") or "").strip()
        if not t:
            continue
        ico = it.get("icon")
        svg = icon(str(ico), 18) if ico else icon_for_label(t, idx + 4, 18)
        support_cards.append(
            f'<li class="wm3-svc-why__support-item">'
            f'<span class="wm3-svc-why__support-ico" aria-hidden="true">{svg}</span>'
            f'<span><h3 class="wm3-svc-why__support-title">{escape(t)}</h3>'
            + (f'<p class="wm3-svc-why__support-desc">{escape(d)}</p>' if d else "")
            + "</span></li>"
        )

    html = (
        '<section class="wm3-section wm3-svc-why" aria-labelledby="wm3-svc-why-title">'
        '<div class="wm3-svc-why__inner">'
        '<header class="wm3-svc-why__head">'
        f'<p class="wm3-svc-why__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-why-title" class="wm3-svc-why__title">{escape(title)}</h2>'
        + (f'<p class="wm3-svc-why__lead">{escape(lead)}</p>' if lead else "")
        + "</header>"
        + (f'<ul class="wm3-svc-why__featured">{"".join(feat_cards)}</ul>' if feat_cards else "")
        + (
            f'<ul class="wm3-svc-why__support" aria-label="Weitere Vorteile">{"".join(support_cards)}</ul>'
            if support_cards
            else ""
        )
        + "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _sustainability(sec: dict) -> str:
    label = (sec.get("label") or "Nachhaltigkeit").strip()
    title = (sec.get("heading") or "Verantwortung für Umwelt und Ressourcen").strip()
    text = (sec.get("text") or "").strip()
    image = _img_src(sec.get("image") or "")
    alt = (sec.get("alt") or "Nachhaltige Entsorgung und Recycling").strip()
    cards = sec.get("cards") or sec.get("items") or []

    media = ""
    if image:
        media = (
            f'<figure class="wm3-svc-green__media">'
            f'<img class="wm3-svc-green__img" src="{escape(image, quote=True)}" '
            f'alt="{escape(alt)}" loading="lazy" decoding="async" /></figure>'
        )
    card_bits = []
    for idx, it in enumerate(cards):
        if not isinstance(it, dict):
            continue
        t = (it.get("title") or "").strip()
        d = (it.get("description") or "").strip()
        if not t:
            continue
        card_bits.append(
            f'<li class="wm3-svc-green__card">'
            f'<h3 class="wm3-svc-green__card-title">{icon_for_label(t, idx, 18)}'
            f"<span>{escape(t)}</span></h3>"
            + (f'<p class="wm3-svc-green__card-desc">{escape(d)}</p>' if d else "")
            + "</li>"
        )
    html = (
        '<section class="wm3-section wm3-svc-green" aria-labelledby="wm3-svc-green-title">'
        '<div class="wm3-svc-green__inner">'
        f"{media}"
        '<div class="wm3-svc-green__copy">'
        f'<p class="wm3-svc-green__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-green-title" class="wm3-svc-green__title">{escape(title)}</h2>'
        + (f'<p class="wm3-svc-green__text">{escape(text)}</p>' if text else "")
        + "</div>"
        + (f'<ul class="wm3-svc-green__cards">{"".join(card_bits)}</ul>' if card_bits else "")
        + "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _tax_info(sec: dict) -> str:
    label   = (sec.get("label") or "Gut zu wissen").strip()
    title   = (sec.get("heading") or "Steuern sparen?\nIhre Entrümpelung kann\nsteuerlich absetzbar sein.").strip()
    title_html = "<br>".join(escape(line) for line in title.split("\n") if line.strip())

    # Lead paragraphs
    paragraphs = sec.get("paragraphs") or []
    if not paragraphs and (sec.get("text") or "").strip():
        paragraphs = [p.strip() for p in str(sec["text"]).split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [
            "Viele Kunden wissen nicht, dass Entrümpelungskosten unter bestimmten "
            "Voraussetzungen steuerlich berücksichtigt werden können.",
            "Wir sorgen dafür, dass Sie dafür eine vollständige und ordnungsgemäße Rechnung erhalten.",
        ]
    lead = "".join(f"<p>{escape(str(p))}</p>" for p in paragraphs[:3] if str(p).strip())

    # Feature cards (icon → title → description, stacked)
    cards = sec.get("cards") or sec.get("features") or []
    if not cards:
        cards = [
            {
                "icon": "check",
                "title": "Ordnungsgemäße Rechnung",
                "description": "Wir erstellen alle notwendigen Angaben übersichtlich und vollständig.",
            },
            {
                "icon": "folder",
                "title": "Einfach für Ihre Steuerunterlagen",
                "description": "Sie erhalten eine klare Rechnung, die Sie direkt verwenden können.",
            },
            {
                "icon": "shield",
                "title": "Weniger Aufwand für Sie",
                "description": "Wir kümmern uns um die korrekte Dokumentation.",
            },
        ]
    card_bits = []
    for idx, c in enumerate(cards[:3]):
        if isinstance(c, dict):
            ct  = (c.get("title") or c.get("label") or "").strip()
            cd  = (c.get("description") or c.get("text") or "").strip()
            ico = (c.get("icon") or "").strip()
        else:
            ct, cd, ico = str(c).strip(), "", ""
        if not ct:
            continue
        svg = icon(ico, 20) if ico else icon_for_label(ct, idx, 20)
        card_bits.append(
            f'<li class="wm3-svc-tax__card">'
            f'<span class="wm3-svc-tax__card-ico" aria-hidden="true">{svg}</span>'
            f'<strong class="wm3-svc-tax__card-title">{escape(ct)}</strong>'
            + (f'<p class="wm3-svc-tax__card-desc">{escape(cd)}</p>' if cd else "")
            + "</li>"
        )

    # Highlight box — emphasize "20 %"
    highlight = sec.get("highlight") or {}
    if isinstance(highlight, str):
        highlight = {"text": highlight}
    hl_kicker  = (highlight.get("kicker") or "Gut zu wissen").strip()
    hl_title   = (highlight.get("title") or "Unter bestimmten Voraussetzungen können 20 % der Arbeitskosten steuerlich berücksichtigt werden.").strip()
    hl_support = (highlight.get("support") or "Wir sorgen dafür, dass Sie alle notwendigen Unterlagen erhalten.").strip()
    hl_title_html = re.sub(
        r"(20\s*%)",
        r'<strong class="wm3-svc-tax__pct">\1</strong>',
        escape(hl_title),
    )

    # CTA
    cta_url  = (sec.get("cta_url") or "#contact").strip()
    cta_text = (sec.get("cta_text") or "Kostenlose Besichtigung vereinbaren").strip()

    # Legal notice
    legal = sec.get("legal") or sec.get("note") or ""
    legal_paras: list[str] = []
    if isinstance(legal, dict):
        legal_paras = [str(p).strip() for p in (legal.get("paragraphs") or []) if str(p).strip()]
        if legal.get("text"):
            legal_paras = [str(legal["text"]).strip()] + legal_paras
    elif isinstance(legal, list):
        legal_paras = [str(p).strip() for p in legal if str(p).strip()]
    elif str(legal).strip():
        legal_paras = [str(legal).strip()]
    if not legal_paras:
        legal_paras = [
            "Ob eine steuerliche Berücksichtigung möglich ist, hängt von Ihrer persönlichen Situation ab.",
            "Bitte wenden Sie sich bei Fragen an Ihren Steuerberater.",
        ]

    legal_html = (
        '<footer class="wm3-svc-tax__legal">'
        '<p class="wm3-svc-tax__legal-label">Hinweis</p>'
        + "".join(f"<p>{escape(p)}</p>" for p in legal_paras[:2])
        + "</footer>"
    )

    html = (
        '<section class="wm3-section wm3-svc-tax" aria-labelledby="wm3-svc-tax-title">'
        '<div class="wm3-svc-tax__inner">'
        '<header class="wm3-svc-tax__head">'
        f'<p class="wm3-svc-tax__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-tax-title" class="wm3-svc-tax__title">{title_html}</h2>'
        + (f'<div class="wm3-svc-tax__lead">{lead}</div>' if lead else "")
        + "</header>"
        + (f'<ul class="wm3-svc-tax__cards">{"".join(card_bits)}</ul>' if card_bits else "")
        + '<aside class="wm3-svc-tax__highlight">'
        + f'<span class="wm3-svc-tax__hl-ico" aria-hidden="true">{icon("tag", 22)}</span>'
        + '<div class="wm3-svc-tax__hl-body">'
        + f'<p class="wm3-svc-tax__hl-title">{escape(hl_kicker)}</p>'
        + f'<p class="wm3-svc-tax__hl-text">{hl_title_html}</p>'
        + (f'<p class="wm3-svc-tax__hl-support">{escape(hl_support)}</p>' if hl_support else "")
        + "</div></aside>"
        + '<div class="wm3-svc-tax__actions">'
        + f'<a href="{escape(cta_url, quote=True)}" class="wm3-svc-tax__cta">'
        + f'{icon("arrow", 18)}<span>{escape(cta_text)}</span></a>'
        + "</div>"
        + legal_html
        + "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _service_areas(sec: dict) -> str:
    label = (sec.get("label") or "Einsatzgebiete").strip()
    title = (sec.get("heading") or "Entrümpelung in Siegen und Umgebung").strip()
    sub = (sec.get("subheading") or "").strip()
    foot = (sec.get("footer") or "").strip()
    cities = sec.get("cities") or sec.get("items") or []

    city_bits = []
    for c in cities:
        name = (c.get("title") if isinstance(c, dict) else c) or ""
        name = str(name).strip()
        if not name:
            continue
        city_bits.append(
            f'<li class="wm3-svc-areas__city">{icon("pin", 18)}'
            f"<span>{escape(name)}</span></li>"
        )
    html = (
        '<section class="wm3-section wm3-svc-areas" aria-labelledby="wm3-svc-areas-title">'
        '<div class="wm3-svc-areas__inner">'
        '<header class="wm3-svc-areas__head">'
        f'<p class="wm3-svc-areas__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-areas-title" class="wm3-svc-areas__title">{escape(title)}</h2>'
        + (f'<p class="wm3-svc-areas__sub">{escape(sub)}</p>' if sub else "")
        + "</header>"
        f'<ul class="wm3-svc-areas__grid">{"".join(city_bits)}</ul>'
        + (f'<p class="wm3-svc-areas__foot">{escape(foot)}</p>' if foot else "")
        + "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _faq_accordion(sec: dict) -> str:
    label = (sec.get("label") or "FAQ").strip()
    title = (sec.get("heading") or "Häufige Fragen zu unseren Leistungen").strip()
    items = sec.get("items") or []
    bits = []
    for it in items:
        if not isinstance(it, dict):
            continue
        q = (it.get("question") or it.get("title") or "").strip()
        a = (it.get("answer") or it.get("text") or "").strip()
        if not q or not a:
            continue
        bits.append(
            f'<details class="wm3-svc-faq__item">'
            f'<summary class="wm3-svc-faq__q">{escape(q)}</summary>'
            f'<p class="wm3-svc-faq__a">{escape(a)}</p>'
            f"</details>"
        )
    html = (
        '<section class="wm3-section wm3-svc-faq" aria-labelledby="wm3-svc-faq-title">'
        '<div class="wm3-svc-faq__inner">'
        '<header class="wm3-svc-faq__head">'
        f'<p class="wm3-svc-faq__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-svc-faq-title" class="wm3-svc-faq__title">{escape(title)}</h2>'
        "</header>"
        f"{''.join(bits)}"
        "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _final_cta(sec: dict) -> str:
    title = (sec.get("heading") or "Jetzt kostenlose Besichtigung vereinbaren").strip()
    sub = (sec.get("subheading") or "").strip()
    image = _img_src(sec.get("image") or "")
    cta_label = (sec.get("cta_label") or "Jetzt anfragen").strip()
    cta_url = (sec.get("cta_url") or _CONTACT).strip()
    phone = (sec.get("phone") or _PHONE_DISPLAY).strip()
    tel = "tel:" + re.sub(r"[^\d+]", "", phone) if phone else _PHONE_TEL
    trust = (sec.get("trust_line") or "Festpreis · Versichert · Besenreine Übergabe").strip()

    bg = f' style="background-image:url(\'{escape(image, quote=True)}\')"' if image else ""
    html = (
        f'<section class="wm3-svc-final" aria-labelledby="wm3-svc-final-title"{bg}>'
        '<div class="wm3-svc-final__shade" aria-hidden="true"></div>'
        '<div class="wm3-svc-final__inner">'
        f'<h2 id="wm3-svc-final-title" class="wm3-svc-final__title">{escape(title)}</h2>'
        + (f'<p class="wm3-svc-final__sub">{escape(sub)}</p>' if sub else "")
        + '<div class="wm3-svc-final__actions">'
        f'<a class="wm3-svc-hero__btn wm3-svc-hero__btn--primary" href="{escape(cta_url, quote=True)}">'
        f"{escape(cta_label)}</a>"
        f'<a class="wm3-svc-hero__btn wm3-svc-hero__btn--ghost" href="{escape(tel, quote=True)}">'
        f'{icon("phone", 18)}<span>{escape(phone)}</span></a>'
        "</div>"
        f'<p class="wm3-svc-final__trust">{escape(trust)}</p>'
        "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def collect_services_faq_items(sec_or_page: dict[str, Any]) -> list[dict]:
    """Extract FAQ Q&A from a services page section or full page dict."""
    sections = sec_or_page.get("sections")
    if isinstance(sections, list):
        items_src: list = []
        for sec in sections:
            if (sec.get("type") or "").lower() == "faq":
                items_src.extend(sec.get("items") or [])
    else:
        items_src = sec_or_page.get("items") or []
    out: list[dict] = []
    for it in items_src:
        if not isinstance(it, dict):
            continue
        q = str(it.get("question") or it.get("title") or "").strip()
        a = str(it.get("answer") or it.get("text") or "").strip()
        if q and a:
            out.append({"question": q, "answer": a})
    return out
