"""Serveur MCP de maintenance (démonstration).

Outils : get_equipment_history, get_open_tickets, create_ticket.
Les autorisations sont vérifiées côté serveur à chaque appel ; create_ticket exige un jeton
d'approbation HMAC lié à l'approbateur et au contenu exact, plus une clé d'idempotence.

Lancement : python -m mcp_server.server   (transport stdio)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from mcp_server.store import AccessDenied, MaintenanceStore  # noqa: E402

mcp = MCPServer('maintidoc-maintenance')
_store: MaintenanceStore | None = None


def store() -> MaintenanceStore:
    global _store
    if _store is None:
        _store = MaintenanceStore()
    return _store


def _wrap(fn, *args) -> str:
    try:
        return json.dumps({'ok': True, 'data': fn(*args)}, ensure_ascii=False)
    except AccessDenied as ex:
        return json.dumps({'ok': False, 'error': 'access_denied', 'message': str(ex)}, ensure_ascii=False)
    except ValueError as ex:
        return json.dumps({'ok': False, 'error': 'invalid', 'message': str(ex)}, ensure_ascii=False)


@mcp.tool()
def get_equipment_history(user_id: str, equipment_id: str, limit: int = 10) -> str:
    """Historique des interventions d'un équipement (les plus récentes d'abord)."""
    return _wrap(store().history, user_id, equipment_id, limit)


@mcp.tool()
def get_open_tickets(user_id: str, equipment_id: str) -> str:
    """Tickets non clos d'un équipement."""
    return _wrap(store().open_tickets, user_id, equipment_id)


@mcp.tool()
def create_ticket(user_id: str, approver_id: str, equipment_id: str, title: str, description: str,
                  priority: str, approval_token: str, idempotency_key: str) -> str:
    """Crée un ticket APRÈS approbation humaine (jeton HMAC lié à l'approbateur et au contenu exact)."""
    return _wrap(store().create_ticket, user_id, approver_id, equipment_id, title, description, priority,
                 approval_token, idempotency_key)


if __name__ == '__main__':
    mcp.run('stdio')
