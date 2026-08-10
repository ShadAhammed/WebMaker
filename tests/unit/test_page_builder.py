from webmaker.agents.website_modernizer.design_system import DesignTokens
from webmaker.agents.website_modernizer.page_builder import (
    _global_css,
    _section_before_after,
    _section_process,
    _services_photo_cards,
)


def test_photo_service_cards_keep_full_description() -> None:
    description = (
        "Complete apartment clearout including dismantling, sorting, transport, "
        "and careful disposal without hiding the end of this sentence."
    )

    html = _services_photo_cards(
        {
            "label": "Services",
            "layout_variant": "photo_cards",
            "items": [
                {
                    "title": "Apartment Clearout",
                    "description": description,
                    "image": "/images/service.jpg",
                }
            ],
        }
    )

    assert description in html
    assert "Complete apartment clearout including dismantling, sorting..." not in html
    assert "Complete apartment clearout including dismantling, sorting\u2026" not in html


def test_photo_service_card_css_puts_copy_below_image() -> None:
    css = _global_css(DesignTokens())

    assert "aspect-ratio:16/10" in css
    assert "wm3-svc-card__cta" not in css
    assert "rgba(11,31,51,0) 46%" not in css


def test_process_section_uses_step_graphics_instead_of_numbers() -> None:
    html = _section_process(
        {
            "label": "So einfach geht's",
            "heading": "In drei Schritten zur Entrümpelung",
            "layout_variant": "illustrated",
            "image": "/wp-content/uploads/webmaker/schritt-art-only.png",
            "steps": [
                {
                    "step": 1,
                    "title": "Besichtigung",
                    "description": "Kostenlos und unverbindlich.",
                },
                {
                    "step": 2,
                    "title": "Festpreisangebot",
                    "description": "Ohne versteckte Kosten.",
                },
                {
                    "step": 3,
                    "title": "Entrümpelung",
                    "description": "Besenrein.",
                },
            ],
        }
    )

    assert "wm3-process--illustrated" in html
    assert "wm3-process__stage" in html
    assert "wm3-process__captions" in html
    assert "schritt-art-only.png" in html
    assert "Besichtigung" in html
    assert "Festpreisangebot" in html
    assert "So einfach geht" in html
    assert 'class="wm3-step__num"' not in html
    assert "schritt-step-01" not in html


def test_process_complete_design_image_skips_duplicate_html_captions() -> None:
    html = _section_process(
        {
            "heading": "In drei Schritten zur Entrümpelung",
            "image": "/wp-content/uploads/webmaker/Schritt-Homepage.png",
            "steps": [
                {"step": 1, "title": "Besichtigung", "description": "Should not render."},
            ],
        }
    )

    assert "Schritt-Homepage.png" in html
    assert "Should not render." not in html
    assert "wm3-process-row" not in html


def test_process_section_falls_back_to_numbers_without_images() -> None:
    html = _section_process(
        {
            "heading": "Ablauf",
            "steps": [{"step": 1, "title": "Start", "description": "Los geht's."}],
        }
    )

    assert 'class="wm3-step__num">1</div>' in html
    assert "wm3-process--illustrated" not in html


def test_before_after_section_renders_transformations_block() -> None:
    html = _section_before_after(
        {
            "label": "Echte Transformationen",
            "heading": "Vorher Chaos. Nachher Besenrein.",
            "subheading": "Schnell, gründlich und zuverlässig.",
            "image": "/wp-content/uploads/webmaker/ba-cards-only.png",
            "cta_label": "Mehr Projekte ansehen",
            "cta_url": "/services/",
            "cta_secondary_label": "Kostenlose Besichtigung",
            "phone": "0151 00000000",
            "trust_items": ["Festpreisgarantie", "Diskret & Zuverlässig"],
        }
    )

    assert "wm3-ba" in html
    assert "Vorher Chaos. Nachher Besenrein." in html
    assert "ba-cards-only.png" in html
    assert "Mehr Projekte ansehen" in html
    assert "tel:015100000000" in html
    assert "Festpreisgarantie" in html


def test_before_after_complete_design_uses_full_artwork() -> None:
    html = _section_before_after(
        {
            "heading": "Vorher Chaos. Nachher Besenrein.",
            "image": "/wp-content/uploads/webmaker/Sehen-Sie-Selbst-Homepage.png",
            "cta_label": "Should not render",
            "trust_items": ["Should not render"],
        }
    )

    assert "Sehen-Sie-Selbst-Homepage.png" in html
    assert "Should not render" not in html
    assert "wm3-ba__actions" not in html
