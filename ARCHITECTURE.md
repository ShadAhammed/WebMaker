# WebMaker — Complete Architecture

> **Status:** Phases 1–10 complete  
> **Purpose:** End-to-end design of how WebMaker runs — from local environment setup through crawl, AI analysis, content generation, WordPress demo build, QA, and orchestration.

---

## 1. What WebMaker Is

WebMaker is an AI-powered local tool that:

1. Reads an existing **public business website**
2. Understands the **business** (services, tone, contacts, strengths)
3. Studies **competitor websites** for structural ideas (never copies content)
4. Generates **improved website content** (homepage, about, services, contact, FAQ, meta tags)
5. Builds a **local WordPress demo site** from that content
6. Runs a **QA review** and produces structured scores/reports

It is designed for **German local businesses** (and similar service businesses): professional, factual, no invented claims, no SEO spam.

---

## 2. High-Level System View

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         OPERATOR / DEVELOPER                             │
│   python app.py  ·  python run.py  ·  ProjectManager API                 │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│                         ProjectManager                                   │
│   create / load / save / resume · phase status · project.json            │
└───────┬───────────┬───────────┬───────────┬───────────┬───────────┬──────┘
        │           │           │           │           │           │
        ▼           ▼           ▼           ▼           ▼           ▼
   Website     Business   Competitor   Content    WordPress      QA
   Crawler     Analyzer   Analyzer     Optimizer  Generator   Reviewer
        │           │           │           │           │           │
        │           └───────────┴─────┬─────┴───────────┘           │
        │                             ▼                             │
        │                        AIRouter                           │
        │                  Gemini · Claude · DeepSeek               │
        │                             │                             │
        ▼                             ▼                             ▼
   projects/<slug>/              .env / Settings              wordpress/
   pages · images · json         API keys · models            local demo site
```

**Design rule:** Modules never call Gemini/Claude/DeepSeek SDKs directly. All LLM traffic goes through `AIRouter`.

**Design rule:** Modules communicate primarily through **typed models** (`webmaker/core/types.py`) and **JSON files** under each project directory — not through shared mutable globals.

---

## 3. Repository Layout

```
WebMaker/
├── app.py                      Application bootstrap (config + logging + readiness)
├── run.py                      Dev CLI (verify / start / stop / info)
├── ARCHITECTURE.md             This document
├── README.md                   Setup & usage guide
├── requirements.txt            Python dependencies
├── pyproject.toml              Project + pytest/ruff config
├── .env.example                API key template
├── .env                        Local secrets (not committed)
│
├── webmaker/                   Main Python package
│   ├── config/
│   │   └── settings.py         Pydantic Settings (paths, DB, AI, crawler)
│   ├── core/
│   │   ├── types.py            Shared models & enums
│   │   ├── exceptions.py       Error hierarchy
│   │   ├── logging.py          Loguru setup
│   │   ├── schema.py           Versioned JSON helpers (schema_version)
│   │   ├── progress.py         ProgressEvent / ProgressManager
│   │   └── prompts.py          PromptLoader + load_prompt()
│   ├── modules/
│   │   ├── website_crawler.py
│   │   ├── business_analyzer.py
│   │   ├── competitor_analyzer.py
│   │   ├── content_optimizer.py
│   │   ├── wordpress_generator.py
│   │   ├── qa_reviewer.py
│   │   ├── project_manager.py
│   │   ├── ai_router.py
│   │   ├── ai_cache.py         Disk AI response cache
│   │   └── job_manager.py      Discrete job queue / execute / retry
│   ├── plugins/
│   │   ├── base.py             Plugin interface (before/after phase & job)
│   │   └── registry.py         PluginRegistry + discovery
│   └── utils/
│       └── helpers.py          slugify, truncate, ensure_dir, …
│
├── prompts/                    Markdown system prompts (loaded dynamically)
├── plugins/                    Optional user plugins (*.py) — empty by default
│
├── config/
│   └── webmaker.yaml           Human-readable defaults reference
│
├── tests/
│   ├── conftest.py
│   ├── unit/                   Fast mocked tests
│   └── integration/            PHP / MariaDB / WP-CLI checks
│
├── setup/
│   ├── setup.py                venv + pip + Playwright browsers
│   ├── setup.ps1               PHP / MariaDB / WordPress / WP-CLI (Windows)
│   └── verify.py               Environment verification (+ optional repair)
│
├── docs/
│   └── architecture.md         Earlier short architecture note
│
├── bin/                        Portable binaries (PHP, MariaDB, wp-cli.phar)
├── wordpress/                  Local WordPress installation
├── projects/                   Per-client project workspaces
├── outputs/                    Exports / generated artefacts
├── logs/                       Application logs
├── cache/                      HTTP / crawl cache + cache/ai/ responses
├── assets/                     Shared static assets
└── templates/                  Future theme/page templates
```

---

## 4. Runtime Environment (Phase 1)

WebMaker assumes a **fully local** stack — no cloud WordPress, no remote DB for the demo.

| Component | Role | Typical location |
|-----------|------|------------------|
| Python 3.11+ venv | Application runtime | `.venv/` |
| Playwright | Screenshots during crawl / QA | Browser binaries via `playwright install` |
| PHP (portable) | WP-CLI runner + built-in web server | `bin/php/` |
| MariaDB (portable) | WordPress database | `bin/mariadb/` (port **3307** by default) |
| WordPress | Demo site | `wordpress/` |
| WP-CLI | Site automation | `bin/wp-cli.phar` |

**Local site URL (default):** `http://localhost:8080`

