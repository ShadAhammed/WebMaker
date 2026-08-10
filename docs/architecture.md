# WebMaker — Architecture

## Overview

WebMaker (`webmake`) is an AI-powered local tool that:

1. **Acquires & validates** the target website into a Website Package (Agent 0 — no AI)
2. **Migrates** the acquired site as-is into a chosen WordPress theme (MigrationAgent — no AI)
3. Analyzes business + competitors
4. Reviews the site against competitors and modern SEO / UX standards
5. Lets a human approve recommendations (OP-Content)
6. Renders approved improvements into the live WordPress demo
7. QA-reviews the demo (content + visual)

**Primary execution path (V2):** the `Orchestrator` runs single-responsibility
**agents**. Agents never call each other; they read prior typed artifacts and
emit new ones under `projects/<slug>/artifacts/`.

**UI tabs:** Crawl → Migrate → Analyze → OP-Content.
Theme selection lives in the **Migrate** tab.

**Launch (canonical):**

```text
.\webmake
.\webmake.ps1
python run.py webmake
```

`start-app` is a deprecated alias that redirects to `webmake`.

---

## V2 Agent Pipeline (definitive)

```text
Target URL (+ optional competitor URLs)
        │
        ▼
┌──────────────────────────┐
│ 0. WebsiteAcquisition    │  Stages 1–9 (deterministic, no AI):
│    "Acquire & Validate"  │  Crawl → HTML/DOM → Assets → Content
│                          │  → Brand → Layout → Screenshots
│                          │  → Website Package → Validation
│                          │  → website_package/ + artifacts/acquisition.json
└────────────┬─────────────┘
             ▼  (completeness report; Migrate warns if < threshold)
┌──────────────────────────┐
│ 1. MigrationAgent        │  Layout pipeline + theme install + WP generate
│    "Migrate as-is"       │  DOM → Layout → Semantic Model → Theme Mapper
│                          │  → optimized_*.json → artifacts/migration.json
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 2. TargetCrawler         │  Reuse crawl; BusinessAnalyzer → Claude
│                          │  → artifacts/target.json
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 3. CompetitorCrawler     │  CompetitorAnalyzer + DeepSeek structure
│                          │  → artifacts/competitors.json
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 4. WebsiteReviewer       │  Claude Sonnet — OP-Content recommendations
│                          │  → artifacts/op_content.json
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 4b. Human approval       │  OP-Content tab — tick recommendations
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 5. Design Pattern        │  GPT (+ catalog fallback)
│    Selector              │  → artifacts/design.json
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 6. LiveDemoRenderer      │  No AI — approved content only
│                          │  → artifacts/render_result.json
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ 7. QAReviewer            │  Claude content + GPT visual
│                          │  → artifacts/qa.json
└──────────────────────────┘
```

**Agent 0 — Website Acquisition & Validation:** understands the target site and
produces a verified Website Package. No AI rewriting, no migration, no SEO.

```text
Website
  → Crawl (pages, nav, robots/sitemap)
  → HTML (raw + cleaned + DOM)
  → Assets (logo, favicon, images, icons, bg, video, pdf)
  → Content (headings, paras, CTAs, forms, contact fields)
  → Brand (colors, fonts, spacing, button style)
  → Layout (hero, services, gallery, FAQ, columns, … — deterministic)
  → Screenshots (full-page per page)
  → website_package/*.json
  → validation_report.json (completeness scores + gaps)
```

Default validation threshold: **95%**. Below threshold → `passed=False` and gaps
listed; Migrate currently **warns** but does not hard-block.

**MigrationAgent** remains the faithful layout interpreter for WordPress
(theme mapper). Prefer running Crawl before Migrate.

Internal MigrationAgent pipeline (deterministic — zero AI):

```text
Website (raw HTML)
    → DOM Extractor          hierarchy-preserving tree (not flat text)
    → Layout Analyzer        structure / spacing / repeated patterns
    → Semantic Layout Model  CMS-agnostic JSON (hero, services_grid,
                             labeled_sections, two_column, gallery, faq, …)
    → Theme Mapper           Gutenberg blocks for the selected theme
    → optimized_*.json       body_html for WordPressGenerator
```

Rules:

- Agents never import or call each other
- Outputs are Pydantic schemas (`webmaker/schemas/`) — no bare dicts on boundaries
- Orchestrator validates, persists, and can **rerun a single agent** from stored upstream artifacts
- Renderer never invents content; it only applies approved recommendations + existing `optimized_*.json`

