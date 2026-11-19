"""Versionnement du reranker adapté (MLOps, point 4 du sujet : « versionner les modèles adaptés »).

- calcule l'empreinte SHA-256 des poids et des données d'entraînement ;
- enregistre le modèle (poids + fiche) comme artefact MLflow avec ses paramètres et métriques ;
- ajoute une entrée au registre versionné dans Git : eval/results/model_registry.json.

Les poids (466 Mo) restent hors de Git ; l'empreinte du registre permet de vérifier qu'un modèle restauré est
exactement celui qui a été évalué.  Usage : python eval/register_model.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maintidoc import config  # noqa: E402
from maintidoc.tracking import log_run  # noqa: E402

REGISTRY = ROOT / 'eval' / 'results' / 'model_registry.json'


def sha256_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        with open(p, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
    return h.hexdigest()


def main():
    model_dir = config.RERANKER_FINETUNED
    if not model_dir.exists():
        sys.exit('Modèle adapté absent : lancer d\'abord python eval/finetune_reranker.py')
    ft = json.loads((ROOT / 'eval' / 'results' / 'finetuning.json').read_text(encoding='utf-8'))
    retrieval = json.loads((ROOT / 'eval' / 'results' / 'retrieval.json').read_text(encoding='utf-8'))['configurations']
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    entry = {
        'nom': 'reranker-maintidoc',
        'version': f"ft-{time.strftime('%Y%m%d')}",
        'date': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'modele_de_base': ft['base_model'],
        'sha256_poids': sha256_files([p for p in model_dir.iterdir() if p.is_file() and p.name != 'MODEL_CARD.json']),
        'sha256_donnees_entrainement': sha256_files(sorted((ROOT / 'eval' / 'data').glob('reranker_*.jsonl'))),
        'commit_git': commit,
        'triplets': ft['triplets'],
        'separation': ft['split'],
        'hyperparametres': {'epochs': 2, 'learning_rate': 2e-5, 'batch': 16, 'max_length': 256},
        'mrr10_questions_generees': {'base': ft['mrr10_base'], 'adapte': ft['mrr10_adapte']},
        'mrr10_scenarios_rediges': {'base': retrieval['hybride + reranker origine']['mrr@10'],
                                    'adapte': retrieval['hybride + reranker adapté']['mrr@10']},
        'statut': 'évalué, non déployé (moins bon que le modèle d\'origine sur les scénarios rédigés)',
        'emplacement': str(model_dir.relative_to(ROOT)).replace('\\', '/'),
    }
    (model_dir / 'MODEL_CARD.json').write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding='utf-8')
    registry = json.loads(REGISTRY.read_text(encoding='utf-8')) if REGISTRY.exists() else []
    registry = [r for r in registry if r['sha256_poids'] != entry['sha256_poids']] + [entry]
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding='utf-8')
    ok = log_run('maintidoc-reranker', f"registre-{entry['version']}",
                 {'base': entry['modele_de_base'], 'sha256_poids': entry['sha256_poids'][:16], 'commit': commit,
                  'statut': entry['statut']},
                 {'mrr10_test_base': ft['mrr10_base']['test'][0], 'mrr10_test_adapte': ft['mrr10_adapte']['test'][0],
                  'mrr10_rediges_base': entry['mrr10_scenarios_rediges']['base'],
                  'mrr10_rediges_adapte': entry['mrr10_scenarios_rediges']['adapte']},
                 artifacts_dir=model_dir)
    print(json.dumps({k: entry[k] for k in ('nom', 'version', 'sha256_poids', 'commit_git', 'statut')},
                     ensure_ascii=False, indent=1), '| MLflow :', ok)


if __name__ == '__main__':
    main()
