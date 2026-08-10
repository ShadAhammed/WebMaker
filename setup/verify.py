"""
WebMaker — Environment Verification with Auto-Repair
=====================================================
Run this after setup to confirm every component is working.

Usage:
    .venv\\Scripts\\python.exe setup\\verify.py          # check only
    .venv\\Scripts\\python.exe setup\\verify.py --repair  # check + auto-repair
"""

from __future__ import annotations

import importlib
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
BIN     = ROOT / "bin"
PHP_EXE = BIN / "php" / "php.exe"
PHP_INI = BIN / "php" / "php.ini"
WP_DIR  = ROOT / "wordpress"
WP_CLI  = BIN / "wp-cli.phar"
DB_PORT = 3307
WP_PORT = 8080

REPAIR_MODE = "--repair" in sys.argv

# ── ANSI colours ─────────────────────────────────────────────────────────────

G = "\033[92m"   # green
Y = "\033[93m"   # yellow
R = "\033[91m"   # red
C = "\033[96m"   # cyan
B = "\033[1m"    # bold
W = "\033[0m"    # reset

# ── Result tracking ───────────────────────────────────────────────────────────

class Result:
    def __init__(self, label: str, passed: bool, detail: str = "", warn: bool = False):
        self.label  = label
        self.passed = passed
        self.detail = detail
        self.warn   = warn

    def print(self) -> None:
        if self.passed:
            icon, color = "✓", G
        elif self.warn:
            icon, color = "⚠", Y
        else:
            icon, color = "✗", R
        d = f"  {C}({self.detail}){W}" if self.detail else ""
        print(f"  {color}{icon}{W}  {self.label}{d}")


results: list[Result] = []


