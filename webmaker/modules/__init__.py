"""
webmaker.modules
================
Primary application modules. Each module exposes one public class.

    WebsiteCrawler       – Retrieves and structures target website content.
    BusinessAnalyzer     – Derives business profile from crawl data.
    CompetitorAnalyzer   – Discovers and benchmarks competitors.
    ContentOptimizer     – Produces SEO-ready content recommendations.
    WordPressGenerator   – Builds the demo WordPress site.
    QAReviewer           – Validates the generated site.
    ProjectManager       – Coordinates the complete pipeline.
    AIRouter             – Dispatches requests to AI providers.
    LibraryBuilder       – Builds a reusable visual design library from a URL.
"""

from webmaker.modules.website_crawler    import WebsiteCrawler
from webmaker.modules.business_analyzer  import BusinessAnalyzer
from webmaker.modules.competitor_analyzer import CompetitorAnalyzer
from webmaker.modules.content_optimizer  import ContentOptimizer
from webmaker.modules.wordpress_generator import WordPressGenerator
from webmaker.modules.qa_reviewer        import QAReviewer
from webmaker.modules.website_fixer      import WebsiteFixer
from webmaker.modules.project_manager    import ProjectManager
from webmaker.modules.ai_router          import AIRouter
from webmaker.modules.ai_cache           import AICache
from webmaker.modules.job_manager        import JobManager, Job, JobResult, JobStatus, JobType
from webmaker.modules.library_builder    import LibraryBuilder

__all__ = [
    "WebsiteCrawler",
    "BusinessAnalyzer",
    "CompetitorAnalyzer",
    "ContentOptimizer",
    "WordPressGenerator",
    "QAReviewer",
    "WebsiteFixer",
    "ProjectManager",
    "AIRouter",
    "AICache",
    "JobManager",
    "Job",
    "JobResult",
    "JobStatus",
    "JobType",
    "LibraryBuilder",
]
