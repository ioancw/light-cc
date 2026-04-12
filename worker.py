"""arq worker entry point.

Run with: arq worker.WorkerSettings
"""

from arq.connections import RedisSettings

from core.config import settings

# Import tools to ensure job registrations happen
import tools.background  # noqa: F401
import core.scheduler  # noqa: F401 — registers "run_scheduled_agent" job
import core.agent_runner  # noqa: F401 — registers "execute_agent_run" job
import core.memory_extractor  # noqa: F401 — registers "extract_memories_from_conversation" job

from core.job_queue import get_arq_worker_functions


class WorkerSettings:
    functions = get_arq_worker_functions()
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")
    max_jobs = 10
    job_timeout = 600  # 10 minutes
