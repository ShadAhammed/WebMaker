"""
WebMaker — Unified Entry Point
==============================
Usage:
    python run.py verify           Verify the development environment
    python run.py verify --repair  Verify and auto-repair where possible
    python run.py start            Start the local dev stack (MariaDB + PHP server)
    python run.py stop             Stop the PHP server and MariaDB
    python run.py info             Show environment summary
    python run.py webmake          Open the webmake Tkinter desk app
    python run.py start-app        Alias for webmake (deprecated)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Load WebMaker/.env into os.environ if present (no dependency required)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


def _venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def _ensure_venv() -> None:
    if not _venv_python().exists():
        print("Virtual environment not found — run:  python setup\\setup.py")
        sys.exit(1)


def cmd_verify(args: list[str]) -> None:
    _ensure_venv()
    extra = ["--repair"] if "--repair" in args else []
    rc = subprocess.run(
        [str(_venv_python()), "setup/verify.py"] + extra,
        cwd=ROOT,
    ).returncode
    sys.exit(rc)


def cmd_webmake(_args: list[str]) -> None:
    """Launch webmake via the PowerShell launcher (MariaDB + PHP + Tk)."""
    ps1 = ROOT / "webmake.ps1"
    if not ps1.exists():
        print(f"Launcher not found: {ps1}")
        sys.exit(1)
    rc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
        ],
        cwd=ROOT,
    ).returncode
    sys.exit(rc)


def cmd_start_app(args: list[str]) -> None:
    """Deprecated alias for cmd_webmake."""
    cmd_webmake(args)


def cmd_start(_args: list[str]) -> None:
    import socket

    _load_dotenv()

    db_port = int(os.environ.get("DB_PORT", "3307"))
    wp_port = int(os.environ.get("WP_PORT") or os.environ.get("WEB_PORT") or "8080")
    wp_user = os.environ.get("WP_ADMIN_USER", "admin")
    wp_pass = os.environ.get("WP_ADMIN_PASS", "(set WP_ADMIN_PASS in .env)")

    print("\n  WebMaker — Starting dev environment\n")

    # ── MariaDB ──────────────────────────────────────────────────────────────
    def _db_up() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", db_port), 1):
                return True
        except OSError:
            return False

    if _db_up():
        print(f"  [OK] MariaDB already running on port {db_port}")
    else:
        mysqld = ROOT / "db" / "mariadb" / "bin" / "mariadbd.exe"
        if not mysqld.exists():
            mysqld = ROOT / "db" / "mariadb" / "bin" / "mysqld.exe"
        my_ini = ROOT / "db" / "my.ini"
        if not mysqld.exists() or not my_ini.exists():
            print("  [WARN] MariaDB not found — run setup\\setup.ps1 first")
        else:
            print(f"  Starting MariaDB on port {db_port}...")
            subprocess.Popen(
                [str(mysqld), f"--defaults-file={my_ini}"],
                creationflags=0x00000008,  # DETACHED_PROCESS
            )
            for _ in range(30):
                time.sleep(1)
                if _db_up():
                    print(f"  [OK] MariaDB started")
                    break
            else:
                print("  [FAIL] MariaDB did not start within 30 s")
                sys.exit(1)

    # ── PHP built-in server ───────────────────────────────────────────────────
    php_exe = ROOT / "bin" / "php" / "php.exe"
    php_ini = ROOT / "bin" / "php" / "php.ini"
    router  = ROOT / "scripts" / "router.php"
    wp_dir  = ROOT / "wordpress"

    if not php_exe.exists():
        print("  [FAIL] PHP not found — run setup\\setup.ps1")
        sys.exit(1)

    print(f"\n  WordPress  : http://localhost:{wp_port}")
    print(f"  Admin      : http://localhost:{wp_port}/wp-admin")
    print(f"  Username   : {wp_user}")
    print(f"  Password   : {wp_pass}")
    print(f"\n  Press Ctrl+C to stop the web server.\n")

    php_args = [str(php_exe)]
    if php_ini.exists():
        php_args += ["-c", str(php_ini)]
    php_args += ["-S", f"localhost:{wp_port}", "-t", str(wp_dir)]
    if router.exists():
        php_args.append(str(router))

    try:
        subprocess.run(php_args, cwd=ROOT)
    except KeyboardInterrupt:
        print("\n  Server stopped.")


def cmd_stop(_args: list[str]) -> None:
    import os
    killed = 0
    for proc in ("php.exe", "mariadbd.exe", "mysqld.exe"):
        rc = subprocess.run(
            ["taskkill", "/F", "/IM", proc],
            capture_output=True,
        ).returncode
        if rc == 0:
            print(f"  Stopped {proc}")
            killed += 1
    if killed == 0:
        print("  No WebMaker processes found running.")


def cmd_info(_args: list[str]) -> None:
    import socket

    def _up(port: int) -> str:
        try:
            with socket.create_connection(("127.0.0.1", port), 1):
                return "running"
        except OSError:
            return "stopped"

    print(f"\n  WebMaker — Environment Info")
    print(f"  {'─' * 40}")
    print(f"  Project root : {ROOT}")
    print(f"  Python       : {sys.version.split()[0]}")
    print(f"  Venv         : {_venv_python().parent.parent}")
    print(f"  WordPress    : http://localhost:8080  [{_up(8080)}]")
    print(f"  MariaDB      : 127.0.0.1:3307        [{_up(3307)}]")

    php = ROOT / "bin" / "php" / "php.exe"
    if php.exists():
        rc, ver = 0, ""
        try:
            ini = ROOT / "bin" / "php" / "php.ini"
            args = [str(php)]
            if ini.exists():
                args += ["-c", str(ini)]
            args += ["-r", "echo PHP_VERSION;"]
            out = subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True, timeout=5)
            ver = out.strip()
        except Exception:
            ver = "?"
        print(f"  PHP          : {ver}  ({php})")

    wp_ver_file = ROOT / "wordpress" / "wp-includes" / "version.php"
    if wp_ver_file.exists():
        import re
        m = re.search(r"wp_version\s*=\s*'([\d.]+)'", wp_ver_file.read_text())
        if m:
            print(f"  WordPress    : {m.group(1)}")
    print()


COMMANDS = {
    "verify":    cmd_verify,
    "start":     cmd_start,
    "stop":      cmd_stop,
    "info":      cmd_info,
    "webmake":   cmd_webmake,
    "start-app": cmd_start_app,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("Available commands:", ", ".join(COMMANDS))
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()