**Admin (default from setup):** user/pass from `.env` / Settings (`WP_ADMIN_*`).

Start/stop helpers live in `run.py` and `scripts/` (generated by setup).

---

## 5. Configuration System

### 5.1 Single source of truth: `Settings`

`webmaker/config/settings.py` loads:

1. Defaults in code  
2. `.env` via `python-dotenv` + Pydantic `BaseSettings`  
3. Process environment overrides  

Important groups:

| Group | Examples |
|-------|----------|
| Paths | `project_root`, `projects_dir`, `wordpress_dir`, `logs_dir`, `bin_dir` |
| Web | `WEB_HOST`, `WEB_PORT` → `wordpress_url` |
| Database | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` |
| WordPress admin | `WP_ADMIN_USER`, `WP_ADMIN_PASS`, `WP_ADMIN_EMAIL` |
| AI | `GEMINI_API_KEY`, `CLAUDE_API_KEY`, `DEEPSEEK_API_KEY`, `AI_PROVIDER`, model names |
| Crawler | `CRAWLER_MAX_DEPTH`, `CRAWLER_MAX_PAGES`, `CRAWLER_TIMEOUT`, … |
| Competitors | `COMPETITOR_MAX` |
| Logging | `LOG_LEVEL`, `log_filename` |

`config/webmaker.yaml` documents the same knobs for humans; runtime still prefers env/Settings.

### 5.2 Secrets policy

- API keys **only** in `.env`  
- Never logged by `AIRouter`  
- Never hardcoded in modules  

---

## 6. Core Shared Layer

### 6.1 Types (`webmaker/core/types.py`)

Cross-module contracts (Pydantic):

| Type | Used by |
|------|---------|
| `ProjectStatus` | Project lifecycle (`pending` → `crawling` → … → `completed` / `failed`) |
| `AIProvider` | `gemini` / `claude` / `deepseek` / `auto` |
| `PageData` / `CrawlResult` | Crawler → analyzers |
| `BusinessInfo` | Business / competitor / content / WP |
| `CompetitorInfo` / `AnalysisResult` | Competitor + content |
| `GenerationResult` | WordPress generator → QA |
| `QACheck` / `QAReport` | QA output |
| `ProjectConfig` | Lightweight project handle for APIs |

### 6.2 Exceptions (`webmaker/core/exceptions.py`)

```
WebMakerError
├── ConfigurationError
├── CrawlerError (+ RateLimitError, RobotsBlockedError)
├── AnalysisError
├── AIError (+ AIProviderUnavailableError, AIResponseError)
├── GenerationError (+ WordPressError, ThemeError)
├── QAError
├── ProjectError (+ ProjectNotFoundError, ProjectAlreadyExistsError)
└── DatabaseError
```

Callers can catch `WebMakerError` broadly or a specific subclass.

### 6.3 Logging (`webmaker/core/logging.py`)

- Loguru console + rotating file under `logs/`  
- Module-bound loggers via `get_logger("module_name")`  
- Level from `LOG_LEVEL`  

---

## 7. End-to-End Pipeline (How a Project Runs)

### 7.1 Pipeline order

```
1. crawl      → WebsiteCrawler
2. analyze    → BusinessAnalyzer
3. compete    → CompetitorAnalyzer
4. optimize   → ContentOptimizer
5. generate   → WordPressGenerator
6. review     → QAReviewer
```

Orchestrated exclusively by **`ProjectManager`**.

### 7.2 Typical operator flow

```python
from webmaker.config.settings import settings
from webmaker.modules.project_manager import ProjectManager

