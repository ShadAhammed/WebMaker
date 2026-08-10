"""
webmaker.agents
===============
Single-responsibility agents for the WebMaker V2 architecture.

Each agent:
- has exactly ONE responsibility,
- reads a strictly-typed input artifact and produces a strictly-typed output,
- never imports or calls another agent (the Orchestrator wires them),
- never mutates its input.

Import concrete agents from their subpackages, e.g.::

    from webmaker.agents.target_crawler import TargetCrawlerAgent
"""

from __future__ import annotations

from webmaker.agents.base import AgentContext, BaseAgent

__all__ = ["AgentContext", "BaseAgent"]
