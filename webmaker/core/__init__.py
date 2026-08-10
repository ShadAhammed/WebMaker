"""
webmaker.core
=============
Core infrastructure: exceptions, logging, shared types.
Import from here rather than from sub-modules directly.
"""

from webmaker.core.exceptions import (
    WebMakerError,
    ConfigurationError,
    CrawlerError,
    AnalysisError,
    GenerationError,
    QAError,
    ProjectError,
    AIError,
    DatabaseError,
    WordPressError,
)
from webmaker.core.logging import setup_logging, get_logger
from webmaker.core.types import (
    ProjectStatus,
    AIProvider,
    ProjectConfig,
    PageData,
    CrawlResult,
    BusinessInfo,
    AnalysisResult,
    GenerationResult,
    QAReport,
)
from webmaker.core.schema import SCHEMA_VERSION, ensure_schema_version, write_versioned_json
from webmaker.core.progress import ProgressEvent, ProgressManager, progress_manager
from webmaker.core.prompts import load_prompt, PromptLoader

__all__ = [
    # exceptions
    "WebMakerError",
    "ConfigurationError",
    "CrawlerError",
    "AnalysisError",
    "GenerationError",
    "QAError",
    "ProjectError",
    "AIError",
    "DatabaseError",
    "WordPressError",
    # logging
    "setup_logging",
    "get_logger",
    # types
    "ProjectStatus",
    "AIProvider",
    "ProjectConfig",
    "PageData",
    "CrawlResult",
    "BusinessInfo",
    "AnalysisResult",
    "GenerationResult",
    "QAReport",
    # schema / progress / prompts
    "SCHEMA_VERSION",
    "ensure_schema_version",
    "write_versioned_json",
    "ProgressEvent",
    "ProgressManager",
    "progress_manager",
    "load_prompt",
    "PromptLoader",
]
