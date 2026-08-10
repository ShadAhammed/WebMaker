"""MigrationAgent (Agent 0) package."""

from __future__ import annotations

from webmaker.agents.migration_agent.agent import MigrationAgent
from webmaker.schemas.migration import MigrateInput, MigrationResult

__all__ = ["MigrationAgent", "MigrateInput", "MigrationResult"]
