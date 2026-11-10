from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maintidoc.schemas import LLMAnswer, LLMTicket, VisualAnalysis  # noqa: E402
from mcp_server.store import AccessDenied, MaintenanceStore  # noqa: E402


class FakeLLM:
    """LLM déterministe pour les tests : cite E1 et recopie le début de la preuve."""
    model = 'fake-llm'

    def __init__(self, visual: dict | None = None, answer: dict | None = None, fail: bool = False):
        self.visual = visual or {'readable': True, 'equipment_model': None, 'screen_text': None, 'lit_indicators': []}
        self.answer = answer
        self.fail = fail
        self.calls: list[str] = []

    def structured(self, prompt, variables, schema, images=None, num_predict=None):
        self.calls.append(prompt)
        if self.fail:
            raise ValueError('sortie invalide simulée')
        if schema is VisualAnalysis:
            return VisualAnalysis(**self.visual), {}
        if schema is LLMAnswer:
            if self.answer:
                return LLMAnswer(**self.answer), {}
            first = variables['evidence'].split('\n', 2)
            snippet = first[1][:200] if len(first) > 1 else ''
            return LLMAnswer(status='answered', answer=f'Selon la documentation : {snippet}', citations=['E1']), {}
        if schema is LLMTicket:
            return LLMTicket(title='Ventilateur FAN-3 dégradé (AL-31)', description='Constat AL-31 sur le module '
                             'FAN-3, voir SAN-MAN-010 TAB-02.', priority='moyenne'), {}
        raise AssertionError(schema)


def make_mcp(db_path: Path):
    store = MaintenanceStore(db_path)

    def call(calls):
        out = []
        for name, args in calls:
            fn = {'get_equipment_history': store.history, 'get_open_tickets': store.open_tickets,
                  'create_ticket': store.create_ticket}[name]
            try:
                out.append({'ok': True, 'data': fn(**args)})
            except AccessDenied as ex:
                out.append({'ok': False, 'error': 'access_denied', 'message': str(ex)})
        return out
    call.store = store
    return call


@pytest.fixture(scope='session')
def retriever():
    from maintidoc.retrieval import get_retriever
    return get_retriever()


@pytest.fixture
def mcp(tmp_path):
    return make_mcp(tmp_path / 'maintenance.db')


@pytest.fixture
def agent_factory(retriever, mcp):
    from maintidoc.agent import Agent

    def make(llm=None):
        return Agent(retriever, llm or FakeLLM(), mcp_call=mcp)
    return make


def photo(name: str) -> bytes:
    return (ROOT / 'eval' / 'photos' / name).read_bytes()


def load_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding='utf-8'))
