"""Calibration hors ligne du juge de fidélité (evidence_check_v2) sur les réponses déjà obtenues.

Rejoue uniquement l'étape de vérification sur les réponses affirmatives de eval/results/e2e_runs.jsonl et mesure :
  * les réponses correctes (scénario réussi) que le juge bloquerait à tort (faux positifs) ;
  * les réponses fautives qu'il bloque (dont TEX-10, l'affirmation contraire à la source).
Résultat : eval/results/faithfulness_calibration.json
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maintidoc import config, security  # noqa: E402
from maintidoc.llm import OllamaVLM  # noqa: E402
from maintidoc.schemas import FaithfulnessCheck  # noqa: E402


def main():
    scen = {s['id']: s for s in json.loads((ROOT / 'eval' / 'scenarios.json').read_text(encoding='utf-8'))}
    elements = {e['key']: e for e in json.loads((config.INDEX_DIR / 'elements.json').read_text(encoding='utf-8'))}
    runs = [json.loads(l) for l in (ROOT / 'eval' / 'results' / 'e2e_runs.jsonl').read_text(encoding='utf-8').splitlines()]
    llm, rows = OllamaVLM(), []
    todo = [r for r in runs if (r.get('result') or {}).get('status') == 'answered' and r.get('evidence')]
    print(len(todo), 'réponses affirmatives à vérifier', flush=True)
    for r in todo:
        ev = '\n\n'.join(f"[{k}] {security.sanitize(elements[k]['content'][:config.EVIDENCE_MAX_CHARS])[0]}"
                         for k in r['evidence'] if k in elements)[:3000]
        t = time.time()
        try:
            chk, _ = llm.structured(config.PROMPT_VERSIONS['evidence_check'],
                                    {'question': scen[r['id']]['question'], 'answer': r['result']['answer'],
                                     'evidence': ev}, FaithfulnessCheck, num_predict=120)
            verdict, raison = chk.verdict, chk.raison
        except ValueError as ex:
            verdict, raison = 'erreur', str(ex)[:100]
        rows.append({'id': r['id'], 'reussi': r['passed'], 'verdict': verdict, 'raison': raison,
                     'secondes': round(time.time() - t, 1)})
        print(f"{r['id']:8s} {'OK' if r['passed'] else 'KO'} -> {verdict:12s} {raison[:90]}", flush=True)
    ok = [x for x in rows if x['reussi']]
    ko = [x for x in rows if not x['reussi']]
    summary = {
        'n': len(rows), 'verdicts': Counter(x['verdict'] for x in rows),
        'faux_positifs_bloques': [x['id'] for x in ok if x['verdict'] == 'contredite'],
        'taux_faux_positifs': round(sum(x['verdict'] == 'contredite' for x in ok) / max(1, len(ok)), 3),
        'echecs_bloques': [x['id'] for x in ko if x['verdict'] == 'contredite'],
        'TEX-10': next((x for x in rows if x['id'] == 'TEX-10'), None),
        'secondes_moyennes': round(sum(x['secondes'] for x in rows) / max(1, len(rows)), 1),
        'details': rows,
    }
    (ROOT / 'eval' / 'results' / 'faithfulness_calibration.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'details'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
