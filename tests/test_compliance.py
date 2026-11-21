"""Exigences ajoutées pour couvrir le sujet : images du corpus transmises au VLM, composants LangChain,
recherche texte -> image multilingue, supervision, pseudonymisation et chiffrement des journaux."""
import json

from fastapi.testclient import TestClient

from maintidoc import config, security
from tests.conftest import FakeLLM


def test_corpus_figure_image_sent_to_vlm(agent_factory, monkeypatch):
    monkeypatch.setattr(config, 'MAX_EVIDENCE_IMAGES', 1)  # option activable (désactivée par défaut avec le 3B)
    llm = FakeLLM()
    out = agent_factory(llm).ask('u-tech-b', "Façade de l'UPS-X900 : dans quel ordre sont les voyants de gauche à droite ?")
    figs = [e for e in out['evidence'] if e['type'] == 'figure']
    assert figs and llm.images_by_prompt.get('answer_v1') == 1
    gen = next(t for t in out['trace'] if t['node'] == 'generate')
    assert gen['stats'][0]['images_jointes']
    monkeypatch.setattr(config, 'MAX_EVIDENCE_IMAGES', 0)
    llm = FakeLLM()
    agent_factory(llm).ask('u-tech-b', "Façade de l'UPS-X900 : dans quel ordre sont les voyants de gauche à droite ?")
    assert llm.images_by_prompt.get('answer_v1') == 0


def test_langchain_retriever_returns_documents(retriever):
    from langchain_core.documents import Document

    from maintidoc.lc import MaintiDocRetriever
    docs = MaintiDocRetriever(retriever=retriever, groups=['tech_ups'], models=['UPS-X900']).invoke('code F-05')
    assert docs and all(isinstance(d, Document) for d in docs)
    assert all('UPS-X900' in retriever.metadata[d.metadata['document_id']]['modeles'] for d in docs)
    # droits appliqués avant la récupération : aucun document réservé aux techniciens onduleurs pour Tech-A
    san = MaintiDocRetriever(retriever=retriever, groups=['tech_san'], models=['UPS-X900']).invoke('code F-05')
    assert all('tech_san' in retriever.metadata[d.metadata['document_id']]['groupes_acces'] for d in san)


def test_langchain_output_parser_validates():
    import pytest

    from maintidoc.lc import parse_structured
    from maintidoc.schemas import LLMAnswer
    ok = parse_structured('{"status": "answered", "answer": "F-05 : surcharge.", "citations": ["E1"]}', LLMAnswer)
    assert ok.citations == ['E1']
    with pytest.raises(ValueError):
        parse_structured('{"status": "peut-être"}', LLMAnswer)


def test_multilingual_text_to_image_search(retriever):
    hits = retriever.visual_from_text("façade d'onduleur en rack avec bargraphe de charge", ['tech_ups'], None)
    assert hits and any(k.startswith('UPS-MAN-030') for k, _ in hits[:3])


def test_metrics_endpoint_restricted_and_counts(agent_factory):
    from api import main
    main.set_agent(agent_factory())
    client = TestClient(main.app)
    tok = lambda u: {'Authorization': f"Bearer {client.post('/auth/token', json={'user_id': u}).json()['access_token']}"}  # noqa: E731
    client.post('/ask', headers=tok('u-tech-b'), json={'question': "Que signifie F-05 sur l'UPS-X900 ?"})
    client.get('/documents/SAN-CAB-012/pages/1', headers=tok('u-tech-a'))  # refus d'accès
    assert client.get('/metrics', headers=tok('u-tech-a')).status_code == 403
    m = client.get('/metrics', headers=tok('u-resp-1')).json()
    assert m['requetes_traitees'] >= 1 and m['latence_s']['mediane'] is not None
    assert m['evenements'].get('refus_acces_http', 0) >= 1 and m['consommation']['appels_modele'] >= 1
    prom = client.get('/metrics?format=prometheus', headers=tok('u-resp-1')).text
    assert 'maintidoc_requests_total' in prom and 'quantile="0.95"' in prom


def test_logs_pseudonymized(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'LOG_DIR', tmp_path)
    monkeypatch.setattr(config, 'LOG_KEY', None)
    security.log_event({'request_id': 'r1', 'user_id': 'u-tech-a', 'status': 'answered'})
    entry = json.loads((tmp_path / 'requests.jsonl').read_text(encoding='utf-8'))
    assert entry['user_id'] != 'u-tech-a' and entry['user_id'].startswith('u_')
    assert entry['user_id'] == security.pseudonymize('u-tech-a')  # stable : permet de compter l'activité


def test_logs_encrypted_when_key_set(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(config, 'LOG_DIR', tmp_path)
    monkeypatch.setattr(config, 'LOG_KEY', Fernet.generate_key().decode())
    security.log_event({'request_id': 'r2', 'user_id': 'u-tech-b', 'status': 'refused'})
    raw = (tmp_path / 'requests.jsonl').read_text(encoding='utf-8')
    assert raw.startswith('ENC:') and 'refused' not in raw
    assert security.read_logs()[0]['status'] == 'refused'
    assert security.purge_logs(retention_days=30) == 0  # la purge sait lire les lignes chiffrées
