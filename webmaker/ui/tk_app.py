"""
webmaker.ui.tk_app
==================
Tkinter desktop app with four V2 tabs:

1. Crawl        — Agent 0: acquire website package + validation (no AI)
2. Migrate      — theme + template → clone as-is into WordPress
3. Analyze      — business (Claude) + competitors (DeepSeek)
4. OP-Content   — review + tick AI recommendations → render

Launch via::

    .\\webmake
    .\\webmake.ps1
    python run.py webmake
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from urllib.parse import urlparse

from webmaker.config.settings import settings
from webmaker.core.exceptions import ProjectAlreadyExistsError, WebMakerError
from webmaker.core.logging import get_logger, setup_logging
from webmaker.core.progress import ProgressEvent
from webmaker.core.types import ProjectStatus
from webmaker.data.theme_catalog import THEMES, get_template
from webmaker.modules.project_manager import ProjectManager
from webmaker.orchestrator import Orchestrator
from webmaker.schemas import MigrationResult, ModernizeResult, OpContent
from webmaker.schemas.acquisition import WebsitePackageResult

log = get_logger("tk_app")

_SKIP_LATER = ("optimize", "generate", "review", "fix")

# Defaults are empty — last session is restored from cache/ui_prefs.json.
_DEFAULT_TARGET_URL = ""
_DEFAULT_PROJECT_NAME = ""
_UI_PREFS_FILE = settings.cache_dir / "ui_prefs.json"


def _load_ui_prefs() -> dict:
    try:
        if _UI_PREFS_FILE.is_file():
            import json
            data = json.loads(_UI_PREFS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_ui_prefs(data: dict) -> None:
    try:
        import json
        _UI_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _UI_PREFS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("Could not save UI prefs: {e}", e=exc)


def _parse_competitor_urls(raw: str) -> list[str]:
    urls: list[str] = []
    for line in (raw or "").splitlines():
        u = line.strip()
        if not u or u.startswith("#"):
            continue
        urls.append(u)
    return urls


def _looks_like_url(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    return bool(parsed.netloc) and "." in parsed.netloc


def _normalise_url(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    return text


class WebMakerApp:
    """Main Tk window — V2 tabs: Migrate, Crawl & Analyze, OP-Content."""

    def __init__(self, root: tk.Tk) -> None:
        self.root     = root
        self.root.title("webmake")
        self.root.minsize(680, 700)
        self.root.geometry("800x780")

        self._manager: ProjectManager | None = None
        self._running = False

        self._build()
        self._refresh_projects_all()
        self._set_status("Ready")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text="webmake", font=("Segoe UI", 16, "bold")).pack(
            anchor=tk.W, pady=(0, 4)
        )

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_acquire = ttk.Frame(self.notebook, padding=10)
        self.tab_migrate = ttk.Frame(self.notebook, padding=10)
        self.tab_crawl   = ttk.Frame(self.notebook, padding=10)
        self.tab_op      = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_acquire, text="Crawl")
        self.notebook.add(self.tab_migrate, text="Migrate")
        self.notebook.add(self.tab_crawl,   text="Analyze")
        self.notebook.add(self.tab_op,      text="OP-Content")

        self._build_acquire_tab()
        self._build_migrate_tab()
        self._build_crawl_tab()
        self._build_op_content_tab()

        # Shared status / progress / log under tabs
        bottom = ttk.Frame(outer)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        row = ttk.Frame(bottom)
        row.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.status_var).pack(side=tk.LEFT)

        ttk.Label(bottom, text="Progress").pack(anchor=tk.W, pady=(6, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(2, 6))

        ttk.Label(bottom, text="Log").pack(anchor=tk.W)
        self.log_box = scrolledtext.ScrolledText(
            bottom, height=10, wrap=tk.WORD, state=tk.DISABLED
        )
        self.log_box.pack(fill=tk.BOTH, expand=True)

    # ── Tab 1: Crawl (Agent 0 — Acquisition & Validation) ─────────────────────

    def _build_acquire_tab(self) -> None:
        pad = {"padx": 0, "pady": 4}
        frm = self.tab_acquire

        ttk.Label(
            frm,
            text=(
                "Step 1 — Crawl.  Acquire the target website into a Website Package "
                "(HTML, assets, content, brand, layout, screenshots) and validate "
                "completeness.  No AI.  No migration."
            ),
            wraplength=740,
        ).pack(anchor=tk.W, pady=(0, 8))

        prefs = _load_ui_prefs()
        url0 = str(prefs.get("target_url") or _DEFAULT_TARGET_URL).strip()
        name0 = str(prefs.get("project_name") or _DEFAULT_PROJECT_NAME).strip()

        ttk.Label(frm, text="Website URL").pack(anchor=tk.W)
        self.acquire_url_var = tk.StringVar(value=url0)
        ttk.Entry(frm, textvariable=self.acquire_url_var).pack(fill=tk.X, **pad)

        ttk.Label(frm, text="Project name / folder").pack(anchor=tk.W, pady=(6, 0))
        self.acquire_name_var = tk.StringVar(value=name0)
        ttk.Entry(frm, textvariable=self.acquire_name_var).pack(fill=tk.X, **pad)

        self.acquire_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm,
            text="Force re-crawl (ignore existing pages.json)",
            variable=self.acquire_force_var,
        ).pack(anchor=tk.W, pady=(6, 0))

        row = ttk.Frame(frm)
        row.pack(fill=tk.X, pady=(10, 4))
        self.acquire_btn = ttk.Button(
            row,
            text="Acquire & Validate",
            command=self._on_acquire,
        )
        self.acquire_btn.pack(side=tk.LEFT)

        self.acquire_score_var = tk.StringVar(value="No acquisition report yet.")
        ttk.Label(
            frm, textvariable=self.acquire_score_var, wraplength=740, foreground="#333"
        ).pack(anchor=tk.W, pady=(8, 4))

        ttk.Label(frm, text="Per-page extraction counts").pack(anchor=tk.W)
        cols = (
            "slug", "h1", "h2", "h3", "p", "img", "btn", "forms", "lists",
            "sections", "links", "shot",
        )
        tree_frm = ttk.Frame(frm)
        tree_frm.pack(fill=tk.BOTH, expand=True, pady=(2, 4))
        self.acquire_tree = ttk.Treeview(
            tree_frm, columns=cols, show="headings", height=8
        )
        headings = {
            "slug": "Page", "h1": "H1", "h2": "H2", "h3": "H3", "p": "¶",
            "img": "Img", "btn": "Btn", "forms": "Forms", "lists": "Lists",
            "sections": "Sect", "links": "Links", "shot": "Shot",
        }
        widths = {
            "slug": 120, "h1": 36, "h2": 36, "h3": 36, "p": 40,
            "img": 40, "btn": 40, "forms": 48, "lists": 44,
            "sections": 44, "links": 48, "shot": 40,
        }
        for c in cols:
            self.acquire_tree.heading(c, text=headings[c])
            self.acquire_tree.column(c, width=widths[c], anchor=tk.CENTER if c != "slug" else tk.W)
        sy = ttk.Scrollbar(tree_frm, orient=tk.VERTICAL, command=self.acquire_tree.yview)
        self.acquire_tree.configure(yscrollcommand=sy.set)
        self.acquire_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(frm, text="Gaps / notes").pack(anchor=tk.W, pady=(6, 0))
        self.acquire_gaps = scrolledtext.ScrolledText(
            frm, height=5, wrap=tk.WORD, state=tk.DISABLED
        )
        self.acquire_gaps.pack(fill=tk.X, pady=(2, 0))

    def _on_acquire(self) -> None:
        if self._running:
            return
        url = _normalise_url(self.acquire_url_var.get())
        if not url:
            messagebox.showerror("Missing URL", "Please enter a website URL.")
            return
        if not _looks_like_url(url):
            messagebox.showerror("Invalid URL", f"Does not look like a URL:\n{url}")
            return
        name = self.acquire_name_var.get().strip()
        force = bool(self.acquire_force_var.get())
        self._persist_acquire_form()
        self._set_busy(True)
        self.progress["value"] = 0
        self._set_status("Acquiring website…")
        self._append_log("─" * 40)
        self._append_log(f"Acquire: {url}  →  {name or '(auto)'}")
        if force:
            self._append_log("Force re-crawl enabled")
        threading.Thread(
            target=self._worker_acquire,
            args=(url, name, force),
            daemon=True,
        ).start()

    def _persist_acquire_form(self) -> None:
        try:
            prefs = _load_ui_prefs()
            prefs["target_url"] = self.acquire_url_var.get().strip()
            prefs["project_name"] = self.acquire_name_var.get().strip()
            _save_ui_prefs(prefs)
        except Exception:  # noqa: BLE001
            pass

    def _worker_acquire(self, url: str, name: str, force: bool) -> None:
        try:
            from webmaker.schemas.acquisition import WebsitePackageResult

            manager = ProjectManager(settings)
            self._manager = manager
            manager.progress.subscribe(self._on_progress)

            try:
                project = manager.create_project(url, name=name, force=False)
                self._ui(lambda: self._append_log(
                    f"Created project: {project.name} ({project.id})"
                ))
            except ProjectAlreadyExistsError:
                project = self._load_existing(manager, url, name, [])
                self._ui(lambda: self._append_log("Project exists — reusing"))

            state = manager.active_state
            if state is not None:
                state.data_dir = state.project_dir
                manager.save_project()

            slug = ""
            if state is not None:
                slug = str((state.metadata or {}).get("slug") or state.name or "")
            if not slug:
                slug = project.name if project else name

            orch = Orchestrator(settings, str(slug), manager=manager)
            result: WebsitePackageResult = orch.run_acquisition(
                force_crawl=force,
                threshold=0.95,
            )
            project_dir = state.project_dir if state else ""

            def _done():
                self.migrate_url_var.set(self.acquire_url_var.get())
                self.migrate_name_var.set(self.acquire_name_var.get())
                self.url_var.set(self.acquire_url_var.get())
                self.name_var.set(self.acquire_name_var.get())
                self._fill_acquire_results(result)
                pct = f"{result.overall_score:.1%}"
                status = "PASSED" if result.passed else "BELOW THRESHOLD"
                self._finish(
                    result.success,
                    f"Acquisition {status} — overall {pct} "
                    f"({result.pages_crawled} pages)",
                    project_dir,
                )

            self._ui(_done)
        except Exception as exc:  # noqa: BLE001
            log.exception("Acquisition failed")
            msg = str(exc)
            self._ui(lambda m=msg: self._finish(False, m, ""))
        finally:
            self._unsubscribe()

    def _fill_acquire_results(self, result) -> None:
        pct = f"{result.overall_score:.1%}"
        status = "PASSED" if result.passed else "BELOW THRESHOLD"
        scores = result.scores
        self.acquire_score_var.set(
            f"{status}  Overall {pct}  (threshold {result.threshold:.0%})  ·  "
            f"Pages {scores.pages:.0%}  Text {scores.text:.0%}  "
            f"Images {scores.images:.0%}  Buttons {scores.buttons:.0%}  "
            f"Sections {scores.sections:.0%}  Nav {scores.navigation:.0%}  "
            f"Brand {scores.brand_assets:.0%}"
        )
        for row in self.acquire_tree.get_children():
            self.acquire_tree.delete(row)
        for st in result.per_page_stats:
            self.acquire_tree.insert(
                "",
                tk.END,
                values=(
                    st.slug,
                    st.h1,
                    st.h2,
                    st.h3,
                    st.paragraphs,
                    st.images,
                    st.buttons,
                    st.forms,
                    st.lists,
                    st.sections,
                    st.links,
                    "yes" if st.has_screenshot else "no",
                ),
            )
        self.acquire_gaps.configure(state=tk.NORMAL)
        self.acquire_gaps.delete("1.0", tk.END)
        lines = list(result.gaps[:40])
        notes = (result.extras or {}).get("notes") or []
        if notes:
            lines.append("")
            lines.extend(str(n) for n in notes)
        if result.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(result.errors)
        self.acquire_gaps.insert("1.0", "\n".join(lines) if lines else "(none)")
        self.acquire_gaps.configure(state=tk.DISABLED)

    # ── Tab 2: Migrate ─────────────────────────────────────────────────────────

    def _build_migrate_tab(self) -> None:
        pad = {"padx": 0, "pady": 4}
        frm = self.tab_migrate

        ttk.Label(
            frm,
            text=(
                "Step 2 — Premium redesign.  Pick a theme and starter template.  "
                "Agent 1 (Creative Director) studies the Design Library, writes a "
                "Design Blueprint, then populates the template with your business content.  "
                "Tick 'Faithful as-is' to skip AI and clone the site verbatim instead.\n\n"
                "Already happy with a previous build?  Do not click Modernize again — "
                "use Open demo or Continue to OP-Content below."
            ),
            wraplength=740,
        ).pack(anchor=tk.W, pady=(0, 8))

        prefs  = _load_ui_prefs()
        url0   = str(prefs.get("target_url")    or _DEFAULT_TARGET_URL).strip()
        name0  = str(prefs.get("project_name")  or _DEFAULT_PROJECT_NAME).strip()

        # URL + project name
        ttk.Label(frm, text="Website URL").pack(anchor=tk.W)
        self.migrate_url_var = tk.StringVar(value=url0)
        ttk.Entry(frm, textvariable=self.migrate_url_var).pack(fill=tk.X, **pad)

        ttk.Label(frm, text="Project name / folder").pack(anchor=tk.W, pady=(6, 0))
        self.migrate_name_var = tk.StringVar(value=name0)
        ttk.Entry(frm, textvariable=self.migrate_name_var).pack(fill=tk.X, **pad)
        self.migrate_name_var.trace_add("write", lambda *_: self._refresh_migrate_status())

        # Existing-demo banner (skip remigration)
        self._migrate_status_frame = ttk.LabelFrame(
            frm, text="Existing demo", padding=8
        )
        self._migrate_status_frame.pack(fill=tk.X, pady=(8, 4))
        self._migrate_status_var = tk.StringVar(
            value="Enter a project name to check for an existing build."
        )
        ttk.Label(
            self._migrate_status_frame,
            textvariable=self._migrate_status_var,
            wraplength=700,
            foreground="#333333",
        ).pack(anchor=tk.W)
        status_btns = ttk.Frame(self._migrate_status_frame)
        status_btns.pack(fill=tk.X, pady=(6, 0))
        self.open_demo_btn = ttk.Button(
            status_btns,
            text="Open demo",
            command=self._on_open_existing_demo,
            state=tk.DISABLED,
        )
        self.open_demo_btn.pack(side=tk.LEFT)
        self.skip_to_op_btn = ttk.Button(
            status_btns,
            text="Continue → OP-Content (review only)",
            command=self._on_skip_to_op_content,
            state=tk.DISABLED,
        )
        self.skip_to_op_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(
            status_btns,
            text="  No rebuild · keeps your current WordPress site",
            foreground="#888888",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        # ── Theme + template selector ──────────────────────────────────────────
        panes = ttk.Frame(frm)
        panes.pack(fill=tk.BOTH, expand=True, pady=(8, 4))
        panes.columnconfigure(0, weight=2)
        panes.columnconfigure(1, weight=3)
        panes.rowconfigure(0, weight=1)

        # Left: theme list
        left = ttk.LabelFrame(panes, text="Theme", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self._theme_listbox = tk.Listbox(
            left,
            selectmode=tk.SINGLE,
            activestyle="dotbox",
            exportselection=False,
            height=6,
            font=("Segoe UI", 10),
            selectbackground="#0078d7",
            selectforeground="white",
        )
        self._theme_listbox.grid(row=0, column=0, sticky="nsew")
        self._theme_ids: list[str] = []
        for t in THEMES:
            self._theme_listbox.insert(tk.END, f"  {t['name']}  {t['seo']}")
            self._theme_ids.append(t["id"])
        self._theme_listbox.bind("<<ListboxSelect>>", self._on_theme_select)

        self._theme_desc_var = tk.StringVar()
        ttk.Label(
            left,
            textvariable=self._theme_desc_var,
            wraplength=230,
            foreground="#555555",
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Right: template list
        right = ttk.LabelFrame(panes, text="Starter Template", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._tmpl_listbox = tk.Listbox(
            right,
            selectmode=tk.SINGLE,
            activestyle="dotbox",
            exportselection=False,
            height=6,
            font=("Segoe UI", 10),
            selectbackground="#0078d7",
            selectforeground="white",
        )
        self._tmpl_listbox.grid(row=0, column=0, sticky="nsew")
        self._tmpl_ids: list[str] = []
        self._tmpl_listbox.bind("<<ListboxSelect>>", self._on_template_select)

        self._tmpl_tags_var = tk.StringVar()
        ttk.Label(
            right,
            textvariable=self._tmpl_tags_var,
            foreground="#555555",
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Preview row
        prev_row = ttk.Frame(frm)
        prev_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(prev_row, text="Template preview:").pack(side=tk.LEFT)
        self._preview_url_var = tk.StringVar(value="— select a template above —")
        ttk.Label(
            prev_row,
            textvariable=self._preview_url_var,
            foreground="#0066cc",
            font=("Segoe UI", 9, "underline"),
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            prev_row, text="Open in Browser", command=self._on_open_preview
        ).pack(side=tk.LEFT, padx=(12, 0))

        # Modernize / Migrate button row
        migrate_row = ttk.Frame(frm)
        migrate_row.pack(fill=tk.X, pady=(10, 0))
        self.migrate_btn = ttk.Button(
            migrate_row,
            text="Modernize & Build  →  WordPress",
            command=self._on_migrate,
        )
        self.migrate_btn.pack(side=tk.LEFT)

        self._faithful_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            migrate_row,
            text="Faithful as-is (no AI)",
            variable=self._faithful_var,
        ).pack(side=tk.LEFT, padx=(14, 0))

        ttk.Label(
            migrate_row,
            text="  Requires internet · Installs theme",
            foreground="#888888",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        ttk.Label(
            frm,
            text=(
                "After a successful build you can leave this tab alone.  "
                "Use Analyze for competitor insights (optional), then OP-Content "
                "for a new Claude review — that does not remigrate."
            ),
            wraplength=740,
            foreground="#888888",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(6, 0))

        # Pre-select first theme after window is fully rendered.
        if self._theme_ids:
            self.root.after(
                100,
                lambda: (
                    self._theme_listbox.selection_set(0),
                    self._populate_templates(self._theme_ids[0]),
                ),
            )
        self.root.after(150, self._refresh_migrate_status)

    def _on_theme_select(self, _event=None) -> None:
        sel = self._theme_listbox.curselection()
        if not sel:
            return
        theme_id = self._theme_ids[sel[0]]
        for t in THEMES:
            if t["id"] == theme_id:
                self._theme_desc_var.set(t["description"])
                break
        self._populate_templates(theme_id)

    def _populate_templates(self, theme_id: str) -> None:
        self._tmpl_listbox.delete(0, tk.END)
        self._tmpl_ids = []
        self._tmpl_tags_var.set("")
        self._preview_url_var.set("— select a template above —")
        for t in THEMES:
            if t["id"] == theme_id:
                self._theme_desc_var.set(t.get("description", ""))
                for tmpl in t["templates"]:
                    self._tmpl_listbox.insert(tk.END, tmpl["name"])
                    self._tmpl_ids.append(tmpl["id"])
                break
        if self._tmpl_ids:
            self._tmpl_listbox.selection_set(0)
            self._sync_template_info(theme_id, self._tmpl_ids[0])

    def _on_template_select(self, _event=None) -> None:
        sel_t = self._theme_listbox.curselection()
        sel_m = self._tmpl_listbox.curselection()
        if not sel_t or not sel_m:
            return
        theme_id = self._theme_ids[sel_t[0]]
        tmpl_id  = self._tmpl_ids[sel_m[0]]
        self._sync_template_info(theme_id, tmpl_id)

    def _sync_template_info(self, theme_id: str, tmpl_id: str) -> None:
        tmpl = get_template(theme_id, tmpl_id)
        if tmpl:
            self._preview_url_var.set(tmpl["preview_url"])
            tags = ", ".join(tmpl.get("tags", []))
            self._tmpl_tags_var.set(f"Best for: {tags}" if tags else "")

    def _on_open_preview(self) -> None:
        url = self._preview_url_var.get()
        if url and url.startswith("http"):
            webbrowser.open(url)

    def _slugify_project_name(self, name: str) -> str:
        raw = (name or "").strip().lower()
        slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
        slug = "-".join(p for p in slug.split("-") if p)
        return slug or "project"

    def _inspect_project_demo(self, name_or_slug: str) -> dict:
        """Detect an existing migrate/modernize build for this project folder."""
        info: dict = {
            "built": False,
            "slug": "",
            "built_via": "",
            "has_pages": False,
            "has_op": False,
            "project_dir": None,
        }
        name = (name_or_slug or "").strip()
        if not name:
            return info
        slug = self._slugify_project_name(name)
        # Prefer exact folder match under projects/
        projects_root = Path(settings.projects_dir)
        candidates = []
        exact = projects_root / slug
        if exact.is_dir():
            candidates.append(exact)
        # Also match by display name folder (e.g. My Business → my-business)
        for child in projects_root.iterdir() if projects_root.is_dir() else []:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name.lower() == slug or child.name.lower() == name.lower():
                if child not in candidates:
                    candidates.append(child)
        if not candidates:
            info["slug"] = slug
            return info

        project_dir = candidates[0]
        info["slug"] = project_dir.name
        info["project_dir"] = project_dir
        arts = project_dir / "artifacts"
        json_dir = project_dir / "json"
        via = []
        if (arts / "modernizer.json").is_file():
            via.append("Modernize")
        if (arts / "migration.json").is_file():
            via.append("Faithful migrate")
        if (json_dir / "generation_report.json").is_file():
            via.append("WordPress generate")
        pages = list(json_dir.glob("optimized_*.json")) if json_dir.is_dir() else []
        info["has_pages"] = bool(pages)
        info["has_op"] = (arts / "op_content.json").is_file()
        info["built"] = bool(via) or info["has_pages"]
        info["built_via"] = " + ".join(via) if via else (
            f"{len(pages)} page file(s)" if pages else ""
        )
        return info

    def _refresh_migrate_status(self) -> None:
        if not hasattr(self, "_migrate_status_var"):
            return
        name = self.migrate_name_var.get().strip() if hasattr(self, "migrate_name_var") else ""
        status = self._inspect_project_demo(name)
        if not name:
            self._migrate_status_var.set(
                "Enter a project name to check for an existing build."
            )
            self.open_demo_btn.configure(state=tk.DISABLED)
            self.skip_to_op_btn.configure(state=tk.DISABLED)
            return
        if status["built"]:
            op_note = (
                " OP-Content review already exists — you can Load it or run a new review."
                if status["has_op"]
                else " No OP-Content yet — Continue to run a Claude review only."
            )
            self._migrate_status_var.set(
                f"Demo ready for «{status['slug']}» "
                f"({status['built_via']}).{op_note}\n"
                "Skip Modernize unless you want to rebuild from scratch."
            )
            self.open_demo_btn.configure(state=tk.NORMAL)
            self.skip_to_op_btn.configure(state=tk.NORMAL)
        else:
            self._migrate_status_var.set(
                f"No existing demo found for «{status['slug'] or name}». "
                "Run Modernize & Build (or Faithful as-is) first."
            )
            self.open_demo_btn.configure(state=tk.DISABLED)
            # Still allow jumping to OP-Content if project folder exists
            can_op = status.get("project_dir") is not None
            self.skip_to_op_btn.configure(
                state=tk.NORMAL if can_op else tk.DISABLED
            )

    def _on_open_existing_demo(self) -> None:
        url = str(settings.wordpress_url or "").rstrip("/")
        if not url:
            messagebox.showerror(
                "No WordPress URL",
                "wordpress_url is not configured in settings.",
            )
            return
        webbrowser.open(url if url.endswith("/") else url + "/")
        self._append_log(f"Opened existing demo: {url}")
        self._set_status("Opened existing WordPress demo (no rebuild).")

    def _on_skip_to_op_content(self) -> None:
        """Jump to OP-Content without remigrating — review-only path."""
        name = self.migrate_name_var.get().strip()
        status = self._inspect_project_demo(name)
        slug = status.get("slug") or self._slugify_project_name(name)
        self._persist_migrate_form()
        self._refresh_projects()
        # Select matching project in OP combo
        selected = None
        for label, s in getattr(self, "_project_map", {}).items():
            if str(s).lower() == slug.lower() or slug.lower() in label.lower():
                selected = label
                break
        if selected:
            self.op_project_var.set(selected)
        elif hasattr(self, "op_project_combo"):
            # Force-add if list empty but folder exists
            label = f"{name or slug} ({slug})"
            values = list(self.op_project_combo["values"] or ())
            if label not in values:
                values = [label, *values]
                self.op_project_combo["values"] = values
                self._project_map[label] = slug
            self.op_project_var.set(label)
        self.notebook.select(self.tab_op)
        self._set_status(
            f"OP-Content ready for «{slug}» — run a new Claude review "
            "(does not remigrate)."
        )
        self._append_log(
            f"Skipped migrate — continuing with content review for {slug}"
        )
        # Auto-load prior OP-Content if present
        if status.get("has_op"):
            self.root.after(50, self._on_op_load)

    def _on_migrate(self) -> None:
        if self._running:
            return

        url = _normalise_url(self.migrate_url_var.get())
        if not url:
            messagebox.showerror("Missing URL", "Please enter a website URL.")
            return
        if not _looks_like_url(url):
            messagebox.showerror("Invalid URL", f"Does not look like a URL:\n{url}")
            return

        # Resolve theme + template selection.
        sel_t = self._theme_listbox.curselection()
        sel_m = self._tmpl_listbox.curselection()
        if not sel_t and self._theme_ids:
            self._theme_listbox.selection_set(0)
            sel_t = (0,)
        if not sel_t:
            messagebox.showerror("No theme", "Select a theme from the list.")
            return
        if not sel_m and self._tmpl_ids:
            self._tmpl_listbox.selection_set(0)
            sel_m = (0,)

        theme_id   = self._theme_ids[sel_t[0]]
        tmpl_id    = self._tmpl_ids[sel_m[0]] if sel_m else ""
        name       = self.migrate_name_var.get().strip()
        tmpl_entry = get_template(theme_id, tmpl_id)
        tmpl_label = tmpl_entry["name"] if tmpl_entry else (tmpl_id or "none")
        faithful   = bool(getattr(self, "_faithful_var", None) and self._faithful_var.get())
        mode_label = "Faithful as-is (no AI)" if faithful else "Modernize (AI-powered)"

        status = self._inspect_project_demo(name)
        if status.get("built"):
            rebuild = messagebox.askyesno(
                "Demo already exists",
                f"Project «{status.get('slug') or name}» already has a WordPress demo "
                f"({status.get('built_via', 'previous build')}).\n\n"
                "If you liked it, click No and use:\n"
                "  • Open demo\n"
                "  • Continue → OP-Content (review only)\n\n"
                "Click Yes only if you want to REPLACE the current demo.",
            )
            if not rebuild:
                return

        confirmed = messagebox.askyesno(
            "Build WordPress Demo",
            f"Mode: {mode_label}\n\n"
            f"This will:\n"
            f"  1. Use crawl data for {url} (re-crawl if missing)\n"
            f"  2. Install theme '{theme_id}'"
            + (f" + template '{tmpl_label}'" if tmpl_id else "") + "\n"
            + (f"  3. Use Claude to map content into a modern professional demo\n"
               if not faithful else
               f"  3. Clone the site's content into WordPress verbatim\n")
            + "\nAn internet connection is required.\n"
            f"Existing demo pages will be replaced.\n\n"
            f"Continue?",
        )
        if not confirmed:
            return

        self._persist_migrate_form()
        self._set_busy(True)
        self.progress["value"] = 0
        faithful = bool(getattr(self, "_faithful_var", None) and self._faithful_var.get())
        mode_lbl = "Faithful as-is" if faithful else "Modernize (AI)"
        self._set_status(f"Starting {mode_lbl}…")
        self._append_log("─" * 40)
        self._append_log(f"Mode: {mode_lbl}  |  {url}  →  {name or '(auto)'}")
        self._append_log(f"Theme: {theme_id}  |  Template: {tmpl_label}")
        if not faithful:
            self._append_log(
                "Creative Director mode: Claude vision studies Design Library "
                "screenshots → Design Blueprint → populate WordPress template…"
            )

        threading.Thread(
            target=self._worker_migrate,
            args=(url, name, theme_id, tmpl_id, faithful),
            daemon=True,
        ).start()

    def _worker_migrate(
        self, url: str, name: str, theme_id: str, tmpl_id: str,
        faithful: bool = False,
    ) -> None:
        try:
            manager = ProjectManager(settings)
            self._manager = manager
            manager.progress.subscribe(self._on_progress)

            # Create or load the project.
            try:
                project = manager.create_project(url, name=name, force=False)
                self._ui(lambda: self._append_log(
                    f"Created project: {project.name} ({project.id})"
                ))
            except ProjectAlreadyExistsError:
                project = self._load_existing(manager, url, name, [])
                self._ui(lambda: self._append_log("Project exists — reusing"))

            # Ensure data_dir is the project folder.
            state = manager.active_state
            if state is not None:
                state.data_dir = state.project_dir
                manager.save_project()

            # Resolve slug for the orchestrator.
            slug = (state.metadata or {}).get("slug") if state else ""
            if not slug and state:
                slug = state.name
            if not slug:
                slug = project.name if project else name

            # Run Agent 1 (Modernizer or faithful migration) via the orchestrator.
            orch = Orchestrator(settings, str(slug), manager=manager)
            acq = orch.store.load(WebsitePackageResult)
            if acq is None:
                self._ui(lambda: self._append_log(
                    "WARNING: No acquisition report — run Crawl tab first for validation."
                ))
            elif not acq.passed:
                self._ui(lambda a=acq: self._append_log(
                    f"WARNING: Acquisition {a.overall_score:.1%} below "
                    f"{a.threshold:.0%} — continuing anyway."
                ))

            if faithful:
                result: MigrationResult = orch.run_migration(
                    theme_id=theme_id,
                    template_id=tmpl_id,
                    force_crawl=False,
                    open_browser=True,
                )
                pages_label = ", ".join(result.pages_migrated) or "none"
                ai_note = ""
            else:
                mod_result: ModernizeResult = orch.run_modernize(
                    theme_id=theme_id,
                    template_id=tmpl_id,
                    force_crawl=False,
                    open_browser=True,
                )
                # Expose a unified interface for the finish block below.
                result = mod_result  # type: ignore[assignment]
                pages_label = ", ".join(mod_result.pages_built) or "none"
                # Log Design Blueprint choices in the UI.
                if mod_result.vision_used:
                    self._ui(lambda n=mod_result.vision_images: self._append_log(
                        f"Vision: Claude studied {n} Design Library screenshot(s)"
                    ))
                    if mod_result.vision_summary:
                        self._ui(lambda s=mod_result.vision_summary: self._append_log(
                            f"Vision summary: {s[:200]}"
                        ))
                else:
                    self._ui(lambda: self._append_log(
                        "Vision: not used (text/heuristic blueprint only)"
                    ))
                if mod_result.blueprint_sections:
                    self._ui(lambda: self._append_log("Design Blueprint:"))
                    for line in mod_result.blueprint_sections[:12]:
                        self._ui(lambda L=line: self._append_log(f"  {L}"))
                if mod_result.design_notes:
                    self._ui(lambda n=mod_result.design_notes: self._append_log(
                        f"Design notes: {n[:160]}"
                    ))
                ai_note = (
                    f"\nAI mapping: {mod_result.mapping_summary[:80]}"
                    if mod_result.ai_used and mod_result.mapping_summary
                    else " (AI fallback used — no Claude key?)"
                    if not mod_result.ai_used
                    else ""
                )
                if mod_result.library_refs_used:
                    ai_note += f"\nLibrary refs: {mod_result.library_refs_used}"
                if mod_result.vision_used:
                    ai_note += f"\nVision screenshots: {mod_result.vision_images}"
            project_dir = state.project_dir if state else ""

            if result.success:
                def _done():
                    # Pre-fill Tab 2 (Analyze) with the same URL/project.
                    self.url_var.set(self.migrate_url_var.get())
                    self.name_var.set(self.migrate_name_var.get())
                    self._refresh_projects()
                    mode_str = "Faithful" if faithful else "Modernized"
                    self._finish(
                        True,
                        f"{mode_str} demo ready.\n"
                        f"Theme: {theme_id}  |  Pages: {pages_label}"
                        + (ai_note if not faithful else "")
                        + "\nOpening browser…",
                        project_dir,
                    )
                self._ui(_done)
            else:
                err = "; ".join(result.errors) or "Build failed"
                self._ui(lambda e=err, d=project_dir: self._finish(False, e, d))

        except Exception as exc:  # noqa: BLE001
            log.exception("Build failed")
            msg = str(exc)
            self._ui(lambda m=msg: self._finish(False, m, ""))
        finally:
            self._unsubscribe()

    # ── Tab 3: Analyze ─────────────────────────────────────────────────────────

    def _build_crawl_tab(self) -> None:
        pad = {"padx": 0, "pady": 4}
        frm = self.tab_crawl

        ttk.Label(
            frm,
            text=(
                "Step 3 — Analyze.  Business analysis (Claude) and competitor "
                "structure (DeepSeek).  Reuses the Crawl package when present — "
                "does not force a full re-crawl.  Optional before OP-Content "
                "(review can run without re-analyzing)."
            ),
            wraplength=740,
        ).pack(anchor=tk.W, pady=(0, 8))

        prefs  = _load_ui_prefs()
        url0   = str(prefs.get("target_url")    or _DEFAULT_TARGET_URL).strip()
        name0  = str(prefs.get("project_name")  or _DEFAULT_PROJECT_NAME).strip()
        comps0 = prefs.get("competitors_text")
        if comps0 is None:
            comps0 = ""
        else:
            comps0 = str(comps0)

        ttk.Label(frm, text="Website URL").pack(anchor=tk.W)
        self.url_var = tk.StringVar(value=url0 or _DEFAULT_TARGET_URL)
        ttk.Entry(frm, textvariable=self.url_var).pack(fill=tk.X, **pad)

        ttk.Label(frm, text="Project name / folder").pack(anchor=tk.W, pady=(8, 0))
        self.name_var = tk.StringVar(value=name0 or _DEFAULT_PROJECT_NAME)
        ttk.Entry(frm, textvariable=self.name_var).pack(fill=tk.X, **pad)

        ttk.Label(frm, text="Competitor websites (one URL per line)").pack(
            anchor=tk.W, pady=(8, 0)
        )
        self.comp_text = scrolledtext.ScrolledText(frm, height=6, wrap=tk.WORD)
        self.comp_text.pack(fill=tk.X, **pad)
        if comps0.strip():
            self.comp_text.insert("1.0", comps0)

        ttk.Label(
            frm,
            text=(
                "If you already ran Crawl (or Migrate), the target crawl is reused. "
                "Analyze mainly runs business profile + competitor comparison.  "
                "Competitor list is remembered from your last session."
            ),
            wraplength=740,
            foreground="#555555",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(4, 0))

        self.crawl_btn = ttk.Button(
            frm, text="Analyze", command=self._on_crawl_analyze
        )
        self.crawl_btn.pack(anchor=tk.W, pady=10)

    # ── Tab 4: OP-Content (human approval) ─────────────────────────────────────

    def _build_op_content_tab(self) -> None:
        frm = self.tab_op

        ttk.Label(
            frm,
            text=(
                "Step 4 — OP-Content (review only).  Does NOT remigrate or rebuild.  "
                "Pick the existing project, run a Claude review (or Load a prior one), "
                "tick tips, Save, then Render approved — changes layer onto the "
                "current WordPress demo."
            ),
            wraplength=740,
        ).pack(anchor=tk.W, pady=(0, 8))

        # Project selector (shares the global project list).
        row = ttk.Frame(frm)
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Project:").pack(side=tk.LEFT)
        self.op_project_var = tk.StringVar()
        self.op_project_combo = ttk.Combobox(
            row, textvariable=self.op_project_var, state="readonly", width=36
        )
        self.op_project_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        ttk.Button(
            row, text="Refresh", command=self._refresh_projects_all
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            row, text="Open demo", command=self._on_open_existing_demo
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Page scope for review.
        page_row = ttk.Frame(frm)
        page_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(page_row, text="Review page:").pack(side=tk.LEFT)
        self.op_page_var = tk.StringVar(value="All pages")
        self.op_page_combo = ttk.Combobox(
            page_row,
            textvariable=self.op_page_var,
            state="readonly",
            width=22,
            values=(
                "All pages",
                "homepage",
                "about",
                "services",
                "contact",
                "faq",
            ),
        )
        self.op_page_combo.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(
            page_row,
            text="  Single page = faster · tips in easy German",
            foreground="#555555",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        # Action buttons.
        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(0, 8))
        self.op_load_btn = ttk.Button(
            btns, text="Load OP-Content", command=self._on_op_load
        )
        self.op_load_btn.pack(side=tk.LEFT)
        self.op_review_btn = ttk.Button(
            btns, text="Run Review (Claude)", command=self._on_op_run_review
        )
        self.op_review_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.op_save_btn = ttk.Button(
            btns, text="Save selections", command=self._on_op_save
        )
        self.op_save_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.op_render_btn = ttk.Button(
            btns, text="Render approved", command=self._on_op_render
        )
        self.op_render_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.op_undo_btn = ttk.Button(
            btns, text="Undo last render", command=self._on_op_undo
        )
        self.op_undo_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.op_summary_var = tk.StringVar(value="No OP-Content loaded.")
        ttk.Label(
            frm, textvariable=self.op_summary_var, wraplength=740, foreground="#555"
        ).pack(anchor=tk.W, pady=(0, 6))
        ttk.Label(
            frm,
            text=(
                "Tips change only small parts of the page (headline, button text, …). "
                "Undo restores the previous page snapshot from before the last Render."
            ),
            wraplength=740,
            foreground="#888888",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(0, 6))

        # Scrollable list of recommendation cards.
        container = ttk.Frame(frm)
        container.pack(fill=tk.BOTH, expand=True)
        self._op_canvas = tk.Canvas(container, highlightthickness=0, height=260)
        scrollbar = ttk.Scrollbar(
            container, orient=tk.VERTICAL, command=self._op_canvas.yview
        )
        self._op_inner = ttk.Frame(self._op_canvas)
        self._op_inner.bind(
            "<Configure>",
            lambda e: self._op_canvas.configure(
                scrollregion=self._op_canvas.bbox("all")
            ),
        )
        self._op_canvas.create_window((0, 0), window=self._op_inner, anchor="nw")
        self._op_canvas.configure(yscrollcommand=scrollbar.set)
        self._op_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # State: loaded artifact + rec.id -> (Recommendation, BooleanVar).
        self._op_content: OpContent | None = None
        self._op_vars: dict[str, tuple[object, tk.BooleanVar]] = {}

    def _op_selected_slug(self) -> str | None:
        label = self.op_project_var.get().strip()
        if not label:
            messagebox.showerror("No project", "Select a project first.")
            return None
        return getattr(self, "_project_map", {}).get(label, label)

    def _render_op_cards(self, op: OpContent) -> None:
        for child in list(self._op_inner.winfo_children()):
            child.destroy()
        self._op_vars = {}

        recs = op.all_recommendations()
        if not recs:
            ttk.Label(
                self._op_inner,
                text="No recommendations in this artifact. Run the review first.",
                foreground="#888",
            ).pack(anchor=tk.W, padx=4, pady=4)
            return

        current_page = None
        for rec in recs:
            if rec.page_slug != current_page:
                current_page = rec.page_slug
                ttk.Label(
                    self._op_inner,
                    text=f"── {current_page or 'general'} ──",
                    font=("Segoe UI", 10, "bold"),
                ).pack(anchor=tk.W, padx=2, pady=(8, 2))

            card = ttk.LabelFrame(
                self._op_inner,
                text=f"[{rec.priority}] {rec.section} · {rec.source}",
                padding=6,
            )
            card.pack(fill=tk.X, padx=2, pady=3)

            var = tk.BooleanVar(value=bool(rec.selected))
            ttk.Checkbutton(
                card, text="Diesen Tipp übernehmen", variable=var
            ).pack(anchor=tk.W)

            for field_label, value in (
                ("Jetzt",                  rec.current),
                ("Problem",                rec.issue),
                ("Tipp",                   rec.recommendation),
                ("Neuer Text auf der Website",
                 getattr(rec, "proposed_html", "") or ""),
                ("Warum",                  rec.reason),
            ):
                if value:
                    ttk.Label(
                        card,
                        text=f"{field_label}: {value}",
                        wraplength=680,
                        justify=tk.LEFT,
                    ).pack(anchor=tk.W)

            self._op_vars[rec.id] = (rec, var)

    def _on_op_load(self) -> None:
        if self._running:
            return
        slug = self._op_selected_slug()
        if not slug:
            return
        try:
            orch = Orchestrator(settings, slug)
            op = orch.store.load(OpContent)
        except WebMakerError as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        if op is None:
            messagebox.showinfo(
                "No OP-Content",
                "No OP-Content artifact yet. Click 'Run Review (Claude)' to create one.",
            )
            self._op_content = None
            self._render_op_cards(OpContent())
            self.op_summary_var.set("No OP-Content loaded.")
            return
        self._op_content = op
        self._render_op_cards(op)
        n = len(op.all_recommendations())
        self.op_summary_var.set(f"{n} recommendation(s). {op.summary}")

    def _on_op_run_review(self) -> None:
        if self._running:
            return
        slug = self._op_selected_slug()
        if not slug:
            return
        page_choice = (self.op_page_var.get() or "All pages").strip()
        page_slug = (
            "all"
            if page_choice.lower() in ("all pages", "all", "alle", "")
            else page_choice.lower()
        )
        self._set_busy(True)
        self.progress["value"] = 0
        scope = "all pages" if page_slug == "all" else page_slug
        self._set_status(f"Running website review ({scope}, easy German)…")
        self._append_log("─" * 40)
        self._append_log(f"OP-Content: website_reviewer for {scope}…")
        threading.Thread(
            target=self._worker_op_review,
            args=(slug, page_slug),
            daemon=True,
        ).start()

    def _worker_op_review(self, slug: str, page_slug: str = "all") -> None:
        try:
            orch = Orchestrator(settings, slug)
            orch.ensure_crawl_artifacts()
            extras: dict[str, object] = {"page_slug": page_slug}
            op = orch.run_agent("website_reviewer", extras=extras)
            self._ui(lambda o=op: self._op_review_done(o))
        except Exception as exc:  # noqa: BLE001
            log.exception("OP-Content review failed")
            msg = str(exc)
            self._ui(lambda m=msg: self._finish(False, m, ""))

    def _op_review_done(self, op: OpContent) -> None:
        self._op_content = op
        self._render_op_cards(op)
        n = len(op.all_recommendations())
        self.op_summary_var.set(f"{n} recommendation(s). {op.summary}")
        self._finish(True, f"Review complete — {n} recommendation(s) generated.", "")

    def _on_op_save(self) -> None:
        if self._running:
            return
        if self._op_content is None:
            messagebox.showerror("Nothing to save", "Load or run OP-Content first.")
            return
        slug = self._op_selected_slug()
        if not slug:
            return
        for rec, var in self._op_vars.values():
            rec.selected = bool(var.get())
        try:
            orch = Orchestrator(settings, slug)
            orch.store.save(self._op_content)
        except WebMakerError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        approved = len(self._op_content.selected_recommendations())
        self._append_log(f"OP-Content: saved {approved} approved recommendation(s).")
        messagebox.showinfo(
            "Saved", f"Saved selections. {approved} recommendation(s) approved."
        )

    def _on_op_render(self) -> None:
        if self._running:
            return
        if self._op_content is None:
            messagebox.showerror("Nothing to render", "Load or run OP-Content first.")
            return
        slug = self._op_selected_slug()
        if not slug:
            return
        for rec, var in self._op_vars.values():
            rec.selected = bool(var.get())
        approved = len(self._op_content.selected_recommendations())
        if approved == 0:
            if not messagebox.askyesno(
                "No approvals",
                "No recommendations are ticked. Render the demo anyway?",
            ):
                return
        self._set_busy(True)
        self.progress["value"] = 0
        self._set_status("Rendering approved content to the demo…")
        self._append_log("─" * 40)
        self._append_log(f"OP-Content: rendering {approved} approved rec(s)…")
        threading.Thread(
            target=self._worker_op_render, args=(slug,), daemon=True
        ).start()

    def _worker_op_render(self, slug: str) -> None:
        try:
            orch = Orchestrator(settings, slug)
            orch.store.save(self._op_content)
            result = orch.run_agent("live_demo_renderer", extras={"open_browser": True})
            state = orch.manager.active_state
            project_dir = state.project_dir if state else ""
            if result.success:
                self._ui(lambda: self._finish(
                    True,
                    "Approved tips applied to the demo (page layout kept). "
                    "Use Undo last render if needed.",
                    project_dir,
                ))
            else:
                err = "; ".join(result.errors) or "Render failed"
                self._ui(lambda e=err, d=project_dir: self._finish(False, e, d))
        except Exception as exc:  # noqa: BLE001
            log.exception("OP-Content render failed")
            msg = str(exc)
            self._ui(lambda m=msg: self._finish(False, m, ""))

    def _on_op_undo(self) -> None:
        if self._running:
            return
        slug = self._op_selected_slug()
        if not slug:
            return
        from webmaker.agents.live_demo_renderer.materialize_content import (
            has_render_backup,
        )
        project_dir = Path(settings.projects_dir) / slug
        # also try case-insensitive
        if not project_dir.is_dir():
            for child in Path(settings.projects_dir).iterdir():
                if child.is_dir() and child.name.lower() == slug.lower():
                    project_dir = child
                    slug = child.name
                    break
        if not has_render_backup(project_dir):
            messagebox.showinfo(
                "Nothing to undo",
                "No last-render backup found for this project.\n"
                "(Backups are created automatically before each Render.)",
            )
            return
        if not messagebox.askyesno(
            "Undo last render",
            "Restore the page content from before the last Render "
            "and push it back to WordPress?",
        ):
            return
        self._set_busy(True)
        self.progress["value"] = 0
        self._set_status("Undoing last render…")
        self._append_log("─" * 40)
        self._append_log(f"OP-Content: undo last render for {slug}…")
        threading.Thread(
            target=self._worker_op_undo, args=(slug,), daemon=True
        ).start()

    def _worker_op_undo(self, slug: str) -> None:
        try:
            from webmaker.agents.live_demo_renderer.wordpress_renderer import (
                WordPressRenderer,
            )
            project_dir = Path(settings.projects_dir) / slug
            if not project_dir.is_dir():
                for child in Path(settings.projects_dir).iterdir():
                    if child.is_dir() and child.name.lower() == slug.lower():
                        project_dir = child
                        break
            renderer = WordPressRenderer(settings, project_dir)
            result = renderer.undo_last_render()
            if result.success:
                try:
                    from webmaker.agents.live_demo_renderer.live_preview import (
                        refresh_preview,
                    )
                    refresh_preview(result.wp_url, open_browser=True)
                except Exception:  # noqa: BLE001
                    pass
                pages = ", ".join(
                    str(p.get("slug") if isinstance(p, dict) else p)
                    for p in (result.pages_rendered or [])
                ) or "pages"
                self._ui(lambda p=pages, d=str(project_dir): self._finish(
                    True,
                    f"Undo complete — restored {p}.",
                    d,
                ))
            else:
                err = "; ".join(result.errors) or "Undo failed"
                self._ui(lambda e=err: self._finish(False, e, ""))
        except Exception as exc:  # noqa: BLE001
            log.exception("OP-Content undo failed")
            msg = str(exc)
            self._ui(lambda m=msg: self._finish(False, m, ""))

    # ── Tab 2: Crawl & Analyze workers ────────────────────────────────────────

    def _on_crawl_analyze(self) -> None:
        if self._running:
            return

        url = _normalise_url(self.url_var.get())
        if not url:
            messagebox.showerror("Missing URL", "Please enter a website URL.")
            return
        if not _looks_like_url(url):
            messagebox.showerror("Invalid URL", f"Does not look like a URL:\n{url}")
            return

        competitors = [
            _normalise_url(c)
            for c in _parse_competitor_urls(self.comp_text.get("1.0", tk.END))
        ]
        for c in competitors:
            if not _looks_like_url(c):
                messagebox.showerror("Invalid competitor URL", f"Bad URL:\n{c}")
                return

        name = self.name_var.get().strip()
        self._persist_crawl_form()
        self._set_busy(True)
        self.progress["value"] = 0
        self._set_status("Starting…")
        self._append_log("─" * 40)
        self._append_log(f"Target: {url}")
        self._append_log(f"Project folder: {name or '(from URL)'}")
        self._append_log(f"Competitors: {len(competitors)}")

        threading.Thread(
            target=self._worker_crawl,
            args=(url, name, competitors),
            daemon=True,
        ).start()

    def _worker_crawl(self, url: str, name: str, competitors: list[str]) -> None:
        try:
            manager = ProjectManager(settings)
            self._manager = manager
            manager.progress.subscribe(self._on_progress)

            try:
                project = manager.create_project(
                    url, name=name, competitor_urls=competitors, force=False
                )
                self._ui(lambda: self._append_log(
                    f"Created project: {project.name} ({project.id})"
                ))
            except ProjectAlreadyExistsError:
                project = self._load_existing(manager, url, name, competitors)
                self._ui(lambda: self._append_log("Project exists — reusing"))

            state = manager.active_state
            if state is not None:
                # Skip target re-crawl if Agent 0 (Migrate) already crawled the site.
                already_migrated = self._migration_done(manager, state)
                state.metadata["force_crawl"]   = not already_migrated
                state.metadata["force_ai"]       = False
                state.metadata["force_compete"]  = True
                for phase in ("crawl", "analyze", "compete"):
                    if phase in state.completed_phases:
                        state.completed_phases = [
                            p for p in state.completed_phases if p != phase
                        ]
                state.data_dir = state.project_dir
                manager.save_project()

                if already_migrated:
                    self._ui(lambda: self._append_log(
                        "Prior crawl/migration found — skipping target re-crawl, "
                        "running analysis + competitor comparison only."
                    ))
                else:
                    self._ui(lambda: self._append_log(
                        "Fresh crawl → analyze (Claude) → compete (DeepSeek)"
                    ))

            # Prefer skip crawl when website_package or pages.json exists
            skip_phases = list(_SKIP_LATER)
            state = manager.active_state
            data_dir = Path(state.project_dir) if state else None
            if data_dir and (
                (data_dir / "website_package" / "validation_report.json").is_file()
                or (data_dir / "json" / "pages.json").is_file()
            ):
                # Still let pipeline run analyze/compete; force_crawl already False when migrated
                pass

            result = manager.run_pipeline(
                competitor_urls=competitors or None,
                skip_phases=skip_phases,
                force_phases=["crawl", "analyze", "compete"],
                stop_on_error=True,
            )
            state = manager.active_state
            project_dir = state.project_dir if state else ""
            status_val = (
                result.status.value if hasattr(result.status, "value") else str(result.status)
            )
            if status_val == ProjectStatus.FAILED.value:
                err = (state.last_error if state else "") or "Pipeline failed"
                self._ui(lambda e=err, d=project_dir: self._finish(False, e, d))
            else:
                slug = (state.metadata or {}).get("slug") if state else ""
                if not slug and state:
                    slug = state.name
                if slug:
                    try:
                        orch = Orchestrator(settings, str(slug), manager=manager)
                        orch.ensure_crawl_artifacts()
                        self._ui(lambda: self._append_log(
                            "V2 artifacts ready (target + competitors)."
                        ))
                    except Exception as art_exc:  # noqa: BLE001
                        log.warning("Could not hydrate V2 artifacts: {e}", e=art_exc)
                        self._ui(lambda e=str(art_exc): self._append_log(
                            f"WARN: V2 artifact hydrate failed: {e}"
                        ))
                self._ui(lambda d=project_dir: self._finish(
                    True, "Crawl & Analyze completed.", d
                ))
                self._ui(self._refresh_projects)
        except Exception as exc:
            log.exception("Crawl & Analyze failed")
            msg = str(exc)
            self._ui(lambda m=msg: self._finish(False, m, ""))
        finally:
            self._unsubscribe()

    @staticmethod
    def _migration_done(manager: ProjectManager, state) -> bool:
        """True when crawl/acquisition data already exists (skip target re-crawl)."""
        try:
            from webmaker.orchestrator.store import ArtifactStore
            from webmaker.schemas import MigrationResult as MR
            project_dir = manager.get_project_dir()
            store = ArtifactStore(project_dir / "artifacts")
            if store.exists(MR) or store.exists(WebsitePackageResult):
                return True
            if (project_dir / "json" / "pages.json").is_file():
                return True
            if (project_dir / "website_package" / "validation_report.json").is_file():
                return True
            return False
        except Exception:
            return False

    # ── Shared UI helpers ──────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, message.rstrip() + "\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def _ui(self, fn) -> None:
        self.root.after(0, fn)

    def _set_busy(self, busy: bool) -> None:
        self._running = busy
        state = tk.DISABLED if busy else tk.NORMAL
        if hasattr(self, "acquire_btn"):
            self.acquire_btn.configure(state=state)
        if hasattr(self, "migrate_btn"):
            self.migrate_btn.configure(state=state)
        if hasattr(self, "crawl_btn"):
            self.crawl_btn.configure(state=state)
        for attr in (
            "op_load_btn", "op_review_btn", "op_save_btn",
            "op_render_btn", "op_undo_btn",
        ):
            if hasattr(self, attr):
                getattr(self, attr).configure(state=state)
        if hasattr(self, "op_page_combo"):
            self.op_page_combo.configure(state=tk.DISABLED if busy else "readonly")
        if busy:
            for attr in ("open_demo_btn", "skip_to_op_btn"):
                if hasattr(self, attr):
                    getattr(self, attr).configure(state=tk.DISABLED)
        else:
            self._refresh_migrate_status()

    def _on_progress(self, event: ProgressEvent) -> None:
        msg = f"{event.percent:.0f}%  [{event.phase or '-'}]  {event.message}"

        def apply() -> None:
            self.progress["value"] = event.percent
            self._set_status(event.message or f"{event.percent:.0f}%")
            self._append_log(msg)

        self._ui(apply)

    def _refresh_projects_all(self) -> None:
        self._refresh_projects()

    def _refresh_projects(self) -> None:
        try:
            pm = ProjectManager(settings)
            values: list[str] = []
            self._project_map: dict[str, str] = {}
            for p in pm.list_projects():
                slug  = (p.metadata or {}).get("slug") or p.name or p.id
                label = f"{p.name} ({slug})"
                values.append(label)
                self._project_map[label] = str(slug)
            if hasattr(self, "op_project_combo"):
                self.op_project_combo["values"] = values
                prefs = _load_ui_prefs()
                preferred = str(
                    prefs.get("project_name") or _DEFAULT_PROJECT_NAME
                ).strip().lower()
                pick = ""
                if values:
                    # Prefer last session project name / slug
                    for label, slug in self._project_map.items():
                        if preferred and (
                            preferred in label.lower()
                            or preferred == str(slug).lower()
                        ):
                            pick = label
                            break
                    if not pick:
                        pick = values[0]
                    if not self.op_project_var.get() or preferred:
                        self.op_project_var.set(pick)
            self._refresh_migrate_status()
        except Exception as exc:
            log.warning("Could not list projects: {e}", e=exc)

    # ── Persist / load helpers ─────────────────────────────────────────────────

    def _persist_migrate_form(self) -> None:
        _save_ui_prefs({
            "target_url":       self.migrate_url_var.get().strip(),
            "project_name":     self.migrate_name_var.get().strip(),
            "competitors_text": self.comp_text.get("1.0", tk.END).rstrip("\n")
            if hasattr(self, "comp_text") else "",
        })

    def _persist_crawl_form(self) -> None:
        _save_ui_prefs({
            "target_url":       self.url_var.get().strip(),
            "project_name":     self.name_var.get().strip(),
            "competitors_text": self.comp_text.get("1.0", tk.END).rstrip("\n"),
        })

    def _on_close(self) -> None:
        try:
            self._persist_crawl_form()
        except Exception:
            pass
        self.root.destroy()

    def _load_existing(self, manager, url, name, competitors):
        if name.strip():
            try:
                cfg = manager.load_project(name.strip())
                if competitors:
                    manager.set_competitor_urls(competitors)
                if manager.active_state is not None:
                    manager.active_state.target_url = url
                    manager.active_state.data_dir   = manager.active_state.project_dir
                    manager.save_project()
                return cfg
            except WebMakerError:
                pass
        for cfg in manager.list_projects():
            if cfg.target_url.rstrip("/") == url.rstrip("/"):
                manager.load_project(cfg.id)
                if competitors:
                    manager.set_competitor_urls(competitors)
                if manager.active_state is not None:
                    manager.active_state.data_dir = manager.active_state.project_dir
                    manager.save_project()
                return cfg
        return manager.create_project(
            url, name=name, competitor_urls=competitors, force=True
        )

    def _unsubscribe(self) -> None:
        if self._manager is not None:
            try:
                self._manager.progress.unsubscribe(self._on_progress)
            except Exception:
                pass

    def _finish(self, success: bool, message: str, project_dir: str) -> None:
        self._set_busy(False)
        self._refresh_migrate_status()
        self._refresh_projects()
        if success:
            self.progress["value"] = 100
            self._set_status("Completed")
            self._append_log(message)
            if project_dir:
                self._append_log(f"Project dir: {project_dir}")
            messagebox.showinfo(
                "Done",
                message + (f"\n\nProject:\n{project_dir}" if project_dir else ""),
            )
        else:
            self._set_status("Failed")
            self._append_log(f"ERROR: {message}")
            messagebox.showerror("Failed", message)


def launch_app() -> None:
    """Create the root window and start the Tk event loop."""
    setup_logging(
        level=settings.log_level,
        log_dir=settings.logs_dir,
        log_filename=settings.log_filename,
    )
    for d in (
        settings.logs_dir,
        settings.cache_dir,
        settings.projects_dir,
        settings.outputs_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    # Open demo once on startup (canonical entry — do not also open from webmake.ps1)
    try:
        webbrowser.open(settings.wordpress_url)
    except Exception:
        pass

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass

    WebMakerApp(root)
    root.mainloop()


def main() -> None:
    launch_app()


if __name__ == "__main__":
    main()
