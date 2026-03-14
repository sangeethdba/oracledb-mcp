#!/usr/bin/env python3

import asyncio

import shared_utils


class _FakeMeta:
    def model_dump(self, exclude_none=True):
        return {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "baggage": "k=v",
        }


class _FakeReqContext:
    meta = _FakeMeta()


def test_extract_propagation_carrier():
    carrier = shared_utils._extract_propagation_carrier(_FakeReqContext())
    assert carrier["traceparent"].startswith("00-")
    assert carrier["baggage"] == "k=v"


def test_trace_tool_noop_when_disabled():
    shared_utils._TRACING_ENABLED = False
    shared_utils._TRACER = None

    @shared_utils.trace_tool
    async def sample_tool(x: int) -> int:
        return x + 1

    result = asyncio.run(sample_tool(2))
    assert result == 3