Theme / template selection happens in the **Migrate** tab (and optionally again
via DesignRecommendation during OP-Content). There is no separate Theme tab.

---

## How the UI Drives the Pipeline

Tk app: `webmaker/ui/tk_app.py` — **four** tabs, shared progress bar and log.

| Tab | Action | Orchestration |
|-----|--------|---------------|
| **Crawl** | Acquire website package + validation; show per-page counts | `Orchestrator.run_acquisition()` → `WebsiteAcquisitionAgent` (Agent 0) |
| **Migrate** | Pick theme + template; clone as-is into WordPress | `Orchestrator.run_migration()` → `MigrationAgent` |
| **Analyze** | Business + competitors (reuse crawl/package) | `run_pipeline` analyze/compete and/or `target_crawler` → `competitor_crawler` |
| **OP-Content** | Review, tick, save, render approved | `website_reviewer` → `live_demo_renderer` |

### Crawl tab (Agent 0)

1. Enter URL + project → **Acquire & Validate**
2. Builds `website_package/` (business, pages, navigation, sections, assets, brand, content, html, screenshots, `validation_report.json`)
3. UI shows overall completeness % and a per-page table (H1/H2/H3, paragraphs, images, buttons, forms, sections, screenshots)
4. Gaps listed when below threshold

### OP-Content tab (human gate)

1. Select project → **Run Review (Claude)** → `WebsiteReviewer` writes `op_content.json`
2. Recommendations show Current / Issue / Recommendation / Reason / Source / Priority
3. Tick items to approve → **Save selections** (persists `selected` flags)
4. **Render approved** → `LiveDemoRenderer` (theme/template from design artifact when present)

Closing and relaunching `webmake` never rebuilds the demo by itself; only
Migrate or OP-Content **Render approved** change WordPress.

### Page slugs

Standard slugs (generated / hydrated by default):

- `homepage`, `about`, `services`, `contact`, `faq`

### Theme & template selection

Catalog: `webmaker/data/theme_catalog.py` (max 5 themes: Kadence, Astra,
GeneratePress, OceanWP, Blocksy).

Theme + starter template are chosen in the **Migrate** tab after Crawl:

1. User picks theme + optional starter template
2. **Migrate as-is** runs MigrationAgent:
   - layout pipeline → `optimized_*.json`
   - `install_theme_stack` / `import_starter_template` (best-effort)
   - `WordPressGenerator` (`reset=True`)
3. Opens local demo URL

---

## AI Model Routing

Configured via `.env` / `settings.py`. Modules and agents pin providers explicitly:

| Agent / task | Provider | Default model |
|--------------|----------|---------------|
| WebsiteAcquisition (Agent 0) | None (deterministic) | — |
| MigrationAgent | None (deterministic) | — |
| Business analysis (inside TargetCrawler) | Claude | `claude-sonnet-4-6` |
| Competitor structure (inside CompetitorCrawler) | DeepSeek | `deepseek-chat` |
| WebsiteReviewer (OP-Content) | Claude | `claude-sonnet-4-6` |
| Design Pattern Selector | OpenAI (GPT) | `gpt-5.5-pro` |
| Design Pattern Selector fallback | Deterministic pattern + theme catalogs | — |
| LiveDemoRenderer | None | — |
| QA content audit (V2 agent) | Claude | `claude-sonnet-4-6` |
| QA visual / UX (V2 agent) | OpenAI (GPT) | `gpt-5.5-pro` |

Env keys: `CLAUDE_API_KEY`, `DEEPSEEK_API_KEY`, `GPT_API_KEY`
(+ `*_MODEL` overrides). `GEMINI_API_KEY` is unused on the V2 path.

`AIRouter` (`ai_router.py`) abstracts SDKs (`_ClaudeProvider`,
`_DeepSeekProvider`, `_OpenAIProvider`; Gemini adapter remains but is not
used by V2 agents). GPT-5-family models use
`max_completion_tokens` and omit temperature. Responses may be cached
(`ai_cache.py`).

Fallback order when `AI_PROVIDER=auto`: Claude → OpenAI → DeepSeek → Gemini.

Task affinity highlights:

- `design_recommendation`, `qa_visual_review` → OpenAI
- `website_review`, `business_analysis`, page copy → Claude
- `competitor_analysis`, `content_review` → DeepSeek
- `qa_review` → Claude

---

## Schemas & Artifacts