pm = ProjectManager(settings)

# Create a workspace (folder + project.json)
pm.create_project(
    "https://example-roofer.de",
    name="demo-business",
    competitor_urls=[
        "https://competitor-a.de",
        "https://competitor-b.de",
    ],
)

# Run everything (or resume later)
pm.run_pipeline()

# If something failed mid-way:
pm.resume()
```

### 7.3 Resume behaviour

Each phase is tracked as:

`not_started` → `running` → `completed` | `failed`

- **Resume** skips `completed` phases  
- **force_phases** re-runs selected phases  
- **skip_phases** omits phases  
- On failure: phase marked `failed`, `project.json` saved, pipeline stops  

### 7.4 Data directory vs project directory

- `project_dir` — ProjectManager folder (`projects/<slug>/`) with `project.json`, logs, subdirs  
- `data_dir` — Where crawler JSON lives (often same folder; may be domain-named folder under `projects/`)  

Downstream modules (`analyze_from_directory`, `optimize_from_directory`, …) always read from **`data_dir`**.

---

## 8. Project Workspace Layout

Example after a full run:

```
projects/<slug>/
├── project.json                 ProjectManager state & phase tracking
├── logs/
│   └── project.log              Per-project execution log
├── pages/                       Extracted page text
├── images/                      Downloaded images
├── screenshots/                 Crawl screenshots
├── assets/                      Favicon, PDFs, etc.
├── raw/                         Raw HTML
├── json/
│   ├── pages/                   Per-page rich JSON
│   ├── pages.json
│   ├── navigation.json
│   ├── images.json
│   ├── crawl_summary.json
│   ├── business_profile.json
│   ├── competitors.json
│   ├── competitor_analysis.json
│   ├── comparison_report.json
│   ├── competitors/<domain>.json
│   ├── optimized_homepage.json
│   ├── optimized_about.json
│   ├── optimized_services.json
│   ├── optimized_contact.json
│   ├── optimized_faq.json
│   ├── meta_data.json
│   ├── content_review.json
│   ├── generation_report.json
│   ├── qa_report.json
│   ├── seo_review.json
│   └── website_score.json
├── config/
└── qa/                          Optional QA screenshots / extras
```

WordPress itself stays in `wordpress/` (shared local install); the generator applies content there via WP-CLI.

---

## 9. Module Deep Dive

### 9.1 `AIRouter` — central LLM gateway

**File:** `webmaker/modules/ai_router.py`  
**Class:** `AIRouter`

| Concern | Behaviour |
|---------|-----------|
| Providers | Gemini (`google-genai`), Claude (`anthropic`), DeepSeek (OpenAI-compatible) |
| Public API | `complete()` → `str`; `request()` → `AIResponse`; `analyze_content()`; `generate_text()` |
| Routing | Preferred provider → task affinity → `AI_PROVIDER` → default chain |
| Affinity | Analysis → Gemini; content → Claude; review/QA → DeepSeek |
| Resilience | Retries with backoff on rate limits/timeouts; fallback across providers |
| Safety | Never logs keys or prompt bodies; redacts secrets in errors |

**Extensibility:** Add a new provider by subclassing `_BaseProvider` and registering it in `_init_clients()` — other modules stay unchanged.

---

### 9.2 `WebsiteCrawler` — deterministic extraction

**File:** `webmaker/modules/website_crawler.py`  
**Class:** `WebsiteCrawler`  
**AI:** none  

**Input:** website URL  

**Does:**

- Same-domain BFS crawl (nav, footer, internal links, sitemap)  
- Respects robots / limits / timeouts  
- Extracts titles, meta, headings, text, links, OG, JSON-LD  
- Downloads images + selected assets  
- Playwright full-page screenshots  
- Writes structured JSON under the project folder  

**Output:** `CrawlResult` + files in `json/`, `pages/`, `images/`, `screenshots/`, …

---

### 9.3 `BusinessAnalyzer` — business understanding

**File:** `webmaker/modules/business_analyzer.py`  
**Class:** `BusinessAnalyzer`  
**AI:** Gemini via `AIRouter` (AUTO)  

**Input:** crawler JSON (`pages.json`, navigation, images, per-page JSON) — **does not crawl**  

**Pipeline:**

1. Load crawler output  
2. Deterministic extraction (emails, phones, socials, heuristics)  
3. AI reasoning for industry, tone, USPs, journey, etc.  
4. Merge (facts win over guesses)  
5. Write `business_profile.json`  
6. Return `BusinessInfo`  

**Rule:** Never invent services, pricing, awards, or history.

---

### 9.4 `CompetitorAnalyzer` — competitive intelligence

**File:** `webmaker/modules/competitor_analyzer.py`  
**Class:** `CompetitorAnalyzer`  
**AI:** Gemini via `AIRouter`  

**Input:**

- User-provided competitor URLs  
- Client `business_profile.json`  

**Does:**

1. Crawl each competitor with `WebsiteCrawler` (no duplicated crawler logic)  
2. AI profile per competitor (services, nav, CTAs, trust, FAQ, strengths/weaknesses)  
3. AI comparison vs client → structural **ideas only**  
4. Write `competitors.json`, `competitor_analysis.json`, `comparison_report.json`  

**Rule:** Never copy wording, design, or images.

If no competitor URLs are set, ProjectManager completes the compete phase with a warning and empty artefacts.

---

### 9.5 `ContentOptimizer` — content generation + review

**File:** `webmaker/modules/content_optimizer.py`  
**Class:** `ContentOptimizer`  
**AI:** Claude (generate) + DeepSeek (review) via `AIRouter`  

**Input:** business profile + comparison report + crawler pages + meta  

**Generates (per page):**

- Homepage, About, Services, Contact, FAQ  
- Hero, intro, CTAs, why-choose-us, FAQs  
- Meta titles / descriptions  

**Then:** DeepSeek reviews for factuality, AI-sounding language, gaps — **does not auto-rewrite**.  

**Outputs:** `optimized_*.json`, `meta_data.json`, `content_review.json`  

**Rules:**

- Preserve facts  
- Use `[MISSING INFORMATION]` instead of inventing  
- Natural language suitable for German local businesses  
- Competitor data = inspiration only  

---

### 9.6 `WordPressGenerator` — local demo build

**File:** `webmaker/modules/wordpress_generator.py`  
**Class:** `WordPressGenerator`  
**AI:** none  

**Input:** optimized JSON + meta + images + business profile  

**Does via WP-CLI (PHP + phar):**

- Verify existing WordPress install (no re-download)  
- Site settings (title, tagline, timezone, language, permalinks)  
- Activate already-installed theme only (no theme downloads)  
- Import crawled images into Media Library  
- Create/update pages from optimized content  
- Apply SEO meta from `meta_data.json` (post meta / Yoast-compatible keys if present)  
- Build Primary menu + set static homepage  
- Write `generation_report.json`  

**Does not** install new plugins/themes from the internet.

---

### 9.7 `QAReviewer` — audit only

**File:** `webmaker/modules/qa_reviewer.py`  
**Class:** `QAReviewer`  
**AI:** DeepSeek primary; optional Claude second opinion  

**Input:** generation report + business/optimized JSON + optional live WP URL  

**Layers:**

1. **Deterministic checks** — business consistency, placeholders, SEO fields, structure, CTAs, accessibility hints  
2. **Live HTTP checks** (optional) — availability, broken links, title/desc/H1, alt text, TTFB/weight  
3. **AI auditor** — strengths/weaknesses/issues (review only)  
4. **Scoring** — content, SEO, accessibility, conversion, business consistency, overall  

**Outputs:** `qa_report.json`, `seo_review.json`, `content_review.json`, `website_score.json`  

**Rule:** Never modifies WordPress or regenerates content.

---

### 9.8 `ProjectManager` — orchestrator

**File:** `webmaker/modules/project_manager.py`  
**Class:** `ProjectManager`  

**Owns:**

- Project create / open / save / delete / list  
- Folder tree creation  
- `project.json` persistence  
- Phase execution, resume, force/skip  
- Environment verification (`php`, `wpcli`, `wordpress`, `mariadb`)  
- Lazy construction of the six pipeline modules (+ injectable mocks for tests)  

**Does not** implement crawl/AI/WP/QA business logic.

---

## 10. AI Usage Map

| Stage | Model (intended) | Via |
|-------|------------------|-----|
| Business understanding | Gemini | `AIRouter` |
| Competitor profiling & comparison | Gemini | `AIRouter` |
| Website copy generation | Claude | `AIRouter` |
| Content critique | DeepSeek | `AIRouter` |
| Site QA auditor | DeepSeek (+ optional Claude) | `AIRouter` |
| Crawl / WordPress build | — | No AI |

If a preferred provider key is missing, `AIRouter` falls back along the available chain (unless `allow_fallback=False`).

---

## 11. Data Contracts Between Stages

```
URL
  → CrawlResult + json/pages.json, images.json, …
  → business_profile.json + BusinessInfo
  → competitors.json + comparison_report.json + AnalysisResult
  → optimized_*.json + meta_data.json + PageContent
  → Live WordPress pages/menu/media + generation_report.json + GenerationResult
  → qa_report.json + website_score.json + QAReport
