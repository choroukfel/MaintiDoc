"""Garde-fous : injections de prompt, demandes à risque, jetons d'approbation, journalisation minimale."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

from . import config

INJECTION_PATTERNS = [
    r'ignor\w*\s+(?:les|toutes\s+les|tes|vos|mes)?\s*(?:instructions|r[èe]gles|consignes)',
    r'ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions',
    r'(?:envoie|transmets|exporte|send)\w*\s+.{0,60}(?:vers|[àa]\s+l\'adresse|to)\s',
    r'sans\s+attendre\s+(?:d\'|l\')?\s*approbation',
    r'sans\s+(?:validation|approbation)\s+humaine',
    r'(?:ignorer|contourner|d[ée]sactiver)\s+les\s+r[èe]gles',
    r'(?:se\s+d[ée]clarer|je\s+suis)\s+administrateur',
    r'approuve\w*\s+automatiquement',
    r'(?:affiche|r[ée]v[èe]le|montre)\w*\s+(?:ton|le|votre)\s+prompt',
    r'[\w.+-]+@[\w-]+\.[\w.]+',
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

RISKY_PATTERNS = [
    (r'failover|basculement\s+forc', 'déclencher un basculement'),
    (r'supprim\w*\s+(?:la|le|les|une|un)?\s*(?:lun|volume)', 'supprimer un volume'),
    (r'modifi\w*\s+(?:le\s+)?zoning|changer\s+le\s+zoning', 'modifier le zoning'),
    (r'r[ée]arm\w*', 'réarmer un équipement'),
    (r'(?:d[ée]sactiv|couper|shunt|contourn)\w*\s+(?:l\'|les?\s+)?(?:alarme|protection|s[ée]curit)', 'désactiver une protection'),
    (r'(?:sous\s+tension|à\s+chaud).{0,40}(?:ouvr|d[ée]mont)|(?:ouvr|d[ée]mont)\w*.{0,40}sous\s+tension', 'intervenir sous tension'),
    (r'retir\w*\s+(?:deux|plusieurs|tous\s+les)\s+disques', 'retirer plusieurs disques'),
    (r'(?:pilote|ex[ée]cute|lance)\w*\s+(?:une\s+)?commande', 'exécuter une commande sur un équipement'),
]
_RISKY = [(re.compile(p, re.I), label) for p, label in RISKY_PATTERNS]


def detect_injection(text: str) -> list[str]:
    return [m.group(0) for p in _INJ for m in [p.search(text or '')] if m]


def sanitize(text: str) -> tuple[str, bool]:
    """Remplace les phrases contenant une tentative d'injection. Retourne (texte, suspect)."""
    sentences = re.split(r'(?<=[.!?\n])\s+', text)
    out, flagged = [], False
    for s in sentences:
        if detect_injection(s):
            out.append('[contenu suspect retiré : tentative d\'instruction dans une donnée]')
            flagged = True
        else:
            out.append(s)
    return ' '.join(out), flagged


_ACTION_REQUEST = re.compile(
    r'\b(comment|proc[ée]dure\s+pour|[ée]tapes\s+pour|aide[- ]moi|explique[- ]moi\s+comment|peux[- ]tu|pouvez[- ]vous|'
    r'fais|lance|d[ée]clenche|force|je\s+veux|donne[- ]moi)\b', re.I)


def risky_request(question: str) -> str | None:
    """Demande d'aide à une action dangereuse ou hors périmètre (pas une simple question documentaire)."""
    if not _ACTION_REQUEST.search(question or ''):
        return None
    for pat, label in _RISKY:
        if pat.search(question or ''):
            return label
    return None


# ------------------------------------------------------------------ approbation des tickets
def content_hash(equipment_id: str, title: str, description: str, priority: str) -> str:
    payload = json.dumps([equipment_id, title, description, priority], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def idempotency_key(request_id: str, equipment_id: str, chash: str) -> str:
    return hashlib.sha256(f'{request_id}|{equipment_id}|{chash}'.encode()).hexdigest()[:32]


def approval_token(approver_id: str, equipment_id: str, chash: str, idem: str,
                   secret: str | None = None) -> str:
    msg = f'{approver_id}|{equipment_id}|{chash}|{idem}'.encode()
    return hmac.new((secret or config.APPROVAL_SECRET).encode(), msg, hashlib.sha256).hexdigest()


def verify_approval(token: str, approver_id: str, equipment_id: str, chash: str, idem: str,
                    secret: str | None = None) -> bool:
    expected = approval_token(approver_id, equipment_id, chash, idem, secret)
    return hmac.compare_digest(expected, token or '')


# ------------------------------------------------------------------ journalisation (métadonnées uniquement)
def log_event(event: dict) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in event.items() if k not in {'question', 'answer', 'image', 'description', 'evidence'}}
    safe['ts'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with open(config.LOG_DIR / 'requests.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(safe, ensure_ascii=False) + '\n')
