"""Catalogue des métadonnées (PostgreSQL ou SQLite) et préchargement du modèle au démarrage de l'API."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from maintidoc import config, ingestion
from maintidoc.catalog import Catalog


def test_catalog_sync_round_trip(retriever, tmp_path):
    cat = Catalog(str(tmp_path / 'catalog.db'))
    meta = ingestion.load_metadata()
    cat.sync(meta, list(retriever.elements.values()), retriever.version)
    assert cat.documents() == meta
    assert cat.count('documents') == len(meta)
    assert cat.count('elements') == len([e for e in retriever.elements.values() if e['document_id'] in meta])
    # colonnes interrogeables directement en SQL (ex. documents restreints aux responsables)
    rows = cat.db.query("SELECT document_id FROM documents WHERE groupes_acces NOT LIKE '%tech_san%' "
                        "AND groupes_acces NOT LIKE '%tech_ups%'")
    assert 'SAN-CAB-012' in {r['document_id'] for r in rows}
    figs = cat.db.query("SELECT COUNT(*) AS n FROM elements WHERE type = 'figure'")[0]['n']
    assert figs == sum(e['type'] == 'figure' for e in retriever.elements.values() if e['document_id'] in meta)
    # une seconde synchronisation remplace le contenu sans doublon
    cat.sync(meta, list(retriever.elements.values()), retriever.version)
    assert cat.count('documents') == len(meta)


def test_access_rights_are_read_from_the_catalog(retriever, tmp_path):
    cat = Catalog(str(tmp_path / 'catalog.db'))
    cat.sync(ingestion.load_metadata(), list(retriever.elements.values()), retriever.version)
    cat.db.run('UPDATE documents SET groupes_acces = ? WHERE document_id = ?',
               (json.dumps(['responsables']), 'SAN-ALM-002'))
    cat.db.commit()
    docs = cat.documents()
    assert docs['SAN-ALM-002']['groupes_acces'] == ['responsables']


def test_retriever_uses_catalog(retriever):
    assert retriever.catalog is not None and retriever.catalog.count('documents') == len(retriever.metadata)
    assert retriever.metadata == ingestion.load_metadata()
    assert 'SAN-CAB-012' not in retriever.allowed_documents(['tech_san'])


@pytest.mark.skipif(not os.getenv('MAINTIDOC_PG_TEST_URL'), reason='PostgreSQL de test non configuré')
def test_catalog_on_real_postgresql(retriever):
    cat = Catalog(os.environ['MAINTIDOC_PG_TEST_URL'])
    meta = ingestion.load_metadata()
    cat.sync(meta, list(retriever.elements.values()), retriever.version)
    assert cat.kind == 'postgresql' and cat.documents() == meta


def test_model_preloaded_at_api_startup(agent_factory, monkeypatch):
    from api import main
    from tests.conftest import FakeLLM

    class WarmLLM(FakeLLM):
        warmed = False

        def warm_up(self):
            WarmLLM.warmed = True

    monkeypatch.setenv('MAINTIDOC_WARMUP', '1')
    main.set_agent(agent_factory(WarmLLM()))
    with TestClient(main.app):  # déclenche le cycle de vie (démarrage) de l'API
        for _ in range(50):
            if WarmLLM.warmed:
                break
            time.sleep(0.1)
    assert WarmLLM.warmed


def test_time_budget_is_120_seconds_by_default():
    assert config.MAX_SECONDS == float(os.getenv('MAINTIDOC_MAX_SECONDS', 120))