```

JSON on disk is the **durable** contract (supports resume and debugging).  
In-memory Pydantic models are the **runtime** contract for APIs and tests.

---

## 12. Control & Entry Points

| Entry | Role |
|-------|------|
| `python app.py` | Load settings, setup logging, init `ProjectManager`, print readiness |
| `python run.py verify\|start\|stop\|info` | Environment / local server helpers |
| `setup/setup.py` | Python deps + Playwright |
| `setup/setup.ps1` | PHP / MariaDB / WordPress / WP-CLI on Windows |
| `setup/verify.py` | Health checks (+ optional repair) |
| `ProjectManager` API | Programmatic full pipeline |

A future Streamlit UI (mentioned in early planning) would sit above `ProjectManager` without changing module boundaries.

---

## 13. Error Handling Strategy

| Layer | Strategy |
|-------|----------|
| Crawler | Per-page failures logged; crawl continues |
| Business / Competitor / Content | Missing JSON → warnings; AI failures recorded; continue when possible |
| Competitor | One competitor failure does not stop others |
| WordPress | Soft failures become report warnings; critical verify failures abort |
| QA | Continues with limited coverage if inputs missing |
| ProjectManager | Marks phase failed, saves state, stops pipeline |
| AIRouter | Retry transient errors; fallback providers; clear `AIError`s |

---

## 14. Testing Strategy

```
tests/
├── unit/           Mocked modules, no network / no live WP required
└── integration/    Real PHP / MariaDB / WP-CLI when present
```

Coverage includes:

- Config, exceptions, utils  
- Each pipeline module (crawler extraction, analyzers, optimizer, WP generator, QA, manager, router)  
- Resume / skip / force / failure persistence for orchestration  

Current scale: **~545 unit tests** (grows with the codebase).

---

## 15. Security & Compliance Notes

- Local-only demo generation by default  
- API keys only in `.env`  
- AI prompts must not invent regulated claims (pricing, certifications, reviews)  
- Competitor analysis is for **ideas**, not content theft  
- Crawler stays on-domain and can respect `robots.txt`  
- No automatic publishing to production hosts  

---

## 16. How the Pieces Come Together (Narrative)

1. **Setup once** — install Python stack, Playwright, PHP, MariaDB, WordPress, WP-CLI; copy `.env.example` → `.env` and add AI keys.  
2. **Create a project** — `ProjectManager.create_project(url, name=…, competitor_urls=[…])` builds `projects/<slug>/` and `project.json`.  
3. **Crawl** — `WebsiteCrawler` fills pages, images, screenshots, and crawler JSON.  
4. **Understand the business** — `BusinessAnalyzer` produces `business_profile.json` (facts + Gemini reasoning).  
5. **Study competitors** — each URL is crawled and profiled; comparison report lists structural opportunities.  
6. **Write better pages** — Claude drafts content; DeepSeek critiques; JSON pages + meta are stored.  
7. **Build the demo** — WP-CLI creates pages, menu, media, SEO meta on the local WordPress.  
8. **Audit** — QAReviewer scores consistency, SEO, conversion readiness; writes reports.  
9. **Present** — open `http://localhost:8080` for the client demo; use QA scores to discuss gaps.  
10. **Resume anytime** — if a phase failed (API limit, WP down), fix the cause and `resume()`.

