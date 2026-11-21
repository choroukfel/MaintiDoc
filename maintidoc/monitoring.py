"""Supervision (MLOps, point 7 du sujet : « surveiller erreurs, latences, consommation et refus d'accès »).

Compteurs en mémoire, exposés par l'API sur GET /metrics (JSON, ou format texte Prometheus avec ?format=prometheus).
Aucune donnée de contenu : uniquement des nombres et des durées.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import Counter, deque

from . import config


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 2)


class Metrics:
    def __init__(self, window: int = 1000):
        self._lock = threading.Lock()
        self.started = time.time()
        self.http = Counter()             # codes HTTP par route (« POST /ask 200 »)
        self.statuses = Counter()         # statuts des réponses (answered, needs_clarification, refused)
        self.events = Counter()           # refus d'accès, injections, garde-fous, tickets, erreurs…
        self.latencies: deque[float] = deque(maxlen=window)
        self.llm_calls = 0
        self.mcp_calls = 0
        self.compute_seconds = 0.0

    def record_http(self, method: str, route: str, status: int) -> None:
        with self._lock:
            self.http[f'{method} {route} {status}'] += 1
            if status in (401, 403):
                self.events['refus_acces_http'] += 1
            elif status == 429:
                self.events['quota_depasse'] += 1
            elif status >= 500:
                self.events['erreur_serveur'] += 1

    def record_answer(self, out: dict, seconds: float) -> None:
        res = out.get('result') or {}
        flags = out.get('flags') or {}
        with self._lock:
            self.statuses[res.get('status', 'inconnu')] += 1
            self.latencies.append(seconds)
            self.compute_seconds += seconds
            self.llm_calls += out.get('llm_calls') or 0
            self.mcp_calls += out.get('mcp_calls') or 0
            for u in res.get('uncertainties', []):
                if 'refusé' in u.lower():
                    self.events['refus_acces_agent'] += 1
                if 'garde-fou' in u.lower():
                    self.events['garde_fou_interdiction'] += 1
            for f in ('injection_question', 'injection_evidence', 'injection_mcp', 'injection_image'):
                if flags.get(f):
                    self.events[f] += 1
            if out.get('awaiting_approval'):
                self.events['brouillon_ticket'] += 1

    def record_ticket(self, status: str | None) -> None:
        """Décision humaine sur un brouillon : created, rejected ou failed."""
        if status:
            with self._lock:
                self.events[f'ticket_{status}'] += 1

    def record_error(self, kind: str) -> None:
        with self._lock:
            self.events[f'erreur_{kind}'] += 1

    def snapshot(self) -> dict:
        with self._lock:
            lat = list(self.latencies)
            n = sum(self.statuses.values())
            return {
                'version': config.APP_VERSION, 'disponible_depuis_s': round(time.time() - self.started),
                'requetes_traitees': n, 'statuts': dict(self.statuses), 'evenements': dict(self.events),
                'latence_s': {'mediane': round(statistics.median(lat), 2) if lat else None,
                              'p95': _percentile(lat, 0.95), 'max': round(max(lat), 2) if lat else None,
                              'fenetre': len(lat)},
                'consommation': {'calcul_total_s': round(self.compute_seconds, 1), 'appels_modele': self.llm_calls,
                                 'appels_mcp': self.mcp_calls},
                'http': dict(self.http),
            }

    def prometheus(self) -> str:
        s = self.snapshot()
        lines = [f'maintidoc_requests_total {s["requetes_traitees"]}',
                 f'maintidoc_compute_seconds_total {s["consommation"]["calcul_total_s"]}',
                 f'maintidoc_llm_calls_total {s["consommation"]["appels_modele"]}',
                 f'maintidoc_mcp_calls_total {s["consommation"]["appels_mcp"]}']
        lines += [f'maintidoc_answers_total{{status="{k}"}} {v}' for k, v in s['statuts'].items()]
        lines += [f'maintidoc_events_total{{event="{k}"}} {v}' for k, v in s['evenements'].items()]
        for q in ('mediane', 'p95'):
            if s['latence_s'][q] is not None:
                lines.append(f'maintidoc_latency_seconds{{quantile="{0.5 if q == "mediane" else 0.95}"}} {s["latence_s"][q]}')
        return '\n'.join(lines) + '\n'


metrics = Metrics()
