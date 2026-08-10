# WebMaker

Turn an existing business website into a modern local WordPress demo, with AI support and human approval before anything goes live on the demo.

Built for presenting website improvements clearly: crawl the current site, review concrete content changes, and show a working WordPress result on your machine.

## Why WebMaker

Most local businesses already have a website. The hard part is showing what a better version could look like without rebuilding everything from scratch or pushing unfinished work to production.

WebMaker closes that gap. You point it at a public URL, analyze the business and competitors, approve the copy you want, and render a local demo you can walk through with a client.

## What it does

- **Crawl and analyze** public pages with Playwright (screenshots, structure, assets)
- **Profile the business** with Claude and map competitor structure with DeepSeek
- **Review content** in OP-Content: AI suggestions, you decide what gets applied
- **Pick a theme** from curated WordPress starters for the local demo
- **Render approved copy** into a local WordPress site (no AI rewrite at render time)
- **Run QA checks** on content and visual quality before you present

This is a local presentation and development tool. It is not a hosted SaaS and it does not auto-deploy to a client’s production server.

## How the pipeline works

```text
Target URL (+ optional competitors)
  -> Crawl / acquisition artifacts
  -> Business analysis (Claude)
  -> Competitor analysis (DeepSeek)
  -> OP-Content review (Claude) + your approval
  -> Design pattern selection (GPT + catalog fallback)
  -> Live demo render (approved copy only)
  -> QA (content + visual)
```

Artifacts land in `projects/<slug>/artifacts/`. Full detail is in [`docs/architecture.md`](docs/architecture.md).

## Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.10+ |
| Desktop UI | Tkinter (`webmake`) |
| Crawling | Playwright |
| AI | Claude, DeepSeek, OpenAI GPT |
| Config | Pydantic Settings, `.env`, `config/webmaker.yaml` |
| Demo site | WordPress (portable PHP + MariaDB + WP-CLI) |
| Tests | pytest |

## Quick start

**You need:** Windows 10/11, Python 3.10+, and API keys for the providers you want to use.

```powershell
cd WebMaker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Add your keys to `.env`, then install the local WordPress runtime:

```powershell
.\setup\setup.ps1
python run.py verify
```

## Configuration

Copy `.env.example` to `.env` and fill in your own keys. Never commit `.env`.

| Variable | Purpose |
|----------|---------|
| `CLAUDE_API_KEY` | Business analysis and OP-Content |
| `DEEPSEEK_API_KEY` | Competitor structure |
| `GPT_API_KEY` | Design patterns and visual QA |
| `GEMINI_API_KEY` | Optional / legacy |
| `WP_ADMIN_USER` / `WP_ADMIN_PASS` | Local WordPress admin (first setup) |
| `WEB_PORT` / `DB_PORT` | Local ports (defaults `8080` / `3307`) |

More options live in `config/webmaker.yaml` and `webmaker/config/settings.py`.

## Usage

```powershell
# WordPress demo at http://localhost:8080
python run.py start

# Desktop UI + browser
.\webmake
```

Three tabs in the UI:

1. **Crawl & Analyze** – crawl the target (and competitors), run analysis
2. **OP-Content** – review tips, approve copy, render into the demo
3. **Theme** – apply a curated theme to the local WordPress site

Also useful:

```powershell
python run.py verify
python run.py info
python run.py stop
```

## Try a demo flow

1. Put your API keys in `.env`
2. Start with `.\webmake`
3. Enter a public target URL and a project name (for example `DemoBiz`)
4. Add competitor URLs if you want
5. Run **Crawl & Analyze**
6. Open **OP-Content**, tick what you approve
7. Render and open `http://localhost:8080`

Project files stay under `projects/<slug>/` on your machine. That folder is gitignored so client work never ships with the repo.

## Project layout

```text
WebMaker/
├── README.md
├── LICENSE
├── .env.example
├── .gitignore
├── app.py / run.py              Entry points
├── webmake.ps1 / webmake.cmd    Launcher
├── requirements.txt
├── pyproject.toml
├── config/webmaker.yaml
├── webmaker/                    Core package (agents, modules, UI, schemas)
├── prompts/                     System prompts
├── setup/                       Environment + WordPress setup
├── tests/
├── docs/
├── projects/                    Local workspaces (gitignored)
└── (local only) .venv/, bin/, db/, wordpress/, cache/, logs/, Library/, Binary/
```

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest tests/unit/ -q
```

Integration tests need MariaDB and PHP running locally.

## Honest limits

- Setup scripts are built for Windows first
- The full pipeline needs third-party AI API keys
- WordPress, PHP, and MariaDB are installed locally; they are not in this git repo
- Demo quality still depends on the source site and your OP-Content approvals
- This tool prepares a local demo. It does not promise rankings or production hosting

## What’s next

- Smoother first-run setup and clearer install docs
- An anonymized sample project for demos without client data
- Stronger render checks so only approved copy reaches the demo pages

## License

Proprietary source-available. See [`LICENSE`](LICENSE).

You can view and run the code for personal, non-commercial evaluation.
**Commercial use needs prior written permission.**

WordPress, themes, plugins, PHP, and MariaDB keep their own licenses when you install them through setup.