---

## 17. Non-Goals (Out of Scope for Current Architecture)

- Automatic competitor discovery from search engines (URLs are user-supplied)  
- Downloading new WP themes/plugins at generation time  
- Auto-fixing QA issues or auto-rewriting after DeepSeek review  
- Multi-tenant cloud SaaS hosting  
- Direct production DNS / hosting deployment  

These can be added later **without breaking** the module boundaries described above.

---

## 18. Extension Guide

| Want to… | Do this |
|----------|---------|
| Add an AI vendor | New `_BaseProvider` + register in `AIRouter._init_clients` |
| Add a pipeline phase | New module + register in `ProjectManager._PIPELINE` / runners |
| Add a page type | Extend ContentOptimizer schemas + WordPressGenerator HTML renderers |
| Change defaults | `.env` / `Settings` / document in `config/webmaker.yaml` |
| Add a UI | Call `ProjectManager` only; do not bypass modules |

---

## 19. Quick Reference — Module I/O

| Module | Reads | Writes | Calls AI? |
|--------|-------|--------|-----------|
| WebsiteCrawler | URL | crawl JSON, images, screenshots | No |
| BusinessAnalyzer | crawl JSON | `business_profile.json` | Yes (Gemini) |
| CompetitorAnalyzer | URLs + business profile | competitor + comparison JSON | Yes (Gemini) |
| ContentOptimizer | business + comparison + pages | `optimized_*.json`, meta, content review | Yes (Claude + DeepSeek) |
| WordPressGenerator | optimized + meta + images | WP site + `generation_report.json` | No |
| QAReviewer | all prior JSON (+ live WP) | QA / SEO / score JSON | Yes (DeepSeek ± Claude) |
| ProjectManager | Settings + module APIs | `project.json`, logs | Indirectly |
| AIRouter | Settings / `.env` | — (responses only) | Yes (owns SDKs) |

