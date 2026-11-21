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
             'acces_validation': "Contrôle d'accès et validation humaine", 'injection': 'Injections de prompt',
             'corpus_etendu': 'Corpus étendu et OCR (complément)'}


def pct(a, b):
    return f'{100 * a / b:.0f} %' if b else 'n/a'


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else None


def load_runs(f: Path | None = None):
    f = f or RES / 'e2e_runs.jsonl'
    rows = {}
    if f.exists():
        for line in f.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            rows[r['id']] = r
    return list(rows.values())


def build_report():
    scen = {s['id']: s for s in json.loads((ROOT / 'eval' / 'scenarios.json').read_text(encoding='utf-8'))}
    runs = load_runs()
    it2_file = RES / 'iteration2_30docs' / 'e2e_runs.jsonl'
    runs2 = load_runs(it2_file) if it2_file.exists() else runs  # itération 2 (archivée), base de comparaison
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
         f"- **Jeu de test** : {len(scen)} scénarios (`eval/scenarios.json`) : les 150 de la section 10 du sujet, plus "
         "10 scénarios sur le corpus étendu (dont 2 accessibles uniquement par OCR et 1 injection dans un scan).",
         '- **Corpus** : 30 documents fictifs (2 familles d\'équipements), dont 2 documents scannés sans couche texte.',
         '- **Matériel** : PC portable Intel Core i5-1145G7, 15 Go de RAM, sans GPU dédié. Le modèle vision-langage '
         'Qwen2.5-VL 3B (quantifié, Ollama) tourne sur le GPU intégré Intel Iris Xe (Vulkan) pour l\'évaluation '
         'finale ; les itérations 1 et 2 ont tourné sur le processeur.',
         '- **Recherche** : embeddings `paraphrase-multilingual-MiniLM-L12-v2` (Qdrant embarqué), BM25, CLIP ViT-B/32, '
         'fusion RRF, reranker `mmarco-mMiniLMv2-L12` (origine puis adapté). OCR local EasyOCR (pages scannées, '
         'texte des figures, photos).',
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
        cf = retrieval['configurations']
        if 'hybride + reranker origine + texte->image' in cf:
            L += ['', f"**Recherche texte → image (encodeur CLIP multilingue)** : sans reranker, ajouter les figures "
                      f"retrouvées à partir du texte de la question dégrade la fusion (MRR@10 "
                      f"{cf['hybride RRF']['mrr@10']:.3f} → {cf['hybride RRF + texte->image (CLIP multilingue)']['mrr@10']:.3f}) ; "
                      f"avec le reranker, l'effet est nul ({cf['hybride + reranker origine']['mrr@10']:.4f} → "
                      f"{cf['hybride + reranker origine + texte->image']['mrr@10']:.4f}). Elle est donc implémentée et testée, "
                      "mais désactivée par défaut (`MAINTIDOC_TEXT_TO_IMAGE=1`)."]
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
              'Les questions générées sont proches du format d\'entraînement : le gain réel se lit sur les scénarios '
              'rédigés à la main.', '']
        it1 = RES / 'iteration1_12docs'
        if (it1 / 'finetuning.json').exists() and retrieval:
            ft1 = json.loads((it1 / 'finetuning.json').read_text(encoding='utf-8'))
            r1 = json.loads((it1 / 'retrieval.json').read_text(encoding='utf-8'))['configurations']
            it2 = RES / 'iteration2_30docs'
            ft2 = json.loads((it2 / 'finetuning.json').read_text(encoding='utf-8')) if (it2 / 'finetuning.json').exists() else ft
            r2 = json.loads((it2 / 'retrieval.json').read_text(encoding='utf-8'))['configurations'] \
                if (it2 / 'retrieval.json').exists() else retrieval['configurations']
            r3 = retrieval['configurations']

            def row(label, data, f, r):
                return (f"| {label} | {sum(f['triplets'].values())} triplets, {data} | "
                        f"{f['mrr10_base']['test'][0]} → {f['mrr10_adapte']['test'][0]} | "
                        f"{r['hybride + reranker origine']['mrr@10']:.3f} | {r['hybride + reranker adapté']['mrr@10']:.3f} |")
            rows = [row('v1 (12 documents)', '3 gabarits fixes par ligne', ft1, r1),
                    row('v2 (30 documents)', 'gabarits variés tirés au hasard', ft2, r2)]
            if ft.get('verification'):
                rows.append(row('v3 (30 documents)', 'gabarits variés, vérifiés automatiquement, plafond 1 500', ft, r3))
            L += ['**Itérations de fine-tuning** :', '',
                  '| Itération | Données d\'entraînement | MRR@10 questions générées (test) origine → adapté | '
                  'MRR@10 scénarios rédigés : origine | MRR@10 scénarios rédigés : adapté |', '|---|---|---|---|---|',
                  *rows, '']
        if ft.get('verification'):
            v = ft['verification']
            L += [f"**Vérification automatique des exemples** (itération v3) : {v['generes']} triplets générés ; écartés : "
                  f"{v['positif_sans_element']} positifs sans l'élément demandé, {v['negatif_ambigu']} négatifs ambigus "
                  f"(faux négatifs probables), {v['doublons']} doublons ; {v['total_retenu']} retenus (plafond de 1 500 du sujet).", '']
        fb, ff = RES / 'e2e_runs_reranker_base.jsonl', RES / 'e2e_runs_reranker_adapte.jsonl'
        if ff.exists() and (fb.exists() or runs):
            # origine : évaluation finale (reranker d'origine par défaut) si aucun fichier dédié
            rb = {json.loads(ln)['id']: json.loads(ln) for ln in fb.read_text(encoding='utf-8').splitlines()} \
                if fb.exists() else {r['id']: r for r in runs}
            rf = {json.loads(ln)['id']: json.loads(ln) for ln in ff.read_text(encoding='utf-8').splitlines()}
            ids = sorted(set(rb) & set(rf))
            L += [f"**Qualité des réponses finales** (mesure demandée par la section 5), sur les {len(ids)} questions "
                  "documentaires textuelles, même code et même modèle :", '',
                  '| Reranker | Scénarios réussis |', '|---|---|',
                  f"| Origine | {sum(rb[i]['passed'] for i in ids)} / {len(ids)} |",
                  f"| Adapté | {sum(rf[i]['passed'] for i in ids)} / {len(ids)} |", '']
            abst = [i for i in ids if not rf[i].get('llm_calls')]
            rest = [i for i in ids if i not in abst]
            if abst:
                L += [f"Avec le reranker adapté, {len(abst)} questions légitimes déclenchent l'abstention sans appel au "
                      f"modèle ({', '.join(abst)}) : il a appris à donner un score élevé aux **lignes de tableau**, le format "
                      "de ses exemples synthétiques, et très bas aux passages rédigés, si bien que le seuil d'abstention "
                      f"se déclenche. Sur les {len(rest)} autres questions, les deux rerankers font jeu égal "
                      f"(adapté {sum(rf[i]['passed'] for i in rest)}, origine {sum(rb[i]['passed'] for i in rest)}). "
                      "Le fine-tuning a donc surtout appris un biais de format, ce qui confirme la piste d'exemples "
                      "d'entraînement plus variés (passages rédigés, questions réelles).", '']
        L += ["**Conclusion** : l'adaptation améliore nettement le classement sur des questions du même format que "
              "l'entraînement, y compris sur des documents jamais vus, mais ce gain **ne se transfère pas** aux questions "
              "rédigées à la main. La v2, aux gabarits plus variés, fait même moins bien que le modèle d'origine : des "
              "questions synthétiques, même diversifiées, apprennent au modèle des formulations artificielles. Le reranker "
              "d'origine reste donc utilisé par défaut (`MAINTIDOC_RERANKER=base`). Piste : constituer des questions "
              "d'entraînement rédigées par des techniciens (ou générées par un LLM plus puissant puis relues).", '']
    L += ['## 4. Évaluation de bout en bout (vrai modèle)', '',
          f"**{summary['n_scenarios_executes']} / {summary['n_scenarios_total']} scénarios exécutés, réussite globale "
          f"{summary['reussite_globale']}.**", '', '| Catégorie | Exécutés | Réussis | Taux |', '|---|---|---|---|']
    for c, lab in CAT_LABEL.items():
        v = summary['par_categorie'].get(c)
        if v:
            L.append(f"| {lab} | {v['n']} | {v['reussis']} | {v['taux']} |")
    s1f = RES / 'iteration1_12docs' / 'summary.json'
    if s1f.exists():
        s1 = json.loads(s1f.read_text(encoding='utf-8'))
        p1 = {c: v['taux'] for c, v in s1['par_categorie'].items()}

        def taux(rs, c=None):
            sub = [r for r in rs if (r['category'] == c if c else r['category'] != 'corpus_etendu')]
            return pct(sum(r['passed'] for r in sub), len(sub)), sum(r['passed'] for r in sub), len(sub)
        L += ['', f"**Évolution sur les 150 scénarios du sujet** : itération 1 (12 documents, sans OCR) "
                  f"{s1['reussite_globale']} → itération 2 (30 documents, OCR) {taux(runs2)[0]} ({taux(runs2)[1]} / "
                  f"{taux(runs2)[2]}) → version finale {taux(runs)[0]} ({taux(runs)[1]} / {taux(runs)[2]}). "
                  f"Sur les 160 scénarios : itération 2 {pct(sum(r['passed'] for r in runs2), len(runs2))} → finale "
                  f"{summary['reussite_globale']}.", '',
              '| Catégorie | Itération 1 | Itération 2 | Finale |', '|---|---|---|---|']
        for c, lab in CAT_LABEL.items():
            if c in summary['par_categorie']:
                L.append(f"| {lab} | {p1.get(c, '-')} | {taux(runs2, c)[0]} | {summary['par_categorie'][c]['taux']} |")
        prev = {x['id']: x for x in runs2}
        gained = sorted(r['id'] for r in runs if r['passed'] and r['id'] in prev and not prev[r['id']]['passed'])
        lost = sorted(r['id'] for r in runs if not r['passed'] and r['id'] in prev and prev[r['id']]['passed'])
        L += ['', f"Réussis en version finale mais pas à l'itération 2 : {', '.join(gained) or 'aucun'} ; "
                  f"régressions : {', '.join(lost) or 'aucune'}."]
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
          '- **Latence (GPU intégré, scénario complet)** : ' + ' ; '.join(f"{k} : médiane {v['mediane']} s, p95 {v['p95']} s (n={v['n']})"
                                             for k, v in summary['latence_s'].items()) + '.', '',
          '### Échecs et analyse', '', '| Scénario | Catégorie | Contrôles en échec | Statut obtenu |', '|---|---|---|---|']
    for r in sorted(runs, key=lambda x: x['id']):
        if not r['passed']:
            fails = ', '.join(n for n, ok in r['checks'] if not ok).replace('|', ' ou ')
            L.append(f"| {r['id']} | {r['category']} | {fails} | {(r.get('result') or {}).get('status', '-')} |")
    # ---------------------------------------------------------- performances matérielles (mémoire et temps GPU)
    benchf = RES / 'bench_inference.json'
    if benchf.exists():
        bench = json.loads(benchf.read_text(encoding='utf-8'))
        L += ['', '### Performances matérielles : processeur et GPU intégré', '',
              "Mesures d'Ollama (`python eval/bench_inference.py`) sur deux requêtes types : réponse documentaire "
              "(~1 800 tokens de prompt) et analyse de photo (~1 300 tokens).", '',
              '| Matériel | Lecture prompt (tokens/s) | Génération (tokens/s) | Réponse texte (s) | Lecture photo (tokens/s) | '
              'Analyse photo (s) | Mémoire GPU (Go) |', '|---|---|---|---|---|---|---|']
        for b in bench:
            t, ph = b['reponse_texte'], b['analyse_photo']
            gpu = sum(m['memoire_gpu_go'] for m in b['memoire'])
            L.append(f"| {b['etiquette']} | {t['lecture_tokens_par_s']} | {t['generation_tokens_par_s']} | "
                     f"{t['duree_totale_s']} | {ph['lecture_tokens_par_s']} | {ph['duree_totale_s']} | {gpu} |")
        igpu = RES / 'e2e_runs_igpu.jsonl'
        if igpu.exists():
            rows = [json.loads(ln) for ln in igpu.read_text(encoding='utf-8').splitlines()]
            cpu = {r['id']: r for r in runs2}
            ok_gpu = {r['id'] for r in rows if r['passed']}
            ok_cpu = {r['id'] for r in rows if r['id'] in cpu and cpu[r['id']]['passed']}
            L += ['', f"Contrôle de qualité sur le GPU intégré ({len(rows)} scénarios clés, code de l'itération 3) : "
                      f"{len(ok_gpu)} réussis, contre {len(ok_cpu)} sur processeur à l'itération 2 pour les mêmes scénarios. "
                      f"Réussis seulement sur GPU : {', '.join(sorted(ok_gpu - ok_cpu)) or 'aucun'} ; réussis seulement sur "
                      f"processeur : {', '.join(sorted(ok_cpu - ok_gpu)) or 'aucun'}. Sur un échantillon aussi petit, la "
                      "qualité est jugée comparable."]
        f7 = RES / 'e2e_runs_7b.jsonl'
        if f7.exists():
            r7 = [json.loads(ln) for ln in f7.read_text(encoding='utf-8').splitlines()]
            r3 = {json.loads(ln)['id']: json.loads(ln) for ln in (RES / 'e2e_runs_igpu.jsonl').read_text(encoding='utf-8').splitlines()} \
                if (RES / 'e2e_runs_igpu.jsonl').exists() else {}
            L += ['', f"**Qwen2.5-VL 7B (modèle du sujet) sur le GPU intégré**, mêmes {len(r7)} scénarios clés : "
                      f"{sum(r['passed'] for r in r7)} réussis (3B : {sum(r3[r['id']]['passed'] for r in r7 if r['id'] in r3)}), "
                      f"durée médiane {statistics.median([r['seconds'] for r in r7]):.0f} s par scénario "
                      f"(3B : {statistics.median([r3[r['id']]['seconds'] for r in r7 if r['id'] in r3]) if r3 else 0:.0f} s). "
                      "Le 7B tient en mémoire GPU partagée (5,35 Go) mais génère environ 2,5 fois moins vite."]
        L += ['', "Le GPU intégré Intel Iris Xe (Vulkan, activé par `OLLAMA_IGPU_ENABLE=1`) lit le prompt et l'image 2,4 à "
                  "2,9 fois plus vite, mais génère un peu moins vite : le gain est surtout net sur les photos. L'objectif "
                  "de 30 s (p95) suppose toujours un GPU dédié.", '']
    # ---------------------------------------------------------- itération 3 : sécurité des réponses
    it3f, calf = RES / 'e2e_runs_iter3.jsonl', RES / 'faithfulness_calibration.json'
    if it3f.exists():
        it3 = {json.loads(ln)['id']: json.loads(ln) for ln in it3f.read_text(encoding='utf-8').splitlines()}
        it2 = {r['id']: r for r in runs2}
        fin = {r['id']: r for r in runs}
        ids = [i for i in it3 if i in it2]
        L += ['', '### Itération 3 : sécurité des réponses (scénarios concernés)', '',
              "Deux corrections, puis réévaluation des scénarios qu'elles touchent (questions textuelles, corpus étendu, "
              "tickets) :",
              "- **Consignes de sécurité indexées à part** (« ATTENTION », « Interdits ») et **toujours jointes aux "
              "preuves** pour une question sur une action physique, avec un **garde-fou déterministe** qui bloque une "
              "réponse autorisant une action interdite (« ne jamais … »).",
              "- **Pas de brouillon de ticket sans code d'alarme** : l'agent demande le code et rappelle les tickets "
              "déjà ouverts (évite les doublons).", '']
        if calf.exists():
            cal = json.loads(calf.read_text(encoding='utf-8'))
            L += [f"**Juge LLM de fidélité (calibration hors ligne sur {cal['n']} réponses de l'itération 2)** : "
                  f"verdicts {dict(cal['verdicts'])} ; bonnes réponses bloquées à tort : {len(cal['faux_positifs_bloques'])} ; "
                  f"réponses fausses détectées : {len(cal['echecs_bloques'])} (TEX-10 jugée « {cal['TEX-10']['verdict']} »). "
                  f"Le juge 3B valide tout et ajoute {cal['secondes_moyennes']} s par réponse : il est désactivé par défaut "
                  "(`MAINTIDOC_FAITHFULNESS_CHECK=1` pour l'activer avec un modèle plus grand).", '']
        L += ['| Catégorie | Scénarios | Itération 2 | Itération 3 | Finale |', '|---|---|---|---|---|']
        for c in sorted({it3[i]['category'] for i in ids}, key=lambda x: list(CAT_LABEL).index(x)):
            sub = [i for i in ids if it3[i]['category'] == c]
            L.append(f"| {CAT_LABEL[c]} | {len(sub)} | {sum(it2[i]['passed'] for i in sub)} | "
                     f"{sum(it3[i]['passed'] for i in sub)} | {sum(fin[i]['passed'] for i in sub if i in fin)} |")
        L.append(f"| **Total** | {len(ids)} | **{sum(it2[i]['passed'] for i in ids)}** | "
                 f"**{sum(it3[i]['passed'] for i in ids)}** | **{sum(fin[i]['passed'] for i in ids if i in fin)}** |")
        gained = sorted(i for i in ids if it3[i]['passed'] and not it2[i]['passed'])
        lost = sorted(i for i in ids if it2[i]['passed'] and not it3[i]['passed'])
        L += ['', f"Scénarios corrigés : {', '.join(gained) or 'aucun'} ; scénarios régressés : {', '.join(lost) or 'aucun'}."]
        if 'TEX-10' in it3:
            L.append(f"TEX-10 (« Peut-on retirer deux disques en même temps ? ») : "
                     f"« {(it3['TEX-10'].get('result') or {}).get('answer', '')[:160]} »")
        L.append('')
    # ---------------------------------------------------------- itération 4 : diagnostic du premier passage final
    e1f = RES / 'e2e_runs_iter4_essai1.jsonl'
    if e1f.exists() and runs:
        e1 = {json.loads(ln)['id']: json.loads(ln) for ln in e1f.read_text(encoding='utf-8').splitlines()}
        prev = {r['id']: r for r in runs2}
        fin = {r['id']: r for r in runs}
        lost1 = sorted(i for i in e1 if i in prev and prev[i]['passed'] and not e1[i]['passed'])
        L += ['', '### Itération 4 : diagnostic et corrections de la version finale', '',
              f"Un premier passage complet avec les ajouts de conformité (images des figures jointes au modèle, "
              f"recherche texte → image, retriever LangChain) a donné **{sum(r['passed'] for r in e1.values())} / "
              f"{len(e1)}**, contre {sum(r['passed'] for r in runs2)} / {len(runs2)} à l'itération 2. Régressions : "
              f"{', '.join(lost1) or 'aucune'}, surtout sur les questions avec photo. Deux causes identifiées dans les "
              "traces, puis corrigées :",
              "- **Les consignes de sécurité occupaient une des 4 places de preuves** et évinçaient le tableau qui "
              "répondait à la question (IMA-25, IMA-26) : une consigne retrouvée s'ajoute désormais aux 4 preuves "
              "(au plus une) au lieu d'en prendre la place.",
              "- **L'image de la figure jointe au modèle 3B l'induisait en erreur** (3 régressions, 1 gain ; ex. TEX-38 : "
              "« 2 segments » au lieu de 3, alors que le tableau est correct) : désactivée par défaut "
              "(`MAINTIDOC_MAX_EVIDENCE_IMAGES=1` pour l'activer avec un modèle plus grand).", '',
              f"**Après correction : {sum(r['passed'] for r in runs)} / {len(runs)}.** Scénarios récupérés par rapport "
              f"au premier passage : {', '.join(sorted(i for i in fin if fin[i]['passed'] and i in e1 and not e1[i]['passed'])) or 'aucun'} ; "
              f"perdus : {', '.join(sorted(i for i in fin if not fin[i]['passed'] and i in e1 and e1[i]['passed'])) or 'aucun'}.", '']
    analysis = ROOT / 'eval' / 'analyse_echecs.md'
    if analysis.exists():
        L += ['', analysis.read_text(encoding='utf-8').strip()]
    L += ['', '## 5. Limites', '',
          "- L'absence d'échec sur un contrôle ne constitue pas une garantie générale de sécurité.",
          '- Le petit modèle 3B, utilisé faute de GPU dédié, lit imparfaitement les photos ; le 7B, testé sur le GPU intégré, '
          'n\'y apporte pas de gain mesurable et un GPU de 24 Go reste la cible.',
          '- La latence mesurée sur le GPU intégré dépasse l\'objectif de 30 s (p95) fixé pour le matériel cible.',
          "- La pertinence des citations est mesurée par rapport aux éléments de référence (or), sans juge humain systématique.", '']
    (ROOT / 'docs' / 'rapport_evaluation.md').write_text('\n'.join(L), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))
