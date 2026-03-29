"""Import all tool modules to trigger registration."""

import logging

logger = logging.getLogger(__name__)

# Core tools — no optional dependencies
import tools.bash  # noqa: F401
import tools.read  # noqa: F401
import tools.write  # noqa: F401
import tools.edit  # noqa: F401
import tools.grep  # noqa: F401
import tools.glob_tool  # noqa: F401
import tools.python_exec  # noqa: F401
import tools.subagent  # noqa: F401
import tools.tasks  # noqa: F401
import tools.background  # noqa: F401
import tools.tool_search  # noqa: F401
import memory.manager  # noqa: F401 — registers memory tools

# Optional tools — require pandas, plotly
try:
    import tools.data_tools  # noqa: F401
    import tools.chart  # noqa: F401
except ImportError as e:
    logger.warning(f"Optional data tools not loaded (install pandas/plotly): {e}")

# Optional tools — require httpx, duckduckgo-search
try:
    import tools.web  # noqa: F401
except ImportError as e:
    logger.warning(f"Optional web tools not loaded (install httpx/duckduckgo-search): {e}")
