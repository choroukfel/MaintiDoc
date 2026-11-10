"""Stockage SQLite du service de maintenance de démonstration, avec contrôle d'accès à chaque appel."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maintidoc import config, security  # noqa: E402

DATA = Path(__file__).resolve().parent / 'data'


class AccessDenied(Exception):
    pass


def _load(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


class MaintenanceStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or config.MCP_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.db_path.exists()
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if new:
            self._init()
        self.users = {u['user_id']: u for u in _load('utilisateurs.json')}
        self.equipment = {e['equipment_id']: e for e in _load('equipements.json')}

    def _init(self):
        c = self.db
        c.execute('CREATE TABLE interventions (id TEXT PRIMARY KEY, equipment_id TEXT, date TEXT, code TEXT, '
                  'probleme TEXT, action TEXT, technicien TEXT, statut TEXT)')
        c.execute('CREATE TABLE tickets (id TEXT PRIMARY KEY, equipment_id TEXT, titre TEXT, description TEXT, '
                  'priorite TEXT, statut TEXT, cree_le TEXT, cree_par TEXT, approuve_par TEXT, '
                  'interventions_liees TEXT, references_doc TEXT, idempotency_key TEXT UNIQUE)')
        for i in _load('historique.json'):
            c.execute('INSERT INTO interventions VALUES (?,?,?,?,?,?,?,?)',
                      (i['id'], i['equipment_id'], i['date'], i['code'], i['probleme'], i['action'], i['technicien'],
                       i['statut']))
        for t in _load('tickets.json'):
            c.execute('INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                      (t['id'], t['equipment_id'], t['titre'], t['description'], t['priorite'], t['statut'],
                       t['cree_le'], t['cree_par'], t['approuve_par'], json.dumps(t['interventions_liees']),
                       json.dumps(t['references']), None))
        c.commit()

    # ------------------------------------------------------------------ contrôle d'accès
    def _check(self, user_id: str, equipment_id: str) -> dict:
        user = self.users.get(user_id)
        if user is None:
            raise AccessDenied('utilisateur inconnu')
        eq = self.equipment.get(equipment_id)
        if eq is None:
            raise AccessDenied('équipement inconnu')
        if eq['groupe_acces'] not in user['groupes']:
            raise AccessDenied(f"l'utilisateur {user['nom']} n'a pas accès à {equipment_id}")
        return user

    # ------------------------------------------------------------------ outils
    def history(self, user_id: str, equipment_id: str, limit: int = 10) -> list[dict]:
        self._check(user_id, equipment_id)
        rows = self.db.execute('SELECT * FROM interventions WHERE equipment_id=? ORDER BY date DESC LIMIT ?',
                               (equipment_id, max(1, min(limit, 20)))).fetchall()
        return [dict(r) for r in rows]

    def open_tickets(self, user_id: str, equipment_id: str) -> list[dict]:
        self._check(user_id, equipment_id)
        rows = self.db.execute("SELECT id, equipment_id, titre, priorite, statut, cree_le FROM tickets "
                               "WHERE equipment_id=? AND statut != 'clos' ORDER BY cree_le DESC",
                               (equipment_id,)).fetchall()
        return [dict(r) for r in rows]

    def create_ticket(self, user_id: str, approver_id: str, equipment_id: str, title: str, description: str,
                      priority: str, approval_token: str, idempotency_key: str) -> dict:
        user = self._check(user_id, equipment_id)
        if not user['peut_preparer_ticket']:
            raise AccessDenied(f"{user['nom']} ne peut pas préparer de ticket")
        approver = self._check(approver_id, equipment_id)
        if not approver['peut_approuver_ticket']:
            raise AccessDenied(f"{approver['nom']} ne peut pas approuver de ticket")
        if priority not in ('basse', 'moyenne', 'haute'):
            raise ValueError('priorité invalide')
        chash = security.content_hash(equipment_id, title, description, priority)
        secret = os.environ.get('MAINTIDOC_APPROVAL_SECRET')
        if not security.verify_approval(approval_token, approver_id, equipment_id, chash, idempotency_key, secret):
            raise AccessDenied("jeton d'approbation invalide (contenu modifié ou approbation absente)")
        existing = self.db.execute('SELECT id FROM tickets WHERE idempotency_key=?', (idempotency_key,)).fetchone()
        if existing:
            return {'ticket_id': existing['id'], 'duplicate': True}
        n = self.db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0]
        tid = f'TCK-2026-{200 + n:04d}'
        self.db.execute('INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (tid, equipment_id, title, description, priority, 'ouvert', time.strftime('%Y-%m-%d'),
                         user['nom'], approver['nom'], '[]', '[]', idempotency_key))
        self.db.commit()
        return {'ticket_id': tid, 'duplicate': False}

    def count_tickets(self) -> int:
        return self.db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0]