Package: `webmaker/schemas/` (Pydantic v2, `extra="forbid"`).

| Artifact | File | Produced by |
|----------|------|-------------|
| `WebsitePackageResult` | `artifacts/acquisition.json` | WebsiteAcquisitionAgent |
| Website package | `website_package/*.json` | WebsiteAcquisitionAgent |
| `MigrationResult` | `artifacts/migration.json` | MigrationAgent |
| Semantic layout model | `json/layout_model.json` (+ artifacts copy) | MigrationAgent |
| `TargetProject` | `artifacts/target.json` | TargetCrawler |
| `CompetitorProjects` | `artifacts/competitors.json` | CompetitorCrawler |
| `OpContent` | `artifacts/op_content.json` | WebsiteReviewer (+ human ticks) |
| `DesignRecommendation` | `artifacts/design.json` | DesignRecommendation |
| `RenderResult` | `artifacts/render_result.json` | LiveDemoRenderer |
| `QAArtifact` | `artifacts/qa.json` | QAReviewer |

Every artifact embeds `ArtifactMeta` (id, project, agent, schema_version, created_at).
`ArtifactStore` writes deterministic JSON (sorted keys).

`Recommendation` fields (exact): `id`, `page_slug`, `section`, `current`,
`issue`, `recommendation`, `reason`, `source`,
`priority` (`critical|high|medium|low`), `selected` (default `false`).

---

## Orchestrator

`webmaker/orchestrator/`:

- `Orchestrator` — sequential run, single-agent rerun, stop on missing/invalid upstream
- `ArtifactStore` — typed load/save under `projects/<slug>/artifacts/`

`ProjectManager` handles project/state CRUD + dirs (`create_project`,
`load_project`, `save_project`, `sync_crawl_data_dir`, …), Crawl pipeline
phases, and Theme apply. Unused optimize/fix helpers may remain in code but
are not wired to the UI.

Agent order:

```text
website_acquisition → migration_agent → target_crawler → competitor_crawler
  → website_reviewer → design_recommendation → live_demo_renderer → qa_reviewer
```

---

## Agents — who does what

| # | Agent | Tab | AI? | Responsibility |
|---|-------|-----|-----|----------------|
| **0** | **WebsiteAcquisitionAgent** | Crawl | No | Acquire website package + validation report. No rewrite / migration / SEO. |
| **1** | **MigrationAgent** | Migrate | No | Faithful as-is clone into selected WP theme (layout map). |
| **2** | **TargetCrawler** | Analyze | Claude (profile) | Business profile from crawl data. |
| **3** | **CompetitorCrawler** | Analyze | DeepSeek | Crawl user-supplied competitors. |
| **4** | **WebsiteReviewer** | OP-Content | Claude | Tickable recommendations. Does not change WP. |
| **4b** | **You (human)** | OP-Content | — | Approve recommendations. |
| **5** | **DesignRecommendation** | OP-Content path | GPT | Theme/template + pattern slots. |
| **6** | **LiveDemoRenderer** | OP-Content → Render | No | Apply approved recs to demo. |
| **7** | **QAReviewer** | after render | Claude + GPT | Report only — never fixes. |

---

---

## WordPress Generation Rules

`WordPressGenerator.generate_from_directory(project_dir, reset=False, update_only=False)`:

| Mode | Behavior |
|------|----------|
| Full generate | Site settings, theme, media, pages, primary menu, SEO meta |
| `reset=True` | Wipe pages/menus first, then full generate |
| `update_only=True` | Update page HTML only — keep theme, media, menus |

| Called from | Flags |
|-------------|--------|
| MigrationAgent | `reset=True` full generate from layout-migrated `optimized_*.json` |
| LiveDemoRenderer | Theme stack (if needed) + content overwrite from approved OP-Content (no invented copy) |

Constraints:

- Prefer **classic** themes with menu locations (`twentytwentyone` first); avoid dumping all pages into the header
- Short nav labels (Startseite, Über uns, Leistungen, Kontakt, FAQ) — never SEO headlines
- No automatic per-service sub-pages; orphan non-standard pages are pruned on full generate
- Theme-tab downloads are allowed for catalog entries; legacy `install_theme()` only activates already-installed themes

---

## Competitor Analysis Rules

