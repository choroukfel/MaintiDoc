"""Accès base de données commun : SQLite (développement, tests) ou PostgreSQL (déploiement Docker Compose).

Les requêtes sont écrites avec « ? » et converties en « %s » pour PostgreSQL.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
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

    def run_many(self, sql: str, rows: list[tuple]) -> None:
        if self.pg:
            with self.conn.cursor() as cur:
                cur.executemany(self._sql(sql), rows)
        else:
            self.conn.executemany(sql, rows)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def has_table(self, name: str) -> bool:
        if self.pg:
            rows = self.query('SELECT table_name FROM information_schema.tables WHERE table_name = ?', (name,))
        else:
            rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
        return bool(rows)

    def close(self) -> None:
        self.conn.close()
