# WebMaker

Local AI-assisted toolkit that crawls a public business website, analyzes it, and builds a modern WordPress demo you can review on your machine.

## Why it exists

Service businesses often need a clearer website before they invest in a full redesign. WebMaker turns an existing public site into a local WordPress demo with human-approved content changes, so you can present concrete before/after work without deploying to production.

## Key capabilities

- **Crawl & analyze** — Playwright crawl of a target site (and optional competitors), screenshots, and structured page extraction
- **Business + competitor analysis** — Claude for business profiling, DeepSeek for competitor structure (configurable providers)
- **OP-Content review** — AI content recommendations with human tick-to-approve before anything is published to the demo
- **Theme selection** — curated WordPress theme / starter template application for the local demo
- **Local WordPress demo** — portable PHP + MariaDB workflow for an offline presentation site
- **QA hooks** — content and visual review agents for the generated demo

WebMaker is a **local development / presentation tool**, not a hosted SaaS and not an automatic production deployer.

## Architecture overview

Primary V2 path (orchestrated agents; agents do not call each other):

```text
Target URL (+ optional competitors)
  → Crawl / acquisition artifacts
  → Business analysis (Claude)
  → Competitor analysis (DeepSeek)
  → OP-Content review (Claude) + human approval
  → Design pattern selection (GPT + catalog fallback)
  → Live demo render (no AI — approved copy only)
  → QA (content + visual)
```

Artifacts are stored under `projects/<slug>/artifacts/`. Schemas live in `webmaker/schemas/`. See [`docs/architecture.md`](docs/architecture.md) for the full pipeline.

## Technology stack

| Layer | Technologies |
|-------|----------------|
| Language | Python 3.10+ |
| UI | Tkinter desktop app (`webmake`) |
| Crawl | Playwright |
| AI | Anthropic Claude, DeepSeek, OpenAI GPT (Gemini adapter optional / unused on V2 path) |
| Config | Pydantic Settings, `.env`, `config/webmaker.yaml` |
| Demo site | WordPress via portable PHP + MariaDB + WP-CLI |
| Tests | pytest |

## Installation

### Prerequisites

- Windows 10/11 (current setup scripts target PowerShell)
- Python 3.10+
- Network access to install dependencies and (optionally) AI provider APIs

### Setup

```powershell
cd WebMaker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
# Edit .env and add at least CLAUDE_API_KEY, DEEPSEEK_API_KEY, GPT_API_KEY as needed
```

Install the local WordPress runtime (PHP, MariaDB, WordPress tree):

```powershell
.\setup\setup.ps1
python run.py verify
```

## Configuration

Copy `.env.example` → `.env` and set keys locally. **Never commit `.env`.**

| Variable | Purpose |
|----------|---------|
| `CLAUDE_API_KEY` | Business analysis + OP-Content (V2) |
| `DEEPSEEK_API_KEY` | Competitor structure analysis |
| `GPT_API_KEY` | Design pattern selection + visual QA |
| `GEMINI_API_KEY` | Optional / legacy path |
| `WP_ADMIN_USER` / `WP_ADMIN_PASS` | Local demo WordPress admin (first setup) |
| `WEB_PORT` / `DB_PORT` | Local server ports (defaults `8080` / `3307`) |

See `config/webmaker.yaml` and `webmaker/config/settings.py` for the full settings surface.

## Usage

```powershell
# Start MariaDB + PHP (WordPress at http://localhost:8080)
python run.py start

# Launch Tk UI + open the demo browser
.\webmake
```

V2 UI tabs:

1. **Crawl & Analyze** — crawl target + competitors, run AI analysis
2. **OP-Content** — review tips, approve copy, render into the demo
3. **Theme** — apply a curated theme/template to the local WordPress site

CLI helpers:

```powershell
python run.py verify
python run.py info
python run.py stop
```

## Example

1. Set API keys in `.env`
2. Run `.\webmake`
3. Enter a **public** target URL and a project name (for example `DemoBiz`)
4. Optionally paste competitor URLs
5. Run **Crawl & Analyze**
6. Open **OP-Content**, approve tips you want applied
7. Render approved copy into the local WordPress demo at `http://localhost:8080`

Project state appears under `projects/<slug>/` on your machine (not published with this repo by default).

## Screenshots / demo

Add portfolio screenshots here when you publish (for example a blurred/anonymized before→after of a demo). Do not upload client screenshots that contain personal data unless you have permission.

## Project structure

```text
WebMaker/
├── README.md
├── LICENSE
├── .env.example
├── .gitignore
├── app.py / run.py          Entry points
├── webmake.ps1 / webmake.cmd
├── requirements.txt
├── pyproject.toml
├── config/webmaker.yaml
├── webmaker/                Python package (agents, modules, UI, schemas)
├── prompts/                 Markdown system prompts
├── setup/                   Environment + WordPress setup scripts
├── tests/                   Unit + integration tests
├── docs/                    Architecture documentation
├── projects/                Local project workspaces (fully gitignored)
├── assets/ / templates/ / plugins/ / outputs/
└── (local only) .venv/, bin/, db/, wordpress/, cache/, logs/, Library/, Binary/
```

## Testing

```powershell
.\.venv\Scripts\Activate.ps1
pytest tests/unit/ -q
```

Integration tests expect the local MariaDB/PHP stack to be running.

## Limitations

- Optimized for **local Windows** setup scripts; other OS support is not packaged yet
- Requires third-party AI API keys for the full V2 pipeline
- WordPress, PHP, and MariaDB are installed locally and are **not** shipped in the public git tree
- Output quality depends on source-site structure and human review of OP-Content tips
- Not a one-click production host or SEO ranking guarantee

## Roadmap

- Cleaner first-run setup and cross-platform install docs
- Optional anonymized sample project for demos without client data
- Continued hardening of render materialization (approved copy only on demo pages)

## License

Proprietary source-available — see [`LICENSE`](LICENSE).

You may view and evaluate the code for personal, non-commercial learning.
**Commercial use requires prior written permission** from the copyright holder.

Third-party software (WordPress, themes/plugins, PHP, MariaDB) remains under their own licenses when you install them locally via setup.
