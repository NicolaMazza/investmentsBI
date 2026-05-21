from __future__ import annotations

import logging
import logging.config
import os
import sys


def setup_logging(level: str | None = None) -> None:
    from app.config import settings

    log_level = (level or settings.log_level).upper()
    is_production = os.getenv("SUPERVISOR_TOKEN") is not None

    if is_production:
        fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    else:
        fmt = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": fmt, "datefmt": "%Y-%m-%dT%H:%M:%S"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "default",
                }
            },
            "root": {"level": log_level, "handlers": ["console"]},
            "loggers": {
                "uvicorn": {"propagate": True},
                "uvicorn.access": {"propagate": True},
                "sqlalchemy.engine": {
                    "level": "WARNING",
                    "propagate": True,
                },
            },
        }
    )