---

## 20. Summary

WebMaker is a **linear, resumable, file-backed pipeline** coordinated by `ProjectManager`, with a strict split between:

- **Deterministic systems** (crawl, WordPress automation)  
- **AI systems** (understanding, writing, reviewing) behind one router  
- **Audit systems** that observe but do not mutate  

That separation is what makes the system maintainable: each phase can be tested, resumed, and replaced independently while the overall product behaviour stays predictable.

---

## 21. Architecture Extensions (Non-Breaking)

These components extend the stable pipeline **without changing phase order or public module contracts**.

### 21.1 Job System (`job_manager.py`)

Lightweight queue for discrete tasks (generate homepage, rebuild WordPress, QA only, re-run competitors, etc.).

| Type | Purpose |
|------|---------|
| `Job` | id, project_id, job_type, status, progress, timestamps, execution_log |
| `JobResult` | success/status/artifacts/data for one execution |
| `JobStatus` | pending → queued → running → completed / failed / cancelled / retrying |

`ProjectManager` owns a `JobManager` internally and exposes:

- `run_job(job_type, params=…)` — create + execute immediately  
- `enqueue_job(…)` — queue for `jobs.process_queue()`  
- `jobs` property — full `JobManager` API (cancel / retry / resume)

`run_pipeline()` / `resume()` / `run_phase()` continue to work exactly as before.

