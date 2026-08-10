"""
webmaker.config.settings
=========================
Centralised typed configuration using Pydantic BaseSettings.

Values are resolved in this order (highest priority first):
1. Real environment variables
2. .env file (project root)
3. Field defaults defined here

No path or credential is hard-coded.  Every default is expressed as a
relative offset from *project_root* so the project can be relocated.

Usage::

    from webmaker.config.settings import settings

    print(settings.wordpress_url)
    print(settings.gemini_api_key)
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load .env before Pydantic reads env vars so file values are visible.
_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """All runtime configuration for WebMaker.

    Fields are grouped by concern.  Most can be overridden from a .env
    file or shell environment variable with the same name (case-insensitive).
    """

    # ── Paths ──────────────────────────────────────────────────────────────────

    project_root:    Path = Field(default=_ROOT)

    # Runtime data directories (created automatically when needed)
    logs_dir:        Path = Field(default=_ROOT / "logs")
    cache_dir:       Path = Field(default=_ROOT / "cache")
    projects_dir:    Path = Field(default=_ROOT / "projects")
    outputs_dir:     Path = Field(default=_ROOT / "outputs")
    assets_dir:      Path = Field(default=_ROOT / "assets")
    templates_dir:   Path = Field(default=_ROOT / "templates")

    # Source-tree directories
    config_dir:      Path = Field(default=_ROOT / "config")
    docs_dir:        Path = Field(default=_ROOT / "docs")

    # WordPress installation
    wordpress_dir:   Path = Field(default=_ROOT / "wordpress")

    # Binary paths
    bin_dir:         Path = Field(default=_ROOT / "bin")
    php_dir:         Path = Field(default=_ROOT / "bin" / "php")
    mariadb_dir:     Path = Field(default=_ROOT / "bin" / "mariadb")
    wpcli_path:      Path = Field(default=_ROOT / "bin" / "wp-cli.phar")

    # ── Web server ────────────────────────────────────────────────────────────

    server_host:     str  = Field(default="localhost", alias="WEB_HOST")
    server_port:     int  = Field(default=8080,        alias="WEB_PORT")

    @property
    def wordpress_url(self) -> str:
        return f"http://{self.server_host}:{self.server_port}"

    # ── Database ──────────────────────────────────────────────────────────────

    db_host:         str  = Field(default="127.0.0.1",      alias="DB_HOST")
    db_port:         int  = Field(default=3307,              alias="DB_PORT")
    db_name:         str  = Field(default="webmaker_wp",     alias="DB_NAME")
    db_user:         str  = Field(default="root",            alias="DB_USER")
    db_password:     str  = Field(default="",                alias="DB_PASSWORD")

    @property
    def db_dsn(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ── WordPress admin ───────────────────────────────────────────────────────

    wp_admin_user:   str  = Field(default="admin",            alias="WP_ADMIN_USER")
    wp_admin_pass:   str  = Field(default="admin",            alias="WP_ADMIN_PASS")
    wp_admin_email:  str  = Field(default="admin@webmaker.local", alias="WP_ADMIN_EMAIL")

    # ── AI providers ──────────────────────────────────────────────────────────

    gemini_api_key:   str = Field(default="", alias="GEMINI_API_KEY")
    claude_api_key:   str = Field(default="", alias="CLAUDE_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    gpt_api_key:      str = Field(default="", alias="GPT_API_KEY")

    # Preferred AI provider (gemini | claude | deepseek | openai | auto)
    ai_provider:      str = Field(default="auto", alias="AI_PROVIDER")

    # Model names – can be overridden per-provider
    gemini_model:     str = Field(default="gemini-3.1-pro-preview", alias="GEMINI_MODEL")
    claude_model:     str = Field(default="claude-sonnet-4-6", alias="CLAUDE_MODEL")
    deepseek_model:   str = Field(default="deepseek-chat",    alias="DEEPSEEK_MODEL")
    gpt_model:        str = Field(default="gpt-5.5-pro", alias="GPT_MODEL")

    # ── Crawler ───────────────────────────────────────────────────────────────

    crawler_max_depth:   int   = Field(default=3,    alias="CRAWLER_MAX_DEPTH")
    crawler_max_pages:   int   = Field(default=50,   alias="CRAWLER_MAX_PAGES")
    crawler_timeout_s:   float = Field(default=30.0, alias="CRAWLER_TIMEOUT")
    crawler_concurrency: int   = Field(default=3,    alias="CRAWLER_CONCURRENCY")
    crawler_respect_robots: bool = Field(default=True, alias="CRAWLER_RESPECT_ROBOTS")

    # ── Competitor analysis ───────────────────────────────────────────────────

    competitor_max_count: int = Field(default=10, alias="COMPETITOR_MAX")

    # ── Logging ───────────────────────────────────────────────────────────────

    log_level:       str  = Field(default="INFO", alias="LOG_LEVEL")
    log_filename:    str  = Field(default="webmaker.log")

    # ── Pydantic settings config ──────────────────────────────────────────────

    model_config = {
        "env_file":         str(_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra":            "ignore",
        "populate_by_name": True,
    }

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def php_exe(self) -> Path:
        return self.php_dir / "php.exe"

    @property
    def php_ini(self) -> Path:
        return self.php_dir / "php.ini"


# Singleton instance — import this rather than instantiating Settings directly.
settings = Settings()
