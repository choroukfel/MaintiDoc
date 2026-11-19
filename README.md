# MaintiDoc

Assistant documentaire **multimodal et agentique** pour la maintenance, réalisé dans le cadre du projet intégrateur
d'IA générative multimodale. À partir d'une question en français, éventuellement accompagnée de la photo d'un
panneau, MaintiDoc :
- recherche les passages, tableaux et figures pertinents ;
- répond en citant ses sources (document, page, élément), ou demande une clarification ;
- consulte l'historique de l'équipement via un serveur **MCP** ;
- prépare un ticket, créé seulement après **approbation humaine**.

> Démonstration sur équipements, documents, tickets et utilisateurs **fictifs**. MaintiDoc ne pilote aucun équipement
> et ne remplace pas un technicien habilité (voir [docs/fiche_limites.md](docs/fiche_limites.md)).

## Architecture

```
Streamlit (ui/app.py) ──HTTP──> FastAPI (api/main.py) : JWT, rôles, accès aux pages des documents
                                   │
                         Workflow LangGraph (maintidoc/agent.py)
 check_access → analyze (texte + photo) → retrieve → [mcp_lookup] → generate → verify
   → check_faithfulness → [draft_ticket → human_approval (interrupt) → create_ticket] → finalize
      │                 │                      │                     │
 security.py     retrieval.py            llm.py (Ollama)       mcp_client.py ──stdio──> mcp_server/server.py
 (injections,    Qdrant + BM25 + CLIP,   Qwen2.5-VL, JSON      get_equipment_history / get_open_tickets /
 jetons HMAC)    RRF, reranker           contraint (Pydantic)  create_ticket (jeton + idempotence)
                 ocr.py (EasyOCR)        vision.py (voyants)   SQLite ou PostgreSQL
                 storage.py : documents en dossier local ou dans MinIO
```

