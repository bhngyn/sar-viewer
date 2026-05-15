"""Database initialisation for the API service.

We reuse the worker's synchronous SQLAlchemy engine (same SQLite file) for
simplicity in Phase 1.  The API is single-threaded (uvicorn default) and
FastAPI is configured to run sync route handlers in a thread pool, so
synchronous SQLAlchemy is safe here.

In Phase 2+ this could be replaced with an async engine if needed; that would
be an interface-change request.
"""

from __future__ import annotations

# Explicit re-exports so mypy's --strict mode (and the module's public surface)
# is well-defined.
from services.worker.src.db import (
    JobRow as JobRow,
)
from services.worker.src.db import (
    SessionLocal as SessionLocal,
)
from services.worker.src.db import (
    create_job as create_job,
)
from services.worker.src.db import (
    get_job as get_job,
)
from services.worker.src.db import (
    init_db as init_db,
)
from services.worker.src.db import (
    list_jobs as list_jobs,
)
