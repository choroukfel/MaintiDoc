"""OCR local (EasyOCR, français + anglais, CPU).

Utilisé pour : les pages scannées (sans couche texte), le texte des figures du corpus et les photos envoyées par
l'utilisateur. Le texte lu est une DONNÉE : il passe par les mêmes filtres anti-injection que les documents.
Désactivable avec MAINTIDOC_OCR=0 (tests rapides, CI).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading

import numpy as np
from PIL import Image

from . import config

ENABLED = os.getenv('MAINTIDOC_OCR', '1') != '0'
_reader = None
_lock = threading.Lock()
_CACHE_FILE = config.INDEX_DIR / 'ocr_cache.json'
_cache: dict[str, str] | None = None


def reader():
    global _reader
    with _lock:
        if _reader is None:
            import easyocr
            _reader = easyocr.Reader(['fr', 'en'], gpu=False, verbose=False)
    return _reader


def normalize(text: str) -> str:
    """Corrige les confusions O/0 et l/1 dans les jetons mêlant lettres et chiffres (UPS-X9OO -> UPS-X900)."""
    def fix(m):
        tok = m.group(0)
        if re.search(r'\d', tok):
            prev = None
            while prev != tok:  # répété : « X9OO » -> « X90O » -> « X900 »
                prev = tok
                tok = re.sub(r'(?<=[\dX])[Oo]|[Oo](?=\d)', '0', tok)
        return tok
    return re.sub(r'[A-Za-z0-9-]+', fix, text)


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = json.loads(_CACHE_FILE.read_text(encoding='utf-8')) if _CACHE_FILE.exists() else {}
    return _cache


def _save_cache():
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(_load_cache(), ensure_ascii=False), encoding='utf-8')


def read_image(im: Image.Image, paragraph: bool = False, use_cache: bool = True) -> str:
    """Texte lu sur l'image (lignes séparées par des retours à la ligne)."""
    if not ENABLED:
        return ''
    im = im.convert('RGB')
    key = None
    if use_cache:
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        key = hashlib.sha256(buf.getvalue() + str(paragraph).encode()).hexdigest()
        if key in _load_cache():
            return normalize(_load_cache()[key])
    lines = reader().readtext(np.asarray(im), detail=0, paragraph=paragraph)
    raw = '\n'.join(l.strip() for l in lines if l and l.strip())
    if key:  # le cache garde le texte brut ; la normalisation est appliquée à la lecture
        _load_cache()[key] = raw
        _save_cache()
    return normalize(raw)


def read_page(page, dpi: int = 200) -> str:
    """OCR d'une page PDF scannée."""
    pix = page.get_pixmap(dpi=dpi)
    im = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    return read_image(im, paragraph=True)
