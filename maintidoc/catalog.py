"""Catalogue des métadonnées documentaires (section 3 du sujet : « PostgreSQL pour les métadonnées »).

Deux tables, synchronisées à chaque chargement de l'index :
- `documents` : identifiant, version, date, famille, modèles, groupes d'accès, niveau de confiance, documents modifiés ;
- `elements` : pour chaque élément indexé (passage, tableau, figure, consigne de sécurité), document, version, page,
  type et identifiant, sans le contenu (qui reste dans l'index Qdrant).

PostgreSQL si MAINTIDOC_DB_URL commence par « postgresql:// » (Docker Compose), SQLite sinon (data/catalog.db).
Les droits d'accès utilisés par la recherche et l'affichage sont relus depuis cette base.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import config
from .db import Database

SCHEMA = [
    'CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, fichier TEXT, titre TEXT, version TEXT, '
    'date TEXT, famille TEXT, modeles TEXT, groupes_acces TEXT, niveau_confiance TEXT, modifie TEXT, '
    'meta_json TEXT, index_version TEXT, maj_le TEXT)',
    'CREATE TABLE IF NOT EXISTS elements (cle TEXT PRIMARY KEY, document_id TEXT, version TEXT, page INTEGER, '
    'element_id TEXT, type TEXT, securite INTEGER, legende TEXT, nb_caracteres INTEGER)',
]


class Catalog:
    def __init__(self, url: str | None = None):
        self.db = Database(url or os.getenv('MAINTIDOC_DB_URL') or str(config.CATALOG_DB))
        self.kind = 'postgresql' if self.db.pg else 'sqlite'
        for sql in SCHEMA:
            self.db.run(sql)
        self.db.commit()

    def sync(self, metadata: dict[str, dict], elements: list[dict], index_version: str) -> None:
        """Remplace le contenu du catalogue par les métadonnées courantes, en une seule transaction."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        docs = [(m['document_id'], m['fichier'], m['titre'], m['version'], m.get('date'), m['famille'],
                 json.dumps(m['modeles'], ensure_ascii=False), json.dumps(m['groupes_acces'], ensure_ascii=False),
                 m['niveau_confiance'], json.dumps(m.get('modifie', []), ensure_ascii=False),
                 json.dumps(m, ensure_ascii=False), index_version, now) for m in metadata.values()]
        elems = [(e['key'], e['document_id'], e['version'], e['page'], e['element_id'], e['type'],
                  int(bool(e.get('securite'))), e.get('caption'), len(e.get('content') or ''))
                 for e in elements if e['document_id'] in metadata]
        try:
            self.db.run('DELETE FROM elements')
            self.db.run('DELETE FROM documents')
            self.db.run_many('INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', docs)
            self.db.run_many('INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', elems)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def documents(self) -> dict[str, dict]:
        """Métadonnées par document, relues depuis la base ; les droits viennent de la colonne groupes_acces."""
        out = {}
        for row in self.db.query('SELECT document_id, groupes_acces, modeles, meta_json FROM documents'):
            meta = json.loads(row['meta_json'])
            meta['groupes_acces'] = json.loads(row['groupes_acces'])
            meta['modeles'] = json.loads(row['modeles'])
            out[row['document_id']] = meta
        return out

    def count(self, table: str) -> int:
        if table not in ('documents', 'elements'):
            raise ValueError(table)
        return self.db.query(f'SELECT COUNT(*) AS n FROM {table}')[0]['n']