- Competitor URLs are **user-supplied only** (Tk text box) — no auto-discovery
- If a URL already appears in competitor `.md` (`<!-- competitor-url: … -->`), **skip** crawl/analysis **forever** on restart — DeepSeek is not called again
- Target business analysis (Claude) is skipped when `business_profile.json` or `json/target_business.md` already exists
- Analyze does **not** force-rerun completed crawl/analyze on every click; compete re-runs only for **new** undocumented competitor URLs
- Output is a numbered **structure story** (what competitors do well for this niche)
- WebsiteReviewer consumes competitor strengths as inspiration only — never copies verbatim
- Zero competitors → crawler completes with a warning / empty artifact

---

## Module & Agent Catalogue

### Agents (`webmaker/agents/`)

| Agent | Class | AI | Output |
|-------|-------|----|--------|
| `website_acquisition` | `WebsiteAcquisitionAgent` | None (stages 1–9) | `WebsitePackageResult` + `website_package/` |
| `migration_agent` | `MigrationAgent` | None (DOM → layout → theme map) | `MigrationResult` + `optimized_*.json` |
| `target_crawler` | `TargetCrawlerAgent` | Claude (via BusinessAnalyzer) | `TargetProject` |
| `competitor_crawler` | `CompetitorCrawlerAgent` | DeepSeek (via CompetitorAnalyzer) | `CompetitorProjects` |
| `website_reviewer` | `WebsiteReviewerAgent` | Claude Sonnet 4.6 | `OpContent` |
| `design_recommendation` | `DesignRecommendationAgent` | GPT pattern selector (+ catalog fallback) | `DesignRecommendation` |
| `live_demo_renderer` | `LiveDemoRendererAgent` | None | `RenderResult` |
| `qa_reviewer` | `QAReviewerAgent` | Claude content + GPT visual | `QAArtifact` |

MigrationAgent internals (`webmaker/agents/migration_agent/`):

| Module | Role |
|--------|------|
| `dom_extractor.py` | Hierarchy-preserving DOM tree from raw HTML |
| `layout_analyzer.py` | CMS-agnostic section detection (grids, labeled rows, two-column, …) |
| `semantic_model.py` | Universal JSON contract between analyzer and mapper |
| `theme_mapper.py` | Semantic sections → Gutenberg HTML for selected theme |
| `pipeline.py` | Wires extract → analyze → map → write `optimized_*.json` |
| `passthrough_writer.py` | Legacy fallback when raw HTML / layout is unavailable |

Contract: `BaseAgent[TIn, TOut]` in `agents/base.py` — validate in → run → validate out → stamp meta.

### Legacy modules (`webmaker/modules/`)

| Module | Class | Role |
|--------|-------|------|
| `website_crawler.py` | `WebsiteCrawler` | Playwright crawl, screenshots, assets |
| `business_analyzer.py` | `BusinessAnalyzer` | Claude → business profile |
| `competitor_analyzer.py` | `CompetitorAnalyzer` | DeepSeek → structure stories + comparison |
| `content_optimizer.py` | `ContentOptimizer` | Page JSON helpers (not UI-primary) |
| `wordpress_generator.py` | `WordPressGenerator` | WP-CLI demo build / hydrate / theme stack |
| `qa_reviewer.py` | `QAReviewer` | Live + AI QA; original-site comparison |
| `website_fixer.py` | `WebsiteFixer` | Fix helpers (not UI-primary) |
| `project_manager.py` | `ProjectManager` | State CRUD; crawl pipeline / theme |
| `ai_router.py` | `AIRouter` | Gemini / Claude / DeepSeek / OpenAI |
| `ai_cache.py` | `AICache` | Response cache |
| `job_manager.py` | `JobManager` | Granular jobs (not primary UI path) |

UI / data / orchestration:

| Path | Role |
|------|------|
| `webmaker/ui/tk_app.py` | Desktop UI (three V2 tabs) |
| `webmaker/data/theme_catalog.py` | Curated themes + starter templates |
| `webmaker/data/design_patterns.py` | Design Pattern Library (hero…footer) |
| `webmaker/schemas/` | Artifact Pydantic models |
| `webmaker/orchestrator/` | Orchestrator + ArtifactStore |

---

## Project Layout

```text
projects/<slug>/
  project.json              ← ProjectState (phases, metadata, regenerate, page_slugs, …)
  artifacts/                ← V2 agent outputs (Pydantic JSON)
    target.json
    competitors.json
    op_content.json
    design.json
    render_result.json
    qa.json
  pages/                    ← crawled text
  images/  screenshots/  assets/  raw/
  json/
    pages.json, images.json, navigation.json, crawl_summary.json
    business_profile.json
    competitors.json, competitor_analysis.json, comparison_report.json
    competitor_structure.md (and per-competitor .md under json/competitors/)
    optimized_<slug>.json, meta_data.json, content_review.json
    generation_report.json
    qa_report.json, seo_review.json, website_score.json
    fix_report.json
  logs/  config/  qa/
```

