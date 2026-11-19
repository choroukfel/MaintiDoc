"""Quotas par utilisateur (section 9 du sujet : « quotas par utilisateur et plafond de consommation »).

Trois limites, appliquées par l'API avant tout appel au modèle :
  * nombre de requêtes par heure glissante ;
  * temps de calcul consommé par période glissante de 24 h (plafond de consommation CPU/GPU) ;
  * nombre de requêtes simultanées.
Les compteurs sont en mémoire (un seul processus d'API dans la démonstration) ; en production, un stockage
partagé (Redis, PostgreSQL) serait nécessaire avec plusieurs instances.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

from . import config

HOUR, DAY = 3600.0, 86400.0


class QuotaExceeded(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class QuotaManager:
    def __init__(self, requests_per_hour: int | None = None, compute_seconds_per_day: float | None = None,
                 max_concurrent: int | None = None, clock=time.time):
        self.requests_per_hour = requests_per_hour if requests_per_hour is not None else config.QUOTA_REQUESTS_PER_HOUR
        self.compute_per_day = (compute_seconds_per_day if compute_seconds_per_day is not None
                                else config.QUOTA_COMPUTE_SECONDS_PER_DAY)
        self.max_concurrent = max_concurrent if max_concurrent is not None else config.QUOTA_MAX_CONCURRENT
        self.clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._compute: dict[str, deque[tuple[float, float]]] = defaultdict(deque)
        self._active: dict[str, int] = defaultdict(int)

    def _prune(self, user_id: str, now: float) -> None:
        req, comp = self._requests[user_id], self._compute[user_id]
        while req and req[0] <= now - HOUR:
            req.popleft()
        while comp and comp[0][0] <= now - DAY:
            comp.popleft()

    def acquire(self, user_id: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune(user_id, now)
            if self._active[user_id] >= self.max_concurrent:
                raise QuotaExceeded('Une requête est déjà en cours pour cet utilisateur.', 30)
            req = self._requests[user_id]
            if len(req) >= self.requests_per_hour:
                raise QuotaExceeded(f'Quota atteint : {self.requests_per_hour} requêtes par heure.',
                                    math.ceil(req[0] + HOUR - now))
            used = sum(s for _, s in self._compute[user_id])
            if used >= self.compute_per_day:
                oldest = self._compute[user_id][0][0]
                raise QuotaExceeded(f'Plafond de calcul atteint : {int(self.compute_per_day)} s par 24 h.',
                                    math.ceil(oldest + DAY - now))
            req.append(now)
            self._active[user_id] += 1

    def release(self, user_id: str, compute_seconds: float) -> None:
        with self._lock:
            self._active[user_id] = max(0, self._active[user_id] - 1)
            self._compute[user_id].append((self.clock(), max(0.0, compute_seconds)))

    @contextmanager
    def slot(self, user_id: str):
        """Réserve un créneau ; le temps passé dans le bloc est décompté du plafond de calcul."""
        self.acquire(user_id)
        start = time.perf_counter()
        try:
            yield
        finally:
            self.release(user_id, time.perf_counter() - start)

    def usage(self, user_id: str) -> dict:
        with self._lock:
            self._prune(user_id, self.clock())
            return {'requetes_derniere_heure': len(self._requests[user_id]),
                    'requetes_max_par_heure': self.requests_per_hour,
                    'calcul_24h_secondes': round(sum(s for _, s in self._compute[user_id]), 1),
                    'calcul_max_24h_secondes': self.compute_per_day,
                    'requetes_en_cours': self._active[user_id]}