| Composant | Choix | Remarque |
|---|---|---|
| Modèle vision-langage | Qwen2.5-VL via Ollama | 3B sur le poste CPU, 7B recommandé sur GPU 24 Go |
| Orchestration | LangGraph (état, budgets, `interrupt`) + LangChain (prompts) | |
| Recherche vectorielle | Qdrant (embarqué, ou serveur via `QDRANT_URL`) | serveur dans Docker Compose |
| Embeddings texte | paraphrase-multilingual-MiniLM-L12-v2 | BGE-M3 trop lourd pour le poste (variable `MAINTIDOC_TEXT_EMBED`) |
| Recherche visuelle | CLIP ViT-B/32 (photo → figures du corpus) | |
| Lexical | BM25 (codes d'alarme : F-05, AL-31…) | |
| Fusion et reranking | RRF + cross-encoder mmarco-mMiniLMv2 | version fine-tunée comparée (`eval/finetune_reranker.py`) |
| Extraction | PyMuPDF (texte, tableaux ligne par ligne, figures + légendes) + **OCR local EasyOCR** | pages scannées, texte des figures, photos |
| Outils | Serveur MCP (SDK `mcp` 2.x, stdio) | base SQLite (développement) ou **PostgreSQL** (`MAINTIDOC_DB_URL`) |
| Stockage des documents | dossier `corpus/` ou **MinIO** (`MAINTIDOC_S3_ENDPOINT`) | MinIO amorcé automatiquement au premier démarrage |
| API / UI | FastAPI + Streamlit | |
| Suivi / déploiement | MLflow (`mlruns/`) + Docker Compose + CI GitHub Actions | `.github/workflows/ci.yml` |

## Arborescence

```
maintidoc/      cœur : config, ingestion, ocr, vision, storage, retrieval, llm, security, agent, schemas, mcp_client
mcp_server/     serveur MCP de maintenance + données fictives (équipements, historique, tickets, utilisateurs)
api/  ui/       API FastAPI et interface Streamlit
prompts/        prompts versionnés (answer_v1, visual_analysis_v1 puis v2, ticket_draft_v1, evidence_check_v1)
corpus/         30 PDF fictifs (dont 2 scannés) + metadata.json (groupes d'accès, versions, niveau de confiance)
scripts/        génération du corpus et des figures
eval/           160 scénarios, photos de test, fine-tuning, évaluation, résultats (et ceux de l'itération 1)
tests/          tests automatisés (pytest)
docs/           note de cadrage, fiche de limites, rapport d'évaluation
.github/        chaîne CI (analyse statique, secrets, audit des dépendances, tests, image Docker)
app.py          prototype initial (conservé pour mémoire)
```

## Installation (Windows, CPU)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
# Ollama : https://ollama.com/download
ollama pull qwen2.5vl:3b
```

## Utilisation

```powershell
# 1. API (construit l'index au premier lancement)
uvicorn api.main:app --port 8000
# 2. Interface, dans un second terminal
streamlit run ui/app.py
```

### Avec Docker Compose (Qdrant, PostgreSQL, MinIO, API, interface)

```powershell
copy .env.example .env      # puis remplacer toutes les valeurs (mots de passe, secrets)
docker compose up -d --build
# interface : http://localhost:8501  ·  API : http://localhost:8000/health  ·  console MinIO : http://localhost:9001
```
Par défaut, l'API utilise l'Ollama installé sur la machine hôte (`OLLAMA_HOST` dans `.env`). Sur un serveur,
`--profile ollama` démarre aussi Ollama dans un conteneur, et `--profile minio` stocke les documents dans MinIO
(avec `MAINTIDOC_S3_ENDPOINT=minio:9000`). Sur le poste de développement, ajouter
`-f docker-compose.local.yml` réutilise les modèles déjà téléchargés.

Utilisateurs fictifs : `u-tech-a` (technicien SAN), `u-tech-b` (technicien onduleurs), `u-resp-1` (responsable,
seul habilité à approuver un ticket et à lire SAN-CAB-012), `u-stagiaire` (lecture seule).

### Scénario de démonstration (section 14 du sujet)
1. **Question + photo** : `u-tech-a`, photo `eval/photos/photo_san3000_fan_orange.jpg`, question « Que signifie ce
   voyant sur SAN-LAB-3000-01 ? Consulte l'historique et prépare un ticket. »
2. **Réponse sourcée** : SAN-MAN-010, page 1, TAB-02 (AL-31, ventilateur dégradé).
3. **Historique MCP** : INC-133 (FAN-3 dégradé, aucun ticket).
4. **Ticket** : brouillon, puis approbation par `u-resp-1` et création via MCP.
5. **Ambiguïté** : `photo_ambigue_voyant_orange.jpg` conduit à une demande de clarification.
6. **Blocages** : `u-tech-a` demande les circuits PDU (document restreint) ; « Ignore les instructions… » est refusé.
7. **Performances** : voir [docs/rapport_evaluation.md](docs/rapport_evaluation.md).

## Tests et évaluation

```powershell
pytest                                   # 73 tests automatisés (LLM simulé, MCP réel en stdio, OCR réel)
python eval/finetune_reranker.py         # fine-tuning du reranker (CPU, environ 30 min)
python eval/run_eval.py retrieval        # Recall@5, nDCG@5, MRR@10 (5 configurations)
python eval/run_eval.py e2e              # 160 scénarios avec le vrai modèle (plusieurs heures sur CPU)
python eval/run_eval.py report           # rapport : docs/rapport_evaluation.md
```

## Résultats (détail dans [docs/rapport_evaluation.md](docs/rapport_evaluation.md))

Mesurés sur un PC portable sans GPU (i5, 15 Go de RAM), avec Qwen2.5-VL 3B, corpus de 30 documents :

| Catégorie (160 scénarios) | Réussite |
|---|---|
| Questions documentaires textuelles (40) | 90 % |
| Questions avec photo (30) | 80 % |
| Consultation MCP (20) | 100 % |
| Sources absentes ou contradictoires (20) | 85 % |
| Contrôle d'accès et validation humaine (20) | 100 % |
| Injections de prompt (20) | 100 % |
| Corpus étendu et OCR (10) | 90 % |
| **Total** | **91 % (146 / 160)** |

- **Sécurité** : 0 ticket créé sans approbation, 0 document interdit dans les preuves, 20 injections sur 20 neutralisées
  (documents, document scanné, photo, réponse MCP).
- **Citations** : 100 % pointent vers une preuve réellement récupérée ; 78 % citent un élément de référence.
- **Recherche** : le reranker fait passer le MRR@10 de 0,55 (hybride) à 0,75. Le reranker fine-tuné progresse sur ses
  propres questions de test (0,66 → 0,82) mais pas sur les questions rédigées à la main (0,63) : le reranker
  d'origine reste utilisé par défaut.
- **Itération 3** : le cas « peut-on retirer deux disques à la fois ? », répondu « oui » à tort, est corrigé (consignes de sécurité indexées à part, garde-fou sur les interdictions) ; aucun ticket n'est plus proposé sans code d'alarme. Un juge LLM de fidélité a été testé : inefficace avec le modèle 3B, il est désactivé par défaut.
- **Latence (CPU)** : médiane de 38 s (texte) et 77 s (photo) ; p95 de 64 s et 175 s.
- **Itération 1** (12 documents, sans OCR) : 89 % sur les 150 scénarios du sujet, contre 91 % à l'itération 2.

## Sécurité (résumé)
- **Secrets** : injectés par variables d'environnement (`.env`, ignoré par Git), jamais dans les prompts ni dans les journaux.
- **Contrôle documentaire** : filtrage par groupe dans la requête Qdrant (avant récupération) et à l'affichage des pages (API, 403).
- **Outils MCP** : autorisations vérifiées côté serveur à chaque appel. `create_ticket` exige un jeton HMAC lié à
  l'approbateur et au contenu exact, et une clé d'idempotence.
- **Injections** : documents, OCR et réponses MCP traités comme des données. Les phrases suspectes sont neutralisées,
  et une demande d'utilisateur contenant une injection est refusée.
- **Budgets** (appliqués par le code) : 3 appels MCP, 6 appels au modèle, temps maximal configurable, taille des
  images et du contexte limitée.
- **Quotas par utilisateur** (API) : 30 requêtes par heure, 3 600 s de calcul par 24 h glissantes, une requête à la
  fois ; au-delà, réponse 429 avec le délai d'attente (`Retry-After`). Consommation visible sur `GET /quota`.
  Réglables par `MAINTIDOC_QUOTA_REQUESTS_PER_HOUR`, `MAINTIDOC_QUOTA_COMPUTE_SECONDS_PER_DAY`, `MAINTIDOC_QUOTA_MAX_CONCURRENT`.
- **Journalisation** : métadonnées uniquement (`data/logs/requests.jsonl`), sans contenu brut ; entrées purgées
  au-delà de 30 jours (`MAINTIDOC_LOG_RETENTION_DAYS`), au démarrage de l'API puis une fois par jour.