Slug = project name or domain. Metadata fields used by the UI loops include:

- `page_slugs`, `max_improve_rounds`, `regenerate`
- `significantly_better_than_original`, `qa_comparison_comment`
- `theme_id` / `template_id` / `theme_applied` / `template_applied`
- `wp_url`, `pages_created`, `pages_hydrated`

---

## Package Structure

```text
webmaker/
├── config/
│   └── settings.py           ← Pydantic BaseSettings (.env)
├── core/
│   ├── exceptions.py
│   ├── logging.py
│   ├── progress.py
│   ├── schema.py
│   └── types.py              ← includes AIProvider (gemini|claude|deepseek|openai)
├── schemas/                  ← V2 artifact models
│   ├── base.py
│   ├── business.py
│   ├── target.py
│   ├── competitor.py
│   ├── review.py             ← OpContent / Recommendation
│   ├── design.py
│   ├── render.py
│   └── qa.py
├── agents/
│   ├── base.py
│   ├── target_crawler/
│   ├── competitor_crawler/
│   ├── website_reviewer/
│   ├── design_recommendation/
│   ├── live_demo_renderer/   ← prepare_render, wordpress_renderer, live_preview
│   └── qa_reviewer/
├── orchestrator/
│   ├── orchestrator.py
│   └── store.py
├── data/
│   ├── theme_catalog.py
│   └── design_patterns.py    ← Design Pattern Library (Agent 4)
├── modules/
│   ├── website_crawler.py
│   ├── business_analyzer.py
│   ├── competitor_analyzer.py
│   ├── content_optimizer.py
│   ├── wordpress_generator.py
│   ├── qa_reviewer.py
│   ├── website_fixer.py
│   ├── project_manager.py
│   ├── ai_router.py
│   ├── ai_cache.py
│   └── job_manager.py
├── ui/
│   └── tk_app.py
├── plugins/
└── utils/
```

---

## Configuration

Priority (highest first):

1. Shell environment variables
2. `.env` (via `python-dotenv`)
3. Defaults in `settings.py`

`config/webmaker.yaml` documents keys; it does not override env/`.env`.

```python
from webmaker.config.settings import settings
print(settings.wordpress_url)
print(settings.gpt_model)  # gpt-5.5-pro by default
```

Local stack: MariaDB + portable PHP + WordPress under `wordpress_dir`,
driven by `webmake.ps1`.

---

## Error Handling & Logging

- All errors inherit `WebMakerError`; agent/orchestrator failures raise
  `AgentError` / `OrchestratorError` and stop that run safely
- Legacy phase failures mark the phase failed, persist state, and stop the pipeline
- Resume continues from the first non-completed phase unless `force_phases` is set
- Logging: Loguru via `setup_logging()` / `get_logger("module_name")`
- Log rotation: 10 MB, retain 14 days

---

## Testing

| Layer | Location | Requires services? |
|-------|----------|--------------------|
| Unit | `tests/unit/` | No |
| Integration | `tests/integration/` | Yes (MariaDB, PHP, WP) |

V2 coverage includes schema round-trips, `BaseAgent` contract, ArtifactStore /
OP-Content selection persistence, orchestrator single-agent rerun + missing-
upstream stop, Design/QA GPT paths with mocked routers (`test_webmaker_v2.py`),
and OpenAI provider adapter tests (`test_ai_router.py`).

```bash
pytest tests/unit/ -v
pytest tests/unit/test_webmaker_v2.py tests/unit/test_ai_router.py -v
pytest -v   # services must be running
```

---

## End-to-End Operator Flow (definitive)

```text
1. .\webmake
2. Tab “Crawl”
     → enter client URL + project → Acquire & Validate
3. Tab “Migrate”
     → pick theme + template → Migrate as-is → live demo
4. Tab “Analyze”
     → optional competitor URLs → business + competitor analysis
5. Tab “OP-Content”
     → Run Review (Claude) → tick recommendations → Save
     → Render approved → live demo updates
6. Review local WordPress demo (wordpress_url)
```

This is the supported production path. Streamlit and earlier multi-phase drafts
are obsolete. V2 agents + Orchestrator are the only UI path.