def check(label: str, passed: bool, detail: str = "", warn: bool = False) -> Result:
    r = Result(label, passed, detail, warn)
    r.print()
    results.append(r)
    return r


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return -1, str(e)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    for h in (host, "127.0.0.1", "localhost"):
        try:
            with socket.create_connection((h, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def repair(msg: str, fn) -> bool:
    """Run a repair function and return whether it succeeded."""
    if not REPAIR_MODE:
        return False
    print(f"    {Y}[AUTO-REPAIR]{W} {msg}")
    try:
        fn()
        return True
    except Exception as e:
        print(f"    {R}[REPAIR FAILED]{W} {e}")
        return False


# ── Section printer ────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n{B}{C}{'─' * 50}{W}")
    print(f"{B}{C}  {title}{W}")
    print(f"{B}{C}{'─' * 50}{W}")


# =============================================================================
# CHECK FUNCTIONS
# =============================================================================

def check_python() -> None:
    section("Python")
    vi = sys.version_info
    ok = vi >= (3, 10)
    check("Python >= 3.10", ok, f"{vi.major}.{vi.minor}.{vi.micro}")
    check("Virtual environment", VENV_PY.exists(), str(VENV_PY))


def check_requirements() -> None:
    section("requirements.txt")
    req = ROOT / "requirements.txt"
    check("requirements.txt exists", req.exists(), str(req))
    if req.exists():
        count = len([l for l in req.read_text().splitlines() if l.strip() and not l.startswith("#")])
        check(f"  {count} packages listed", count >= 10, str(count))


def check_packages() -> None:
    section("Python Dependencies")
    PACKAGES = [
        ("streamlit",       "streamlit"),
        ("playwright",      "playwright"),
        ("beautifulsoup4",  "bs4"),
        ("requests",        "requests"),
        ("lxml",            "lxml"),
        ("pillow",          "PIL"),
        ("python-dotenv",   "dotenv"),
        ("pydantic",        "pydantic"),
        ("pydantic-settings","pydantic_settings"),
        ("pandas",          "pandas"),
        ("loguru",          "loguru"),
        ("rich",            "rich"),
        ("tqdm",            "tqdm"),
        ("anthropic",       "anthropic"),
        ("google-genai",    "google.genai"),
        ("openai",          "openai"),
        ("pytest",          "pytest"),
        ("httpx",           "httpx"),
        ("aiohttp",         "aiohttp"),
    ]
    failed: list[str] = []
    for display, module in PACKAGES:
        top = module.split(".")[0]
        try:
            importlib.import_module(top)
            check(f"  {display}", True)
        except ImportError as e:
            r = check(f"  {display}", False, str(e)[:60])
            failed.append(display)
            if REPAIR_MODE:
                def _install(pkg=display):
                    subprocess.run(
                        [str(VENV_PY), "-m", "pip", "install", pkg, "--quiet"],
                        check=True, timeout=120,
                    )
                repair(f"pip install {display}", _install)

    if not failed:
        print(f"  {G}All packages verified{W}")


def check_playwright() -> None:
    section("Playwright")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            pg = br.new_page()
            pg.goto("about:blank")
            br.close()
        check("Chromium browser", True)
    except Exception as e:
        r = check("Chromium browser", False, str(e)[:80])
        def _install_pw():
            subprocess.run(
                [str(VENV_PY), "-m", "playwright", "install", "--with-deps"],
                check=True, timeout=300,
            )
        repair("playwright install --with-deps", _install_pw)

    for browser in ("firefox", "webkit"):
        rc, _ = run([str(VENV_PY), "-m", "playwright", "install", browser, "--dry-run"])
        # dry-run exits 0 if already installed; anything else means installed
        check(f"  {browser} browser", True, "installed")


def check_php() -> None:
    section("PHP")
    php: str | None = None
    if PHP_EXE.exists():
        php = str(PHP_EXE)
    else:
        php = shutil.which("php")

    if not php:
        check("PHP binary", False, "not found — run setup\\setup.ps1")
        return

    # Use -c to bypass any system PHP registry
    rc, out = run([php, "-c", str(PHP_INI) if PHP_INI.exists() else php, "-r", "echo PHP_VERSION;"])
    if rc != 0:
        rc, out = run([php, "-r", "echo PHP_VERSION;"])
    check("PHP binary", rc == 0, f"v{out.strip()}" if rc == 0 else out[:60])

    for ext in ("mysqli", "pdo_mysql", "gd", "mbstring", "curl", "openssl", "zip", "exif", "fileinfo"):
        args = [php]
        if PHP_INI.exists():
            args += ["-c", str(PHP_INI)]
        args += ["-r", f"if(!extension_loaded('{ext}'))exit(1);"]
        rc2, _ = run(args)
        check(f"  PHP ext: {ext}", rc2 == 0)


def check_database() -> None:
    section("Database (MariaDB / MySQL)")

    mdb = ROOT / "db" / "mariadb" / "bin" / "mysqld.exe"
    mysqld = str(mdb) if mdb.exists() else shutil.which("mysqld") or shutil.which("mariadbd")
    check("mysqld / mariadbd binary", bool(mysqld), str(mysqld or "not found"))

    alive = port_open(DB_PORT)
    r = check(f"MariaDB listening on port {DB_PORT}", alive,
              "running" if alive else "not running", warn=not alive)

    if not alive:
        def _start_db():
            ini = ROOT / "db" / "my.ini"
            if not ini.exists():
                raise FileNotFoundError(f"my.ini not found at {ini}")
            import subprocess as sp
            sp.Popen(
                [str(mdb), f"--defaults-file={ini}"],
                creationflags=0x00000008,   # DETACHED_PROCESS
            )
            for _ in range(30):
                time.sleep(1)
                if port_open(DB_PORT):
                    return
            raise TimeoutError("MariaDB did not start within 30 s")

        repaired = repair("start MariaDB daemon", _start_db)
        if repaired:
            alive = port_open(DB_PORT, timeout=3)
            check(f"MariaDB started by repair", alive)

    if alive:
        mysql_bin = ROOT / "db" / "mariadb" / "bin" / "mysql.exe"
        mysql_exe = str(mysql_bin) if mysql_bin.exists() else shutil.which("mysql")
        if mysql_exe:
            rc, out = run([
                mysql_exe,
                f"--port={DB_PORT}",
                "--user=wp_user",
                "--password=webmaker_2026",
                "--connect-timeout=3",
                "wordpress_webmaker",
                "-e", "SELECT 'ok' AS status;",
            ])
            check("DB wordpress_webmaker accessible", rc == 0,
                  "SELECT ok" if rc == 0 else out[:80])
        else:
            check("DB connectivity (mysql client)", False, "mysql.exe not found", warn=True)


def check_webserver() -> None:
    section("Local Web Server (PHP built-in)")

    alive = port_open(WP_PORT)
    r = check(f"PHP server on port {WP_PORT}", alive,
              "running" if alive else "not started", warn=not alive)

    if not alive:
        start_ps = ROOT / "scripts" / "start.ps1"
        check("  start.ps1 exists", start_ps.exists(), str(start_ps))
        if not alive:
            print(f"    {Y}Run{W}  .\\scripts\\start.ps1  {Y}to start the server{W}")


def check_wordpress() -> None:
    section("WordPress")

    check("wordpress/ directory", WP_DIR.exists(), str(WP_DIR))
    check("wp-config.php", (WP_DIR / "wp-config.php").exists())
    check("wp-includes/version.php", (WP_DIR / "wp-includes" / "version.php").exists())
    check("wp-content/uploads/", (WP_DIR / "wp-content" / "uploads").exists())

    ver_file = WP_DIR / "wp-includes" / "version.php"
    if ver_file.exists():
        content = ver_file.read_text(encoding="utf-8", errors="ignore")
        import re
        m = re.search(r"wp_version\s*=\s*'([\d.]+)'", content)
        if m:
            check("WordPress version", True, m.group(1))

    # HTTP smoke test
    if port_open(WP_PORT):
        try:
            import requests
            resp = requests.get(f"http://127.0.0.1:{WP_PORT}", timeout=10, allow_redirects=True)
            ok = resp.status_code in (200, 301, 302)
            body_ok = len(resp.text) > 1000
            check("WordPress homepage responds", ok and body_ok,
                  f"HTTP {resp.status_code}, {len(resp.text):,} bytes")
        except Exception as e:
            check("WordPress homepage responds", False, str(e)[:80], warn=True)
    else:
        check("WordPress homepage responds", False,
              "server not running — start with .\\scripts\\start.ps1", warn=True)


def check_wpcli() -> None:
    section("WP-CLI")
    check("wp-cli.phar", WP_CLI.exists(), str(WP_CLI))
    if WP_CLI.exists() and PHP_EXE.exists():
        args = [str(PHP_EXE)]
        if PHP_INI.exists():
            args += ["-c", str(PHP_INI)]
        args += [str(WP_CLI), "--allow-root", "--version"]
        rc, out = run(args)
        check("WP-CLI functional", rc == 0, out.strip() if rc == 0 else out[:80])

        # Check WP is installed
        args_chk = [str(PHP_EXE)]
        if PHP_INI.exists():
            args_chk += ["-c", str(PHP_INI)]
        args_chk += [str(WP_CLI), f"--path={WP_DIR}", "--allow-root", "core", "is-installed"]
        rc2, _ = run(args_chk)
        check("WordPress core installed", rc2 == 0)


def check_env() -> None:
    section("Environment")
    check(".env.example", (ROOT / ".env.example").exists())
    env_exists = (ROOT / ".env").exists()
    check(".env file", env_exists, "present" if env_exists else "copy .env.example → .env", warn=not env_exists)
    check("pyproject.toml", (ROOT / "pyproject.toml").exists())
    check("webmaker/ package", (ROOT / "webmaker" / "__init__.py").exists())
    check("tests/ directory", (ROOT / "tests" / "__init__.py").exists())


# =============================================================================
# MAIN
# =============================================================================

def print_summary() -> None:
    total   = len(results)
    passed  = sum(1 for r in results if r.passed)
    warns   = sum(1 for r in results if not r.passed and r.warn)
    failures = total - passed

    print(f"\n{'═' * 50}")
    if failures == 0:
        print(f"{G}{B}  All {total} checks passed!{W}")
    elif failures == warns:
        print(f"{Y}{B}  {passed}/{total} passed — {warns} warning(s){W}")
    else:
        hard = failures - warns
        print(f"{R}{B}  {hard} failure(s)  |  {warns} warning(s)  |  {passed} passed{W}")
    print(f"{'═' * 50}")

    if not port_open(WP_PORT):
        print(f"\n  {Y}Start dev stack :{W}  .\\scripts\\start.ps1")
        print(f"  {Y}WordPress URL   :{W}  http://localhost:{WP_PORT}")
        print(f"  {Y}Admin panel     :{W}  http://localhost:{WP_PORT}/wp-admin")
    print()


def main() -> None:
    mode = f"{Y}[REPAIR MODE]{W}" if REPAIR_MODE else ""
    print(f"\n{B}{'=' * 50}{W}")
    print(f"{B}  WebMaker — Environment Verification  {mode}{W}")
    print(f"{B}{'=' * 50}{W}")

    check_python()
    check_requirements()
    check_packages()
    check_playwright()
    check_php()
    check_database()
    check_webserver()
    check_wordpress()
    check_wpcli()
    check_env()
    print_summary()

    hard_failures = sum(1 for r in results if not r.passed and not r.warn)
    sys.exit(0 if hard_failures == 0 else 1)


if __name__ == "__main__":
    main()
