"""Stockage du service de maintenance de démonstration, avec contrôle d'accès à chaque appel.

Base de données : SQLite par défaut (développement) ou PostgreSQL si MAINTIDOC_DB_URL commence par
« postgresql:// » (déploiement Docker Compose).
"""
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
SCHEMA = [
    'CREATE TABLE interventions (id TEXT PRIMARY KEY, equipment_id TEXT, date TEXT, code TEXT, probleme TEXT, '
    'action TEXT, technicien TEXT, statut TEXT)',
    'CREATE TABLE tickets (id TEXT PRIMARY KEY, equipment_id TEXT, titre TEXT, description TEXT, priorite TEXT, '
    'statut TEXT, cree_le TEXT, cree_par TEXT, approuve_par TEXT, interventions_liees TEXT, references_doc TEXT, '
    'idempotency_key TEXT UNIQUE)',
]


class AccessDenied(Exception):
    pass


def _load(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


class _DB:
    """Adaptateur minimal SQLite / PostgreSQL (requêtes écrites avec « ? »)."""

    def __init__(self, url_or_path: str):
        self.pg = url_or_path.startswith('postgresql://')
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self.conn = psycopg.connect(url_or_path, row_factory=dict_row)
        else:
            Path(url_or_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(url_or_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row

    def _sql(self, sql: str) -> str:
        return sql.replace('?', '%s') if self.pg else sql

    def query(self, sql: str, params=()) -> list[dict]:
        cur = self.conn.execute(self._sql(sql), params)
        return [dict(r) for r in cur.fetchall()]

    def run(self, sql: str, params=()) -> None:
        self.conn.execute(self._sql(sql), params)

    def commit(self) -> None:
        self.conn.commit()

    def has_tables(self) -> bool:
        if self.pg:
            rows = self.query("SELECT table_name FROM information_schema.tables WHERE table_name='tickets'")
        else:
            rows = self.query("SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'")
        return bool(rows)


class MaintenanceStore:
    def __init__(self, db_path: Path | str | None = None):
        target = str(db_path) if db_path else (os.getenv('MAINTIDOC_DB_URL') or str(config.MCP_DB))
        self.db = _DB(target)
        if not self.db.has_tables():
            self._init()
        self.users = {u['user_id']: u for u in _load('utilisateurs.json')}
        self.equipment = {e['equipment_id']: e for e in _load('equipements.json')}

    def _init(self):
        for stmt in SCHEMA:
            self.db.run(stmt)
        for i in _load('historique.json'):
            self.db.run('INSERT INTO interventions VALUES (?,?,?,?,?,?,?,?)',
                        (i['id'], i['equipment_id'], i['date'], i['code'], i['probleme'], i['action'], i['technicien'],
                         i['statut']))
        for t in _load('tickets.json'):
            self.db.run('INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (t['id'], t['equipment_id'], t['titre'], t['description'], t['priorite'], t['statut'],
                         t['cree_le'], t['cree_par'], t['approuve_par'], json.dumps(t['interventions_liees']),
                         json.dumps(t['references']), None))
        self.db.commit()

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
        return self.db.query('SELECT * FROM interventions WHERE equipment_id=? ORDER BY date DESC LIMIT ?',
                             (equipment_id, max(1, min(int(limit), 20))))

    def open_tickets(self, user_id: str, equipment_id: str) -> list[dict]:
        self._check(user_id, equipment_id)
        return self.db.query("SELECT id, equipment_id, titre, priorite, statut, cree_le FROM tickets "
                             "WHERE equipment_id=? AND statut != 'clos' ORDER BY cree_le DESC", (equipment_id,))

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
        existing = self.db.query('SELECT id FROM tickets WHERE idempotency_key=?', (idempotency_key,))
        if existing:
            return {'ticket_id': existing[0]['id'], 'duplicate': True}
        n = self.count_tickets()
        tid = f'TCK-2026-{200 + n:04d}'
        self.db.run('INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (tid, equipment_id, title, description, priority, 'ouvert', time.strftime('%Y-%m-%d'),
                     user['nom'], approver['nom'], '[]', '[]', idempotency_key))
        self.db.commit()
        return {'ticket_id': tid, 'duplicate': False}

    def count_tickets(self) -> int:
        row = self.db.query('SELECT COUNT(*) AS n FROM tickets')[0]
        return int(row['n'])
