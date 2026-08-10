"""
WebMaker — Python environment setup.
Creates/reuses a virtual environment and installs all dependencies.
Run with the system Python: python setup/setup.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
REQ = ROOT / "requirements.txt"
PY_MIN = (3, 10)


def _run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def _pip() -> Path:
    return VENV / "Scripts" / "pip.exe"


def _python() -> Path:
    return VENV / "Scripts" / "python.exe"


def step(msg: str) -> None:
    print(f"\n[*] {msg}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


# ── Python version check ────────────────────────────────────────────────────

step("Checking Python version")
if sys.version_info < PY_MIN:
    fail(f"Python {PY_MIN[0]}.{PY_MIN[1]}+ required — found {sys.version}")
    sys.exit(1)
ok(f"Python {sys.version}")


# ── Virtual environment ─────────────────────────────────────────────────────

step("Virtual environment")
venv_py = _python()
if venv_py.exists():
    ok(f"Reusing existing venv at {VENV}")
else:
    print(f"  Creating .venv at {VENV}…")
    _run([sys.executable, "-m", "venv", str(VENV)])
    ok("Virtual environment created")


# ── Upgrade pip ─────────────────────────────────────────────────────────────

step("Upgrading pip / setuptools / wheel")
# Must use 'python -m pip' (not pip.exe) to upgrade pip itself on Windows
_run([str(_python()), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "--quiet"])
ok("pip up to date")


# ── Install packages ─────────────────────────────────────────────────────────

step(f"Installing packages from {REQ.name}")
_run([str(_pip()), "install", "-r", str(REQ), "--quiet"])
ok("All packages installed")


# ── Playwright browsers ──────────────────────────────────────────────────────

step("Installing Playwright browsers (Chromium + Firefox + WebKit)")
_run([str(_python()), "-m", "playwright", "install", "--with-deps"])
ok("Playwright browsers installed")


# ── Verify imports ──────────────────────────────────────────────────────────

step("Verifying package imports")
packages = [
    ("streamlit", "streamlit"),
    ("playwright", "playwright"),
    ("beautifulsoup4", "bs4"),
    ("requests", "requests"),
    ("lxml", "lxml"),
    ("pillow", "PIL"),
    ("python-dotenv", "dotenv"),
    ("pydantic", "pydantic"),
    ("pandas", "pandas"),
    ("loguru", "loguru"),
    ("rich", "rich"),
    ("tqdm", "tqdm"),
    ("anthropic", "anthropic"),
    ("google-genai", "google.genai"),
    ("openai", "openai"),
    ("pytest", "pytest"),
    ("httpx", "httpx"),
    ("aiohttp", "aiohttp"),
]

failed = []
for display, module in packages:
    result = subprocess.run(
        [str(_python()), "-c", f"import {module.split('.')[0]}"],
        capture_output=True,
    )
    if result.returncode == 0:
        ok(display)
    else:
        fail(f"{display}  <- import failed")
        failed.append(display)

print()
if failed:
    warn(f"{len(failed)} package(s) failed to import: {', '.join(failed)}")
    sys.exit(1)
else:
    print("\033[92m  All packages verified successfully.\033[0m")

print()
print("  Next step: run  setup\\setup.ps1  (PowerShell, as Administrator if needed)")
