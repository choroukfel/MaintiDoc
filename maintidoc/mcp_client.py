"""Client MCP (transport stdio) : lance le serveur de maintenance comme sous-processus."""
from __future__ import annotations

import asyncio
import json
import os
import sys

from . import config


async def _call(calls: list[tuple[str, dict]]) -> list[dict]:
    from mcp import Client, StdioServerParameters
    params = StdioServerParameters(command=sys.executable, args=['-m', 'mcp_server.server'], cwd=str(config.ROOT),
                                   env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    out = []
    async with Client(params, read_timeout_seconds=60) as client:
        for name, args in calls:
            res = await client.call_tool(name, args)
            text = ''.join(getattr(c, 'text', '') for c in res.content)
            try:
                out.append(json.loads(text))
            except json.JSONDecodeError:
                out.append({'ok': False, 'error': 'invalid_response', 'message': text[:200]})
    return out


def call_tools(calls: list[tuple[str, dict]]) -> list[dict]:
    """Exécute plusieurs appels d'outils dans une même session MCP (synchrone)."""
    return asyncio.run(_call(calls))


async def list_tools() -> list[str]:
    from mcp import Client, StdioServerParameters
    params = StdioServerParameters(command=sys.executable, args=['-m', 'mcp_server.server'], cwd=str(config.ROOT),
                                   env=dict(os.environ))
    async with Client(params) as client:
        return [t.name for t in (await client.list_tools()).tools]
