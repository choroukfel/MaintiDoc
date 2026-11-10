"""Détection déterministe des voyants allumés (traitement d'image classique, sans modèle).

Complète la lecture du VLM : repère les zones très lumineuses et saturées, les regroupe en taches
et les classe par teinte. Rapide (quelques millisecondes) et indépendant du modèle de langage.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# plages de teinte (0-360°) des couleurs de voyants
HUES = [('rouge', 0, 12), ('orange', 12, 42), ('jaune', 42, 70), ('vert', 70, 170), ('bleu', 170, 260),
        ('rouge', 330, 361)]


def detect_lit_leds(im: Image.Image, max_side: int = 400) -> dict[str, int]:
    """Retourne {couleur: nombre de taches} pour les voyants allumés détectés."""
    im = im.convert('RGB')
    im.thumbnail((max_side, max_side))
    hsv = np.asarray(im.convert('HSV'), dtype=np.float32)
    h, s, v = hsv[..., 0] * 360 / 255, hsv[..., 1], hsv[..., 2]
    mask = (v > 170) & (s > 110)
    counts: dict[str, int] = {}
    if not mask.any():
        return counts
    from collections import deque
    seen = np.zeros_like(mask)
    H, W = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        q, pix = deque([(y, x)]), []
        seen[y, x] = True
        while q:
            cy, cx = q.popleft()
            pix.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        n = len(pix)
        if n < 4 or n > 0.02 * H * W:  # bruit, ou grande surface colorée (étiquette, bargraphe)
            continue
        ys, xs = zip(*pix)
        if (max(ys) - min(ys) + 1) > 3 * (max(xs) - min(xs) + 1) or (max(xs) - min(xs) + 1) > 3 * (max(ys) - min(ys) + 1):
            continue  # forme allongée : pas un voyant rond
        hue = float(np.median([h[p] for p in pix]))
        for name, lo, hi in HUES:
            if lo <= hue < hi:
                counts[name] = counts.get(name, 0) + 1
                break
    return counts


def describe(counts: dict[str, int]) -> str:
    if not counts:
        return 'aucun voyant allumé détecté'
    alerts = {c: n for c, n in counts.items() if c != 'vert'}
    parts = [f'{n} {c}' for c, n in sorted(counts.items())]
    txt = 'voyants allumés détectés : ' + ', '.join(parts)
    if alerts:
        txt += f" (voyant(s) d'alerte non vert(s) : {', '.join(alerts)})"
    return txt
