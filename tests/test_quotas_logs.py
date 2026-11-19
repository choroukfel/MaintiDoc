"""Quotas par utilisateur et durée de conservation des journaux."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from maintidoc import config, security
from maintidoc.quotas import QuotaExceeded, QuotaManager


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_requests_per_hour_limit_and_reset():
    clock = Clock()
    q = QuotaManager(requests_per_hour=2, compute_seconds_per_day=1e9, max_concurrent=5, clock=clock)
    for _ in range(2):
        q.acquire('u'); q.release('u', 1)
    with pytest.raises(QuotaExceeded) as ex:
        q.acquire('u')
    assert 0 < ex.value.retry_after <= 3600
    q.acquire('autre')  # les quotas sont propres à chaque utilisateur
    clock.t += 3601
    q.acquire('u')  # fenêtre glissante : de nouveau autorisé


def test_compute_ceiling_per_day():
    clock = Clock()
    q = QuotaManager(requests_per_hour=100, compute_seconds_per_day=100, max_concurrent=5, clock=clock)
    q.acquire('u'); q.release('u', 120)
    with pytest.raises(QuotaExceeded, match='calcul'):
        q.acquire('u')
    clock.t += 86401
    q.acquire('u')
    assert q.usage('u')['requetes_en_cours'] == 1


def test_one_concurrent_request_per_user():
    q = QuotaManager(requests_per_hour=100, compute_seconds_per_day=1e9, max_concurrent=1)
    q.acquire('u')
    with pytest.raises(QuotaExceeded, match='en cours'):
        q.acquire('u')
    q.release('u', 0)
    q.acquire('u')


def test_api_returns_429_with_retry_after(agent_factory):
    from api import main
    main.set_agent(agent_factory())
    main.set_quotas(QuotaManager(requests_per_hour=1, compute_seconds_per_day=1e9, max_concurrent=1))
    try:
        client = TestClient(main.app)
        tok = client.post('/auth/token', json={'user_id': 'u-tech-b'}).json()['access_token']
        h = {'Authorization': f'Bearer {tok}'}
        assert client.post('/ask', headers=h, json={'question': "Que signifie F-05 sur l'UPS-X900 ?"}).status_code == 200
        r = client.post('/ask', headers=h, json={'question': "Que signifie F-02 sur l'UPS-X900 ?"})
        assert r.status_code == 429 and int(r.headers['Retry-After']) > 0
        usage = client.get('/quota', headers=h).json()
        assert usage['requetes_derniere_heure'] == 1 and usage['calcul_24h_secondes'] > 0
    finally:
        main.set_quotas(QuotaManager())


def test_log_retention_purges_old_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'LOG_DIR', tmp_path)
    old = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - 40 * 86400))
    recent = time.strftime('%Y-%m-%dT%H:%M:%S')
    (tmp_path / 'requests.jsonl').write_text(
        json.dumps({'ts': old, 'status': 'answered'}) + '\n' + json.dumps({'ts': recent, 'status': 'refused'}) + '\n'
        + 'ligne illisible\n', encoding='utf-8')
    assert security.purge_logs(retention_days=30) == 2
    lines = (tmp_path / 'requests.jsonl').read_text(encoding='utf-8').splitlines()
    assert [json.loads(line)['ts'] for line in lines] == [recent]


def test_logs_never_contain_raw_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'LOG_DIR', tmp_path)
    security.log_event({'request_id': 'r1', 'question': 'secret métier', 'answer': 'réponse', 'status': 'answered'})
    text = (tmp_path / 'requests.jsonl').read_text(encoding='utf-8')
    assert 'secret métier' not in text and 'réponse' not in text and '"status": "answered"' in text
