"""
webmaker.data.theme_catalog
============================
Curated catalog of 5 SEO-friendly WordPress themes and their starter templates.

Each entry describes:
- ``id``          — internal identifier (used as WP slug for install)
- ``name``        — display name
- ``description`` — short pitch
- ``seo``         — SEO rating label
- ``wp_slug``     — slug passed to ``wp theme install``
- ``plugins``     — list of WP plugin slugs to install alongside the theme
- ``templates``   — list of starter templates for this theme

Each template entry:
- ``id``          — unique slug passed to the import mechanism
- ``name``        — display label
- ``preview_url`` — live demo URL opened in the browser for preview
- ``tags``        — keywords that hint at best-fit business types
"""

from __future__ import annotations

from typing import TypedDict


class TemplateEntry(TypedDict):
    id: str
    name: str
    preview_url: str
    tags: list[str]


class ThemeEntry(TypedDict):
    id: str
    name: str
    description: str
    seo: str
    wp_slug: str
    plugins: list[str]
    templates: list[TemplateEntry]


THEMES: list[ThemeEntry] = [
    {
        "id": "kadence",
        "name": "Kadence",
        "description": (
            "Lightweight, highly customisable block theme with a massive free "
            "starter template library. Outstanding Core Web Vitals scores and "
            "native Gutenberg blocks make it a top SEO choice."
        ),
        "seo": "Excellent ★★★★★",
        "wp_slug": "kadence",
        "plugins": ["kadence-blocks", "kadence-starter-templates"],
        "templates": [
            {
                "id": "home-services",
                "name": "Home Services",
                "preview_url": "https://homedepot.kadencewp.com/",
                "tags": ["cleaning", "maintenance", "services", "local"],
            },
            {
                "id": "construction",
                "name": "Construction",
                "preview_url": "https://construction.kadencewp.com/",
                "tags": ["construction", "renovations", "trades"],
            },
            {
                "id": "local-business",
                "name": "Local Business",
                "preview_url": "https://localbusiness.kadencewp.com/",
                "tags": ["local", "services", "professional"],
            },
            {
                "id": "restoration",
                "name": "Restoration / Entrümpelung",
                "preview_url": "https://restoration.kadencewp.com/",
                "tags": ["hausentrümpelung", "clearance", "removal", "junk"],
            },
        ],
    },
    {
        "id": "astra",
        "name": "Astra",
        "description": (
            "Ultra-fast (< 50 KB) classic + FSE hybrid theme. Works with "
            "Elementor, Beaver Builder, and Gutenberg. 200+ free starter sites "
            "including several service / cleaning niches."
        ),
        "seo": "Excellent ★★★★★",
        "wp_slug": "astra",
        "plugins": ["astra-sites"],
        "templates": [
            {
                "id": "house-cleaning",
                "name": "House Cleaning",
                "preview_url": "https://websitedemos.net/house-cleaning-02/",
                "tags": ["cleaning", "maid", "haushaltsreinigung"],
            },
            {
                "id": "pest-control",
                "name": "Pest Control / Property Services",
                "preview_url": "https://websitedemos.net/pest-control-02/",
                "tags": ["pest", "property", "local services"],
            },
            {
                "id": "handyman",
                "name": "Handyman / Home Repair",
                "preview_url": "https://websitedemos.net/handyman-02/",
                "tags": ["handyman", "repair", "home services"],
            },
        ],
    },
    {
        "id": "generatepress",
        "name": "GeneratePress",
        "description": (
            "Minimal, developer-friendly theme with < 10 KB base size. "
            "Pairs with GenerateBlocks for pixel-perfect layouts. "
            "Consistently achieves perfect Lighthouse scores."
        ),
        "seo": "Excellent ★★★★★",
        "wp_slug": "generatepress",
        "plugins": ["generateblocks"],
        "templates": [
            {
                "id": "small-business",
                "name": "Small Business",
                "preview_url": "https://generatepress.com/categories/business/",
                "tags": ["small business", "services", "corporate"],
            },
            {
                "id": "services-agency",
                "name": "Services Agency",
                "preview_url": "https://generatepress.com/categories/agency/",
                "tags": ["agency", "services", "professional"],
            },
        ],
    },
    {
        "id": "oceanwp",
        "name": "OceanWP",
        "description": (
            "Feature-rich free theme with dedicated Extensions for header, "
            "footer, and WooCommerce. Good out-of-the-box performance and a "
            "growing library of 200+ free demos."
        ),
        "seo": "Good ★★★★☆",
        "wp_slug": "oceanwp",
        "plugins": ["ocean-extra"],
        "templates": [
            {
                "id": "cleaning-service",
                "name": "Cleaning Service",
                "preview_url": "https://demos.oceanwp.org/cleaning-service/",
                "tags": ["cleaning", "residential", "commercial"],
            },
            {
                "id": "business",
                "name": "Business Pro",
                "preview_url": "https://demos.oceanwp.org/business-pro/",
                "tags": ["business", "services", "local"],
            },
        ],
    },
    {
        "id": "blocksy",
        "name": "Blocksy",
        "description": (
            "Modern, FSE-ready theme with an advanced customiser. Excellent "
            "performance and unique features like dynamic content blocks and "
            "offcanvas menus. Great choice for modern-looking service sites."
        ),
        "seo": "Very Good ★★★★☆",
        "wp_slug": "blocksy",
        "plugins": ["blocksy-companion"],
        "templates": [
            {
                "id": "services-modern",
                "name": "Services (Modern)",
                "preview_url": "https://creativethemes.com/blocksy/demos/business/",
                "tags": ["services", "modern", "corporate"],
            },
            {
                "id": "local-pro",
                "name": "Local Pro",
                "preview_url": "https://creativethemes.com/blocksy/demos/local/",
                "tags": ["local", "services", "small business"],
            },
        ],
    },
]

# Convenience lookup
THEME_BY_ID: dict[str, ThemeEntry] = {t["id"]: t for t in THEMES}


def get_theme(theme_id: str) -> ThemeEntry | None:
    """Return a theme entry by its ``id``, or ``None`` if not found."""
    return THEME_BY_ID.get(theme_id)


def get_template(theme_id: str, template_id: str) -> TemplateEntry | None:
    """Return a template entry, or ``None`` if not found."""
    theme = THEME_BY_ID.get(theme_id)
    if not theme:
        return None
    for t in theme["templates"]:
        if t["id"] == template_id:
            return t
    return None
