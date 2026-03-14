#!/usr/bin/env python3
"""Generate a Markdown catalog of MCP tools from oracledb_mcp.py."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Any, List


def _default_for_annotation(annotation: Any) -> str:
    text = str(annotation)
    if "int" in text:
        return "1"
    if "float" in text:
        return "1.0"
    if "bool" in text:
        return "false"
    if "Dict" in text or "dict" in text:
        return "{}"
    if "List" in text or "list" in text:
        return "[]"
    return '"<value>"'


def _example_call(name: str, sig: inspect.Signature) -> str:
    args: List[str] = []
    for p in sig.parameters.values():
        if p.name == "self":
            continue
        if p.default is inspect._empty:
            args.append(f"{p.name}={_default_for_annotation(p.annotation)}")
        else:
            if isinstance(p.default, str):
                val = f'"{p.default}"'
            else:
                val = repr(p.default)
            args.append(f"{p.name}={val}")
    return f"{name}({', '.join(args)})"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("ORACLE_USER", "doc_user")
    os.environ.setdefault("ORACLE_PASSWORD", "doc_password")
    os.environ.setdefault("ORACLE_DSN", "localhost:1521/XEPDB1")
    sys.path.insert(0, str(repo_root))

    import oracledb_mcp  # noqa: WPS433

    tools = oracledb_mcp.mcp._tool_manager._tools
    names = sorted(tools.keys())

    lines: List[str] = []
    lines.append("# OracleDB MCP Tool Catalog")
    lines.append("")
    lines.append(f"Total tools: **{len(names)}**")
    lines.append("")
    lines.append("Call format in MCP clients:")
    lines.append("- `tool_name(param=value, ...)`")
    lines.append("- Use named arguments exactly as shown in each signature.")
    lines.append("")

    for idx, name in enumerate(names, 1):
        tool = tools[name]
        fn = tool.fn
        sig = inspect.signature(fn)
        doc = (inspect.getdoc(fn) or "").strip()
        summary = doc.splitlines()[0] if doc else "No description."
        lines.append(f"## {idx}. `{name}`")
        lines.append(f"- Signature: `{name}{sig}`")
        lines.append(f"- Purpose: {summary}")
        lines.append("- Example call:")
        lines.append("```text")
        lines.append(_example_call(name, sig))
        lines.append("```")
        lines.append("")

    out = repo_root / "docs" / "TOOL_CATALOG.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
