#!/usr/bin/env python3
"""
Local shared utilities for standalone OracleDB MCP server.
"""

import json
import logging
import os
import sys
from inspect import iscoroutinefunction
from functools import wraps
from typing import Any, Dict, Optional, Tuple

from fastmcp.server.context import request_ctx

_TRACER = None
_TRACING_ENABLED = False

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


def _parse_otel_headers(value: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for item in (value or "").split(","):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, val = part.split("=", 1)
        headers[key.strip()] = val.strip()
    return headers


def _extract_request_context() -> Any:
    try:
        return request_ctx.get()
    except LookupError:
        return None


def _extract_propagation_carrier(req_context: Any) -> Dict[str, str]:
    carrier: Dict[str, str] = {}
    if req_context is None or getattr(req_context, "meta", None) is None:
        return carrier

    meta = req_context.meta
    try:
        payload = meta.model_dump(exclude_none=True)
    except Exception:
        payload = {}

    # Direct keys in _meta
    for key in ("traceparent", "tracestate", "baggage"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            carrier[key] = value.strip()

    # Nested carrier conventions used by some clients.
    for container_key in ("context", "otel", "trace"):
        container = payload.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in ("traceparent", "tracestate", "baggage"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                carrier[key] = value.strip()

    return carrier


def _span_attrs(func_name: str, kwargs: Dict[str, Any], req_context: Any) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {
        "mcp.server.name": "oracledb-mcp",
        "mcp.tool.name": func_name,
        "mcp.tool.argument_count": len(kwargs),
        "mcp.tool.arguments": ",".join(sorted(kwargs.keys())) if kwargs else "",
        "gen_ai.system": "mcp",
        "gen_ai.operation.name": "tool.call",
    }
    if req_context is not None:
        if getattr(req_context, "request_id", None) is not None:
            attrs["mcp.request.id"] = str(req_context.request_id)
        if getattr(req_context, "session", None) is not None and getattr(req_context.session, "id", None) is not None:
            attrs["mcp.session.id"] = str(req_context.session.id)
    return attrs


def initialize_tracing(service_name: str, service_version: str = "dev") -> None:
    global _TRACER, _TRACING_ENABLED

    if os.getenv("TRACING", "").lower() != "enabled":
        # Keep startup fully silent unless tracing is explicitly enabled.
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except Exception as e:
        print(f"tracing init failed for {service_name}: {e}", file=sys.stderr)
        return

    try:
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment": os.getenv("OTEL_ENVIRONMENT", os.getenv("ENVIRONMENT", "local")),
            }
        )
        provider = TracerProvider(resource=resource)

        exporter_kind = os.getenv("OTEL_TRACES_EXPORTER", "otlp").strip().lower()
        if exporter_kind == "console":
            exporter = ConsoleSpanExporter()
        else:
            headers = _parse_otel_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""))
            exporter = OTLPSpanExporter(
                endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")),
                headers=headers or None,
            )

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer(service_name, service_version)
        _TRACING_ENABLED = True
        print(f"tracing enabled for {service_name}", file=sys.stderr)
    except Exception as e:
        _TRACING_ENABLED = False
        _TRACER = None
        print(f"tracing init failed for {service_name}: {e}", file=sys.stderr)


def trace_tool(func):
    if not callable(func):
        return func

    async def _run_async(*args, **kwargs):
        if not _TRACING_ENABLED or _TRACER is None:
            return await func(*args, **kwargs)

        from opentelemetry import context as otel_context
        from opentelemetry import propagate
        from opentelemetry.trace import SpanKind, Status, StatusCode

        req_context = _extract_request_context()
        carrier = _extract_propagation_carrier(req_context)
        parent = propagate.extract(carrier=carrier) if carrier else otel_context.get_current()

        with _TRACER.start_as_current_span(
            name=f"mcp.tool.{func.__name__}",
            context=parent,
            kind=SpanKind.SERVER,
        ) as span:
            for k, v in _span_attrs(func.__name__, kwargs, req_context).items():
                if v is not None:
                    span.set_attribute(k, v)
            if carrier.get("traceparent"):
                span.set_attribute("mcp.context.traceparent.present", True)
            try:
                result = await func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    def _run_sync(*args, **kwargs):
        if not _TRACING_ENABLED or _TRACER is None:
            return func(*args, **kwargs)

        from opentelemetry import context as otel_context
        from opentelemetry import propagate
        from opentelemetry.trace import SpanKind, Status, StatusCode

        req_context = _extract_request_context()
        carrier = _extract_propagation_carrier(req_context)
        parent = propagate.extract(carrier=carrier) if carrier else otel_context.get_current()

        with _TRACER.start_as_current_span(
            name=f"mcp.tool.{func.__name__}",
            context=parent,
            kind=SpanKind.SERVER,
        ) as span:
            for k, v in _span_attrs(func.__name__, kwargs, req_context).items():
                if v is not None:
                    span.set_attribute(k, v)
            if carrier.get("traceparent"):
                span.set_attribute("mcp.context.traceparent.present", True)
            try:
                result = func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    if iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await _run_async(*args, **kwargs)

        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        return _run_sync(*args, **kwargs)

    return sync_wrapper
