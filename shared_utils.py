#!/usr/bin/env python3
"""
Local shared utilities for standalone OracleDB MCP server.
"""

import json
import logging
from contextlib import contextmanager
from functools import wraps
from typing import Any, Dict


class CustomJSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "service": getattr(record, "service", "mcp-server"),
        }
        if record.exc_info:
            log_data["error"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def get_logger(service_name: str) -> logging.Logger:
    import sys

    logger = logging.getLogger(service_name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(CustomJSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.service = service_name
            return record

        logging.setLogRecordFactory(record_factory)
    return logger


class JSONFormatter:
    @staticmethod
    def format_response(data: Any, indent: int = None, optimize: bool = False, warnings: list = None) -> str:
        try:
            if warnings:
                if isinstance(data, dict):
                    data["_warnings"] = warnings
                else:
                    data = {"data": data, "_warnings": warnings}
            return json.dumps(data, indent=indent, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": f"JSON formatting error: {str(e)}"}, indent=indent)


def initialize_tracing(service_name: str, service_version: str = "dev") -> None:
    # No-op for standalone local usage.
    print(f"tracing disabled for {service_name} (set TRACING=enabled to enable)")


def trace_tool(func):
    # No-op decorator compatible with async and sync functions.
    if callable(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        return async_wrapper
    return func
