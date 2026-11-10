"""Synthèse des résultats d'évaluation -> eval/results/summary.json et docs/rapport_evaluation.md."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / 'eval' / 'results'
CAT_LABEL = {'texte': 'Questions documentaires textuelles', 'image': 'Questions avec image ou schéma',
             'mcp': 'Consultation MCP', 'absent_contradictoire': 'Sources absentes / contradictoires',
             'acces_validation': "Contrôle d'accès et validation humaine", 'injection': 'Injections de prompt'}


def pct(a, b):
    return f'{100 * a / b:.0f} %' if b else 'n/a'


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else None


def load_runs():
    f = RES / 'e2e_runs.jsonl'
    rows = {}
    if f.exists():
        for line in f.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            rows[r['id']] = r
    return list(rows.values())


def build_report():
    scen = {s['id']: s for s in json.loads((ROOT / 'eval' / 'scenarios.json').read_text(encoding='utf-8'))}
    runs = load_runs()
    retrieval = json.loads((RES / 'retrieval.json').read_text(encoding='utf-8')) if (RES / 'retrieval.json').exists() else None
    ft = json.loads((RES / 'finetuning.json').read_text(encoding='utf-8')) if (RES / 'finetuning.json').exists() else None
    by_cat = defaultdict(list)
    for r in runs:
        by_cat[r['category']].append(r)

    # ---------------------------------------------------------- indicateurs transverses
    answered = [r for r in runs if (r.get('result') or {}).get('status') == 'answered']
    cits = [c for r in answered for c in r['result']['citations']]
    valid = [ok for r in runs for name, ok in r['checks'] if name == 'citations_valides']
    gold_hit, gold_n = 0, 0
    from maintidoc.retrieval import Retriever  # noqa: F401  (résolution des éléments d'or)
    elements = json.loads((ROOT / 'data' / 'index' / 'elements.json').read_text(encoding='utf-8'))
    for r in answered:
        g = scen[r['id']]['expected'].get('gold')
        if not g or not r['result']['citations']:
            continue
        gold = set()
        for x in g:
            if isinstance(x, str):
                gold.add(x)
            else:
                gold |= {f"{e['document_id']}/{e['element_id']}" for e in elements
                         if e['document_id'] == x['doc'] and x['contains'].lower() in e['content'].lower()}
        cited = {f"{c['document_id']}/{c['element_id']}" for c in r['result']['citations']}
        gold_n += 1
        gold_hit += bool(cited & gold)
    sec = {
        'tickets_crees_sans_approbation': sum(1 for r in runs if (r.get('ticket') or {}).get('status') == 'created'
                                              and not scen[r['id']]['expected'].get('decision', {}).get('approved')),
        'fuites_documents_interdits': sum(1 for r in runs for n, ok in r['checks'] if n.startswith('interdit:') and not ok),
        'fuites_contenu_interdit': sum(1 for r in runs for n, ok in r['checks'] if n.startswith('absent:') and not ok),
        'injections_refusees_ou_neutralisees': sum(1 for r in by_cat['injection'] if r['passed']),
        'injections_total': len(by_cat['injection']),
    }
    lat = defaultdict(list)
    for r in runs:
        if scen[r['id']]['expected'].get('action'):
            continue
        kind = 'photo' if scen[r['id']].get('photo') else ('sans LLM' if not r.get('llm_calls') else 'texte')
        lat[kind].append(r['seconds'])
    summary = {
        'n_scenarios_executes': len(runs), 'n_scenarios_total': len(scen),
        'reussite_globale': pct(sum(r['passed'] for r in runs), len(runs)),
        'par_categorie': {c: {'n': len(v), 'reussis': sum(x['passed'] for x in v), 'taux': pct(sum(x['passed'] for x in v), len(v))}
                          for c, v in by_cat.items()},
        'citations': {'reponses_affirmatives': len(answered), 'citations': len(cits),
                      'validite_structurelle': pct(sum(valid), len(valid)),
                      'citation_pertinente_vs_or': pct(gold_hit, gold_n), 'n_evalue_vs_or': gold_n},
        'securite': sec,
        'latence_s': {k: {'n': len(v), 'mediane': round(statistics.median(v), 1), 'p95': p95(v)} for k, v in lat.items() if v},
        'appels_llm_moyens': round(statistics.mean([r['llm_calls'] or 0 for r in runs]), 2) if runs else None,
        'max_appels_mcp': max([r['mcp_calls'] or 0 for r in runs] or [0]),
        'max_appels_llm': max([r['llm_calls'] or 0 for r in runs] or [0]),
    }
    (RES / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')

    # ---------------------------------------------------------- rapport Markdown
    L = ['# MaintiDoc : rapport d\'évaluation', '',
         '*Généré automatiquement par `python eval/run_eval.py report` à partir de `eval/results/`.*', '',
         '## 1. Protocole', '',
         '- **Jeu de test** : 150 scénarios (`eval/scenarios.json`) répartis selon la section 10 du sujet.',
         '- **Matériel** : PC portable Intel Core i5-1145G7, 15 Go de RAM, **sans GPU**. Le modèle vision-langage '
         'Qwen2.5-VL 3B (quantifié, Ollama) tourne sur le CPU.',
         '- **Recherche** : embeddings `paraphrase-multilingual-MiniLM-L12-v2` (Qdrant embarqué), BM25, CLIP ViT-B/32, '
         'fusion RRF, reranker `mmarco-mMiniLMv2-L12` (origine puis adapté).',
         '- Chaque scénario repart d\'une base MCP identique ; un scénario est **réussi** si tous ses contrôles passent.', '']
    if retrieval:
        L += ['## 2. Recherche documentaire', '',
              'Les métriques portent sur les scénarios qui ont des éléments de référence (or), avec le filtrage des droits '
              'de l\'utilisateur appliqué.', '',
              '| Configuration | n | Recall@5 | nDCG@5 | MRR@10 | MRR@10 (documents de test) |', '|---|---|---|---|---|---|']
        for n, v in retrieval['configurations'].items():
            L.append(f"| {n} | {v['n']} | {v['recall@5']:.3f} | {v['ndcg@5']:.3f} | {v['mrr@10']:.3f} | "
                     f"{v['mrr@10 (docs test)'] if v['mrr@10 (docs test)'] is not None else 'n/a'} (n={v['n_docs_test']}) |")
        L += ['', '**Multimodalité (questions avec photo, sans nom de modèle dans la question)** :', '',
              '| Entrée | n | Recall@5 | nDCG@5 | MRR@10 |', '|---|---|---|---|---|']
        for n, v in retrieval['texte_vs_multimodal'].items():
            L.append(f"| {n} | {v['n']} | {v['recall@5']:.3f} | {v['ndcg@5']:.3f} | {v['mrr@10']:.3f} |")
        L.append('')
    if ft:
        L += ['## 3. Fine-tuning ciblé du reranker', '',
              f"- **Données** : {sum(ft['triplets'].values())} triplets (question, passage pertinent, négatif difficile) "
              f"générés à partir des tableaux : entraînement {ft['triplets']['train']}, validation {ft['triplets']['val']}, "
              f"test {ft['triplets']['test']}.",
              f"- **Séparation par document** : test = {', '.join(ft['split']['test_docs'])} ; validation = "
              f"{', '.join(ft['split']['val_docs'])} ; NOTE-EXT-099 exclu.",
              f"- **Entraînement** : {ft['training']['pairs']} paires, {ft['training']['steps']} pas, "
              f"{ft['training']['seconds']} s sur CPU.", '',
              '| MRR@10 (questions générées) | Validation | Test |', '|---|---|---|',
              f"| Reranker d'origine | {ft['mrr10_base']['val'][0]} | {ft['mrr10_base']['test'][0]} |",
              f"| Reranker adapté | {ft['mrr10_adapte']['val'][0]} | {ft['mrr10_adapte']['test'][0]} |", '',
              'Les questions générées sont proches du format d\'entraînement : le gain réel se lit plutôt sur les '
              'scénarios rédigés à la main (section 2, lignes « reranker origine » et « reranker adapté »).', '',
              "**Conclusion** : l'adaptation améliore nettement le classement sur des questions du même format que "
              "l'entraînement, y compris sur des documents jamais vus, mais ce gain **ne se transfère pas** aux questions "
              "rédigées à la main (MRR@10 équivalent, et inférieur sur les documents de test, n=18). Les questions "
              "d'entraînement, générées par gabarits, sont trop uniformes. Le reranker d'origine reste donc utilisé "
              "par défaut (`MAINTIDOC_RERANKER=base`). Piste : des questions d'entraînement plus variées (paraphrases "
              'rédigées ou générées par un LLM, puis vérifiées).', '']
    L += ['## 4. Évaluation de bout en bout (vrai modèle)', '',
          f"**{summary['n_scenarios_executes']} / {summary['n_scenarios_total']} scénarios exécutés, réussite globale "
          f"{summary['reussite_globale']}.**", '', '| Catégorie | Exécutés | Réussis | Taux |', '|---|---|---|---|']
    for c, lab in CAT_LABEL.items():
        v = summary['par_categorie'].get(c)
        if v:
            L.append(f"| {lab} | {v['n']} | {v['reussis']} | {v['taux']} |")
    c = summary['citations']
    L += ['', '### Indicateurs', '',
          f"- **Citations** : {c['citations']} citations dans {c['reponses_affirmatives']} réponses affirmatives ; validité "
          f"structurelle (la citation pointe vers une preuve réellement récupérée) : {c['validite_structurelle']} ; citation "
          f"d'au moins un élément de référence : {c['citation_pertinente_vs_or']} (sur {c['n_evalue_vs_or']} réponses avec or).",
          f"- **Sécurité** : tickets créés sans approbation : **{sec['tickets_crees_sans_approbation']}** ; documents "
          f"interdits apparus dans les preuves : **{sec['fuites_documents_interdits']}** ; contenus interdits dans une réponse : "
          f"**{sec['fuites_contenu_interdit']}** ; injections bloquées ou neutralisées : "
          f"{sec['injections_refusees_ou_neutralisees']} / {sec['injections_total']}.",
          f"- **Budgets** : au plus {summary['max_appels_llm']} appels au modèle et {summary['max_appels_mcp']} appels MCP "
          f"par demande (limites : 6 et 3) ; {summary['appels_llm_moyens']} appels au modèle en moyenne.",
          '- **Latence (CPU)** : ' + ' ; '.join(f"{k} : médiane {v['mediane']} s, p95 {v['p95']} s (n={v['n']})"
                                             for k, v in summary['latence_s'].items()) + '.', '',
          '### Échecs et analyse', '', '| Scénario | Catégorie | Contrôles en échec | Statut obtenu |', '|---|---|---|---|']
    for r in sorted(runs, key=lambda x: x['id']):
        if not r['passed']:
            fails = ', '.join(n for n, ok in r['checks'] if not ok).replace('|', ' ou ')
            L.append(f"| {r['id']} | {r['category']} | {fails} | {(r.get('result') or {}).get('status', '-')} |")
    analysis = ROOT / 'eval' / 'analyse_echecs.md'
    if analysis.exists():
        L += ['', analysis.read_text(encoding='utf-8').strip()]
    L += ['', '## 5. Limites', '',
          "- L'absence d'échec sur un contrôle ne constitue pas une garantie générale de sécurité.",
          '- Le petit modèle 3B, utilisé faute de GPU, lit imparfaitement les photos ; un modèle 7B sur GPU de 24 Go reste la cible.',
          '- La latence mesurée sur CPU dépasse largement l\'objectif de 30 s (p95) fixé pour le matériel cible.',
          "- La pertinence des citations est mesurée par rapport aux éléments de référence (or), sans juge humain systématique.", '']
    (ROOT / 'docs' / 'rapport_evaluation.md').write_text('\n'.join(L), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))
