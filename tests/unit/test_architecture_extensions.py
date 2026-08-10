"""Unit tests for schema versioning, progress, prompts, AI cache, jobs, plugins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webmaker.core.progress import ProgressEvent, ProgressManager
from webmaker.core.prompts import PromptLoader, load_prompt, load_prompt_or_default
from webmaker.core.schema import (
    SCHEMA_VERSION,
    ensure_schema_version,
    load_json_list,
    unwrap_json,
    write_versioned_json,
)
from webmaker.modules.ai_cache import AICache
from webmaker.modules.job_manager import Job, JobManager, JobResult, JobStatus, JobType
from webmaker.plugins import Plugin, PluginRegistry, register_plugin
from webmaker.plugins.registry import plugin_registry


# ── Schema ─────────────────────────────────────────────────────────────────────

class TestSchemaVersion:
    def test_ensure_dict(self):
        out = ensure_schema_version({"a": 1})
        assert out["schema_version"] == SCHEMA_VERSION
        assert out["a"] == 1

    def test_ensure_does_not_clobber(self):
        out = ensure_schema_version({"schema_version": 99, "a": 1})
        assert out["schema_version"] == 99

    def test_ensure_list_wraps(self):
        out = ensure_schema_version([1, 2])
        assert out == {"schema_version": SCHEMA_VERSION, "items": [1, 2]}

    def test_unwrap_legacy_list(self):
        assert unwrap_json([1, 2]) == [1, 2]

    def test_unwrap_versioned_list(self):
        assert unwrap_json({"schema_version": 1, "items": [1, 2]}) == [1, 2]

    def test_write_and_load_list(self, tmp_path: Path):
        path = tmp_path / "pages.json"
        write_versioned_json(path, [{"url": "x"}])
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 1
        assert load_json_list(path) == [{"url": "x"}]


# ── Progress ───────────────────────────────────────────────────────────────────

class TestProgressManager:
    def test_emit_and_subscribe(self):
        pm = ProgressManager()
        seen: list[ProgressEvent] = []
        pm.subscribe(seen.append)
        ev = pm.emit(10, "Crawling website", phase="crawl", project_id="p1")
        assert ev.percent == 10
        assert len(seen) == 1
        assert seen[0].message == "Crawling website"
        assert pm.latest is not None
        assert pm.latest.percent == 10

    def test_emit_phase_milestones(self):
        pm = ProgressManager()
        ev = pm.emit_phase("optimize", "Generating homepage", project_id="x")
        assert ev.percent == pytest.approx(60.0)
        assert ev.phase == "optimize"

    def test_listener_exception_isolated(self):
        pm = ProgressManager()

        def bad(_e):
            raise RuntimeError("boom")

        pm.subscribe(bad)
        # Must not raise
        pm.emit(50, "ok")


# ── Prompts ────────────────────────────────────────────────────────────────────

class TestPromptLoader:
    def test_load_from_repo_prompts(self):
        # Repo prompts/ should exist with business.md
        text = load_prompt("business")
        assert "business analyst" in text.lower() or "JSON" in text

    def test_interpolate(self, tmp_path: Path):
        (tmp_path / "hello.md").write_text("Hello {{name}}!", encoding="utf-8")
        loader = PromptLoader(tmp_path)
        assert loader.load("hello", variables={"name": "WebMaker"}) == "Hello WebMaker!"

    def test_missing_raises(self, tmp_path: Path):
        from webmaker.core.exceptions import ConfigurationError
        loader = PromptLoader(tmp_path)
        with pytest.raises(ConfigurationError):
            loader.load("nope")

    def test_load_or_default(self, tmp_path: Path):
        loader = PromptLoader(tmp_path)
        assert loader.load_or_default("missing", "FALLBACK") == "FALLBACK"


# ── AI Cache ───────────────────────────────────────────────────────────────────

class TestAICache:
    def test_set_get_invalidate(self, tmp_path: Path):
        cache = AICache(tmp_path, enabled=True)
        key = AICache.make_key(
            model="m",
            provider="gemini",
            system="sys",
            prompt="hi",
            context={"a": 1},
        )
        assert cache.get(key) is None
        cache.set(key, provider="gemini", model="m", response_text="hello")
        hit = cache.get(key)
        assert hit is not None
        assert hit.response_text == "hello"
        assert cache.invalidate(key) == 1
        assert cache.get(key) is None

    def test_disabled_always_misses(self, tmp_path: Path):
        cache = AICache(tmp_path, enabled=False)
        key = AICache.make_key(
            model="m", provider="claude", system="", prompt="x", context=None
        )
        cache.set(key, provider="claude", model="m", response_text="nope")
        assert cache.get(key) is None

    def test_key_stable(self):
        a = AICache.make_key(
            model="m", provider="p", system="s", prompt="u", context={"b": 2, "a": 1}
        )
        b = AICache.make_key(
            model="m", provider="p", system="s", prompt="u", context={"a": 1, "b": 2}
        )
        assert a == b


# ── Job Manager ────────────────────────────────────────────────────────────────

class TestJobManager:
    def test_create_execute_complete(self, tmp_path: Path):
        jm = JobManager(storage_dir=tmp_path / "jobs")

        def handler(job: Job) -> JobResult:
            jm.update_progress(job.id, 50, "halfway")
            return JobResult(success=True, message="done", artifacts=["a.json"])

        jm.register_handler(JobType.RUN_QA, handler)
        job = jm.create_job(JobType.RUN_QA, "proj1", enqueue=False)
        result = jm.execute(job.id)
        assert result.success
        assert jm.get_job(job.id).status == JobStatus.COMPLETED.value
        assert jm.get_job(job.id).progress == 100.0
        assert "halfway" in "\n".join(jm.get_job(job.id).execution_log)

    def test_cancel_queued(self, tmp_path: Path):
        jm = JobManager(storage_dir=tmp_path / "jobs")
        jm.register_handler("custom", lambda j: JobResult(success=True))
        job = jm.create_job("custom", "p", enqueue=True)
        jm.cancel(job.id)
        assert jm.get_job(job.id).status == JobStatus.CANCELLED.value

    def test_retry_and_resume(self, tmp_path: Path):
        jm = JobManager(storage_dir=tmp_path / "jobs")
        calls = {"n": 0}

        def handler(job: Job):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("fail once")
            return JobResult(success=True, message="ok")

        jm.register_handler(JobType.REBUILD_WORDPRESS, handler)
        job = jm.create_job(
            JobType.REBUILD_WORDPRESS, "p", max_retries=1, enqueue=False
        )
        result = jm.execute(job.id)
        assert result.success
        assert calls["n"] == 2

    def test_queue_fifo(self, tmp_path: Path):
        jm = JobManager(storage_dir=tmp_path / "jobs")
        order: list[str] = []

        def handler(job: Job):
            order.append(job.job_type)
            return JobResult(success=True)

        jm.register_handler("a", handler)
        jm.register_handler("b", handler)
        jm.create_job("a", "p")
        jm.create_job("b", "p")
        jm.process_queue()
        assert order == ["a", "b"]


# ── Plugins ────────────────────────────────────────────────────────────────────

class TestPlugins:
    def test_hooks_called(self):
        registry = PluginRegistry()
        events: list[str] = []

        class Demo(Plugin):
            name = "demo"

            def before_phase(self, phase, project_state, context):
                events.append(f"before:{phase}")

            def after_phase(self, phase, project_state, context, *, success):
                events.append(f"after:{phase}:{success}")

            def before_job(self, job, context):
                events.append("before_job")

            def after_job(self, job, result, context):
                events.append("after_job")

        registry.register(Demo())
        registry.call_before_phase("crawl", None)
        registry.call_after_phase("crawl", None, success=True)
        registry.call_before_job(Job(id="1", job_type="x"))
        registry.call_after_job(Job(id="1"), JobResult(success=True))
        assert events == [
            "before:crawl",
            "after:crawl:True",
            "before_job",
            "after_job",
        ]

    def test_plugin_exception_isolated(self):
        registry = PluginRegistry()

        class Bad(Plugin):
            name = "bad"

            def before_phase(self, phase, project_state, context):
                raise RuntimeError("nope")

        registry.register(Bad())
        registry.call_before_phase("analyze", None)  # must not raise

    def test_discover_empty_dir(self, tmp_path: Path):
        registry = PluginRegistry()
        assert registry.discover(tmp_path) == []

    def test_global_register(self):
        class Tmp(Plugin):
            name = "tmp_test_plugin"

        plugin_registry.unregister("tmp_test_plugin")
        register_plugin(Tmp())
        assert any(p.name == "tmp_test_plugin" for p in plugin_registry.list())
        plugin_registry.unregister("tmp_test_plugin")
