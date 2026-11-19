"""Mesure des performances d'inférence d'Ollama (processeur, GPU intégré ou GPU dédié).

Rejoue deux requêtes représentatives de MaintiDoc :
  * une réponse documentaire (prompt answer_v1 avec ~1 500 tokens de preuves) ;
  * une analyse de photo (prompt visual_analysis_v2 + image).
Relève la vitesse de lecture du prompt, la vitesse de génération, la durée totale et la mémoire occupée
(dont la mémoire GPU) rapportées par Ollama.  Usage : python eval/bench_inference.py <étiquette>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maintidoc import config  # noqa: E402
from maintidoc.llm import image_to_bytes, load_prompt  # noqa: E402



def run(client, prompt: str, variables: dict, images=None, num_predict=200) -> dict:
    p = load_prompt(prompt)
    msg = {'role': 'user', 'content': p['user_template'].format(**variables)}
    if images:
        msg['images'] = images
    t = time.time()
    r = client.chat(model=config.VLM_MODEL, messages=[{'role': 'system', 'content': p['system']}, msg],
                    options={'temperature': 0, 'num_ctx': config.NUM_CTX, 'num_predict': num_predict}, keep_alive='5m')
    total = time.time() - t
    return {'prompt_tokens': r.prompt_eval_count, 'tokens_generes': r.eval_count,
            'lecture_tokens_par_s': round(r.prompt_eval_count / max(1e-9, r.prompt_eval_duration / 1e9), 1),
            'generation_tokens_par_s': round(r.eval_count / max(1e-9, r.eval_duration / 1e9), 1),
            'duree_totale_s': round(total, 1), 'chargement_s': round((r.load_duration or 0) / 1e9, 1)}


def main(label: str):
    from ollama import Client
    from PIL import Image
    client = Client(host=config.OLLAMA_HOST, timeout=900)
    evidence = '\n\n'.join(
        e['content'] for e in json.loads((config.INDEX_DIR / 'elements.json').read_text(encoding='utf-8'))
        if e['document_id'] in ('SAN-ALM-002', 'SAN-PROC-003'))[:4400]
    run(client, 'answer_v1', {'question': 'test', 'context': '', 'evidence': 'test'}, num_predict=5)  # chargement
    texte = run(client, 'answer_v1', {'question': 'Que signifie le voyant FAULT orange fixe sur le SAN-LAB 2000 ?',
                                      'context': '', 'evidence': evidence})
    img = image_to_bytes(Image.open(ROOT / 'eval' / 'photos' / 'photo_ups900_surcharge_orange.jpg').convert('RGB'))
    photo = run(client, config.PROMPT_VERSIONS['visual'], {'question': 'Que signifie ce voyant ?'}, images=[img])
    ps = client.ps().models
    mem = [{'modele': m.model, 'memoire_totale_go': round(m.size / 1e9, 2), 'memoire_gpu_go': round(m.size_vram / 1e9, 2)}
           for m in ps]
    result = {'etiquette': label, 'date': time.strftime('%Y-%m-%dT%H:%M:%S'), 'modele': config.VLM_MODEL,
              'reponse_texte': texte, 'analyse_photo': photo, 'memoire': mem}
    out = ROOT / 'eval' / 'results' / 'bench_inference.json'
    runs = json.loads(out.read_text(encoding='utf-8')) if out.exists() else []
    out.write_text(json.dumps([r for r in runs if r['etiquette'] != label] + [result], ensure_ascii=False, indent=1),
                   encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'cpu')
