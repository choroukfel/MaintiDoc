"""API FastAPI de MaintiDoc : authentification (JWT), autorisation, questions, approbation des tickets.

Lancement : uvicorn api.main:app --port 8000
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from maintidoc import config, security  # noqa: E402
from maintidoc.agent import USERS, Agent  # noqa: E402
from maintidoc.monitoring import metrics  # noqa: E402
from maintidoc.quotas import QuotaExceeded, QuotaManager  # noqa: E402

log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app):
    security.purge_logs()  # durée de conservation des journaux (MAINTIDOC_LOG_RETENTION_DAYS)
    if os.getenv('MAINTIDOC_WARMUP', '1') == '1':
        threading.Thread(target=_warm_up, daemon=True).start()
    yield


def _warm_up() -> None:
    """Préchargement en arrière-plan : index, modèles de recherche et modèle vision-langage."""
    try:
        llm = get_agent().llm
        if hasattr(llm, 'warm_up'):
            llm.warm_up()
    except Exception as ex:  # Ollama absent au démarrage : la première demande chargera le modèle
        log.warning('Préchargement du modèle impossible : %s', ex)


app = FastAPI(title='MaintiDoc API', version='1.0', lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


@app.middleware('http')
async def _count_http(request: Request, call_next):
    """Supervision : compte chaque réponse HTTP par route et code (refus d'accès, quotas, erreurs)."""
    try:
        response = await call_next(request)
    except Exception:
        metrics.record_http(request.method, _route(request), 500)
        raise
    metrics.record_http(request.method, _route(request), response.status_code)
    return response


def _route(request: Request) -> str:
    r = request.scope.get('route')
    return getattr(r, 'path', None) or request.url.path
_agent: Agent | None = None
quotas = QuotaManager()


def set_quotas(manager: QuotaManager) -> None:  # utilisé par les tests
    global quotas
    quotas = manager


_agent_lock = threading.Lock()


def get_agent() -> Agent:
    global _agent
    with _agent_lock:  # le préchargement et une première requête peuvent arriver en même temps
        if _agent is None:
            from maintidoc.llm import OllamaVLM
            from maintidoc.retrieval import get_retriever
            _agent = Agent(get_retriever(), OllamaVLM())
    return _agent


def set_agent(agent: Agent) -> None:  # utilisé par les tests
    global _agent
    _agent = agent


# ------------------------------------------------------------------ authentification (démonstration)
class LoginRequest(BaseModel):
    user_id: str


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if cred is None:
        raise HTTPException(401, 'Authentification requise')
    try:
        claims = jwt.decode(cred.credentials, config.JWT_SECRET, algorithms=['HS256'])
    except jwt.PyJWTError:
        raise HTTPException(401, 'Jeton invalide ou expiré')
    user = USERS.get(claims.get('sub'))
    if user is None:
        raise HTTPException(401, 'Utilisateur inconnu')
    return user


@app.post('/auth/token')
def login(req: LoginRequest):
    """Démonstration : connexion par identifiant fictif, sans mot de passe (à remplacer par un IdP en production)."""
    user = USERS.get(req.user_id)
    if user is None:
        raise HTTPException(401, 'Utilisateur inconnu')
    token = jwt.encode({'sub': user['user_id'], 'role': user['role'], 'exp': int(time.time()) + 8 * 3600},
                       config.JWT_SECRET, algorithm='HS256')
    return {'access_token': token, 'token_type': 'bearer', 'user': user}


# ------------------------------------------------------------------ questions
class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=config.MAX_QUESTION_CHARS)
    image_base64: str | None = None


class DecisionRequest(BaseModel):
    approved: bool
    draft: dict | None = None


@app.get('/health')
def health():
    return {'status': 'ok', 'version': config.APP_VERSION}


@app.post('/ask')
def ask(req: AskRequest, user: dict = Depends(current_user)):
    image = None
    if req.image_base64:
        try:
            image = base64.b64decode(req.image_base64, validate=True)
        except Exception:
            raise HTTPException(400, 'Image base64 invalide')
        if len(image) > config.MAX_IMAGE_BYTES:
            raise HTTPException(413, 'Image trop volumineuse')
    try:
        with quotas.slot(user['user_id']):
            t0 = time.perf_counter()
            out = get_agent().ask(user['user_id'], req.question, image)
            metrics.record_answer(out, time.perf_counter() - t0)
            return out
    except QuotaExceeded as ex:
        raise HTTPException(429, str(ex), headers={'Retry-After': str(ex.retry_after)})
    except ValueError as ex:
        metrics.record_error('requete_invalide')
        raise HTTPException(400, str(ex))


@app.get('/quota')
def my_quota(user: dict = Depends(current_user)):
    return quotas.usage(user['user_id'])


@app.get('/metrics')
def get_metrics(format: str = 'json', user: dict = Depends(current_user)):
    """Supervision : réservée aux responsables (données agrégées, sans contenu)."""
    if user['role'] != 'responsable':
        raise HTTPException(403, 'Supervision réservée aux responsables')
    if format == 'prometheus':
        return PlainTextResponse(metrics.prometheus())
    return metrics.snapshot()


@app.post('/tickets/{thread_id}/decision')
def decide(thread_id: str, req: DecisionRequest, user: dict = Depends(current_user)):
    try:
        out = get_agent().decide(thread_id, user['user_id'], req.approved, req.draft)
        metrics.record_ticket((out.get('ticket') or {}).get('status'))
        return out
    except PermissionError as ex:
        raise HTTPException(403, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


# ------------------------------------------------------------------ documents (contrôle d'accès à l'affichage)
@app.get('/documents')
def documents(user: dict = Depends(current_user)):
    r = get_agent().retriever
    allowed = r.allowed_documents(user['groupes'])
    return [{k: m[k] for k in ('document_id', 'titre', 'version', 'modeles', 'niveau_confiance')}
            for d, m in r.metadata.items() if d in allowed]


@app.get('/documents/{document_id}/pages/{page}')
def page_image(document_id: str, page: int, user: dict = Depends(current_user)):
    r = get_agent().retriever
    if document_id not in r.metadata:
        raise HTTPException(404, 'Document inconnu')
    if document_id not in r.allowed_documents(user['groupes']):
        raise HTTPException(403, 'Accès refusé à ce document')
    try:
        return Response(r.render_page(document_id, page), media_type='image/png')
    except IndexError:
        raise HTTPException(404, 'Page inexistante')