### 21.2 AI Cache (`ai_cache.py` + `AIRouter`)

Before every provider call, `AIRouter.request()` checks `cache/ai/` using:

```
SHA256(model + provider + system + user prompt + context)
```

Hits return the stored response; misses call the provider and store the result.  
Invalidate via `AIRouter.invalidate_cache()` or `AICache.invalidate()`.

### 21.3 Prompt Repository (`prompts/` + `core/prompts.py`)

System prompts live in Markdown files (e.g. `business.md`, `competitor.md`, `homepage.md`, `faq.md`, `review.md`, `qa.md`, `wordpress.md`).  
Load with `load_prompt(name)` / `PromptLoader` / `AIRouter.load_prompt()`.  
Modules keep short inline fallbacks only if a file is missing.

### 21.4 Versioned Output Schema (`core/schema.py`)

Every generated JSON artefact includes `schema_version` (currently `1`).  
Lists are stored as `{"schema_version": 1, "items": [...]}`.  
Loaders accept both legacy bare lists and versioned wrappers via `unwrap_json` / `load_json_list`.

### 21.5 Progress Event System (`core/progress.py`)

| Type | Role |
|------|------|
| `ProgressEvent` | percent, message, phase, project_id, job_id, status |
| `ProgressManager` | subscribe / emit / history; pipeline milestones 0→100 |

`ProjectManager` emits milestones (create 0%, crawl 10%, analyze 35%, …, complete 100%).  
Future Streamlit/CLI UIs subscribe — no UI is required in core.

### 21.6 Plugin System (`webmaker/plugins/` + `plugins/`)

Optional plugins implement:

- `before_phase` / `after_phase`  
- `before_job` / `after_job`  

Register in-process or drop `*.py` into top-level `plugins/` exposing `PLUGIN` or `create_plugin()`.  
Hook failures are logged and swallowed. **Zero plugins = full pipeline unchanged.**

### 21.7 Extension Guide (updated)

| Want to… | Do this |
|----------|---------|
| Run one task only | `ProjectManager.run_job(JobType.RUN_QA)` (etc.) |
| Skip duplicate AI calls | Leave AI cache enabled (default) |
| Edit prompts | Change files under `prompts/` — no Python edits |
| Bump JSON format | Increment `SCHEMA_VERSION`; keep loaders backward compatible |
| Show progress in UI | `progress.subscribe(callback)` |
| Add SEO / a11y / i18n | New `Plugin` subclass; do not edit core modules |