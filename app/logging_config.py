"""
Root logging setup.

Uvicorn only configures its own ``uvicorn.*`` loggers, so every ``logging.getLogger("app.…")``
call in this codebase would otherwise fall through to ``logging.lastResort``, which drops
anything below WARNING. On a local terminal that is merely annoying; on a hosted service it
means the ingestion pipeline's INFO/DEBUG diagnostics never reach the log stream at all.

``configure_logging`` is called first thing in ``create_app`` so app loggers are wired up
before any other startup work can emit.
"""
from __future__ import annotations

import logging
import sys

_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Attach a stdout handler to the root logger. Idempotent."""
    global _configured
    if _configured:
        return

    resolved = getattr(logging, str(level).upper(), logging.INFO)
    if not isinstance(resolved, int):
        resolved = logging.INFO

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.setLevel(resolved)
    # Replace any handler we installed on a previous call (e.g. uvicorn --reload re-import).
    for existing in list(root.handlers):
        if getattr(existing, "_farmdss_handler", False):
            root.removeHandler(existing)
    handler._farmdss_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # The app's own loggers follow the configured level; keep third-party chatter quieter.
    logging.getLogger("app").setLevel(resolved)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(max(resolved, logging.WARNING))

    _configured = True
