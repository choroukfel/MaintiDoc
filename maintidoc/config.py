"""Configuration centrale de MaintiDoc (chemins, modèles, budgets, secrets)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / 'corpus'
DATA_DIR = ROOT / 'data'
INDEX_DIR = DATA_DIR / 'index'
LOG_DIR = DATA_DIR / 'logs'
MODELS_DIR = DATA_DIR / 'models'
MCP_DATA_DIR = ROOT / 'mcp_server' / 'data'
MCP_DB = Path(os.getenv('MAINTIDOC_MCP_DB', DATA_DIR / 'mcp' / 'maintenance.db'))
PROMPTS_DIR = ROOT / 'prompts'

# ------------------------------------------------------------------ modèles
OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434')
VLM_MODEL = os.getenv('MAINTIDOC_VLM', 'qwen2.5vl:3b')
# BGE-M3 (2,2 Go) est remplacé par un modèle multilingue plus léger compatible avec le poste de développement
# (CPU, disque limité). Changer la variable d'environnement pour revenir à BAAI/bge-m3.
TEXT_EMBED_MODEL = os.getenv('MAINTIDOC_TEXT_EMBED', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
CLIP_MODEL = os.getenv('MAINTIDOC_CLIP', 'sentence-transformers/clip-ViT-B-32')
# Encodeur de texte CLIP multilingue (aligné sur CLIP ViT-B/32) : recherche « question en français -> schéma »
CLIP_TEXT_MODEL = os.getenv('MAINTIDOC_CLIP_TEXT', 'sentence-transformers/clip-ViT-B-32-multilingual-v1')
# Désactivée par défaut : mesurée sans gain avec le reranker et dégradant la fusion sans reranker (rapport, section 2)
TEXT_TO_IMAGE = os.getenv('MAINTIDOC_TEXT_TO_IMAGE', '0') == '1'
RERANKER_BASE = os.getenv('MAINTIDOC_RERANKER_BASE', 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')
RERANKER_FINETUNED = MODELS_DIR / 'reranker-maintidoc'
# 'finetuned' | 'base' | 'none'
# 'base' par défaut : meilleur résultat mesuré sur les scénarios rédigés à la main (voir docs/rapport_evaluation.md)
RERANKER_MODE = os.getenv('MAINTIDOC_RERANKER', 'base')
QDRANT_URL = os.getenv('QDRANT_URL')  # None => Qdrant embarqué (fichiers locaux)

# ------------------------------------------------------------------ budgets (appliqués par le code)
MAX_MCP_CALLS = int(os.getenv('MAINTIDOC_MAX_MCP_CALLS', 3))
MAX_LLM_CALLS = int(os.getenv('MAINTIDOC_MAX_LLM_CALLS', 6))
# Le sujet vise 60 s sur un GPU 24 Go ; sur le poste CPU de développement, la limite est relevée.
MAX_SECONDS = float(os.getenv('MAINTIDOC_MAX_SECONDS', 600))
MAX_IMAGE_SIDE = int(os.getenv('MAINTIDOC_MAX_IMAGE_SIDE', 512))
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_QUESTION_CHARS = 2000
NUM_CTX = int(os.getenv('MAINTIDOC_NUM_CTX', 4096))
NUM_PREDICT = int(os.getenv('MAINTIDOC_NUM_PREDICT', 400))
EVIDENCE_TOP_K = int(os.getenv('MAINTIDOC_TOP_K', 4))
# Nombre d'images de figures du corpus jointes à la génération de la réponse. 0 par défaut : avec le modèle 3B,
# l'image jointe a fait plus de mal que de bien (3 régressions, 1 gain : rapport, itération 4) ; 1 avec un modèle plus grand.
MAX_EVIDENCE_IMAGES = int(os.getenv('MAINTIDOC_MAX_EVIDENCE_IMAGES', 0))
EVIDENCE_MAX_CHARS = int(os.getenv('MAINTIDOC_EVIDENCE_CHARS', 1100))
LLM_TIMEOUT = float(os.getenv('MAINTIDOC_LLM_TIMEOUT', 420))

# ------------------------------------------------------------------ quotas par utilisateur (API)
QUOTA_REQUESTS_PER_HOUR = int(os.getenv('MAINTIDOC_QUOTA_REQUESTS_PER_HOUR', 30))
# plafond de temps de calcul (inférence CPU/GPU) par utilisateur et par période glissante de 24 h
QUOTA_COMPUTE_SECONDS_PER_DAY = float(os.getenv('MAINTIDOC_QUOTA_COMPUTE_SECONDS_PER_DAY', 3600))
QUOTA_MAX_CONCURRENT = int(os.getenv('MAINTIDOC_QUOTA_MAX_CONCURRENT', 1))

# ------------------------------------------------------------------ journalisation
LOG_RETENTION_DAYS = int(os.getenv('MAINTIDOC_LOG_RETENTION_DAYS', 30))

# ------------------------------------------------------------------ secrets (injectés à l'exécution, jamais dans Git)
# En développement, un secret aléatoire est généré par processus et transmis au serveur MCP enfant
# via l'environnement. En production : variables d'environnement / gestionnaire de secrets.
os.environ.setdefault('MAINTIDOC_APPROVAL_SECRET', secrets.token_hex(32))
os.environ.setdefault('MAINTIDOC_JWT_SECRET', secrets.token_hex(32))
APPROVAL_SECRET = os.environ['MAINTIDOC_APPROVAL_SECRET']
# Journaux : pseudonymisation des utilisateurs (secret à fixer dans .env pour des pseudonymes stables entre
# redémarrages) et chiffrement optionnel des lignes (clé Fernet, générée par Fernet.generate_key()).
os.environ.setdefault('MAINTIDOC_LOG_SECRET', secrets.token_hex(32))
LOG_SECRET = os.environ['MAINTIDOC_LOG_SECRET']
LOG_KEY = os.getenv('MAINTIDOC_LOG_KEY')
JWT_SECRET = os.environ['MAINTIDOC_JWT_SECRET']

# Version de l'application (tag Git), injectée par Docker Compose ; enregistrée avec chaque requête.
APP_VERSION = os.getenv('MAINTIDOC_VERSION', 'dev')

PROMPT_VERSIONS = {'answer': 'answer_v1', 'visual': 'visual_analysis_v2', 'ticket': 'ticket_draft_v1',
                   'evidence_check': 'evidence_check_v2'}
# Vérification de fidélité avant affichage (un appel au modèle de plus par réponse affirmative)
# Juge LLM de fidélité : désactivé par défaut (calibré inefficace avec Qwen2.5-VL 3B ; à activer avec un modèle plus grand).
# Le garde-fou déterministe sur les interdictions reste toujours actif.
FAITHFULNESS_CHECK = os.getenv('MAINTIDOC_FAITHFULNESS_CHECK', '0') == '1'
