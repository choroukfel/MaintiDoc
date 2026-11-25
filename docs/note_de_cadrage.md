# MaintiDoc : note de cadrage

*Projet intégrateur d'IA générative multimodale. Version 1.1 du 19 novembre 2026.*

## 1. Objectif

MaintiDoc est un assistant documentaire destiné aux techniciens de maintenance. À partir d'une question en
français, éventuellement accompagnée d'une photographie d'un panneau d'équipement, il :

- recherche les passages, tableaux et figures pertinents dans un corpus de manuels autorisés ;
- produit une réponse **sourcée** (document, page, élément) ou demande une **clarification** ;
- consulte l'historique des interventions et les tickets ouverts via un serveur **MCP** ;
- prépare un **brouillon de ticket**, créé seulement après **approbation humaine explicite**.

MaintiDoc est une aide documentaire. Il ne pilote aucun équipement, ne garantit pas de diagnostic et ne
remplace ni les procédures de sécurité ni un technicien habilité.

## 2. Utilisateurs et droits

| Profil | Groupes documentaires | Préparer un ticket | Approuver un ticket |
|---|---|---|---|
| Technicien SAN (Tech-A) | `tech_san` | Oui | Non |
| Technicien onduleurs (Tech-B) | `tech_ups` | Oui | Non |
| Responsable maintenance (Resp-1) | `tech_san`, `tech_ups`, `responsables` | Oui | Oui |
| Stagiaire (lecteur) | `tech_ups` | Non | Non |

Tous les utilisateurs sont fictifs (`mcp_server/data/utilisateurs.json`).

## 3. Périmètre

**Inclus**
- Deux familles d'équipements fictifs : baies de stockage **SAN-LAB 2000 / 3000** et onduleurs **UPS-X500 / X900**.
- Entrées : question en français, photographie, document PDF.
- Sorties : réponse sourcée au format JSON validé, demande de clarification, brouillon de ticket.
- Outils MCP : `get_equipment_history`, `get_open_tickets`, `create_ticket` (avec jeton d'approbation).

**Exclus**
- Pilotage ou reconfiguration d'équipements (failover, zoning, suppression de LUN, réarmement d'onduleur).
- Diagnostic garanti, interventions autonomes.
- Données réelles : tous les documents, équipements, tickets et utilisateurs sont synthétiques.

## 4. Corpus

Le corpus compte **30 documents** (`corpus/`, décrits dans `corpus/metadata.json`), dans la fourchette de 30 à 50
fixée par le sujet. Il est généré de façon reproductible par `scripts/generate_corpus.py`.

| Famille | Documents | Points d'attention pour l'évaluation |
|---|---|---|
| SAN-LAB 2000 | SAN-MAN-001, SAN-ALM-002, SAN-PROC-003, SAN-FW-004, SAN-PSU-005, SAN-CAP-007 | Un même voyant FAULT orange correspond à trois alarmes possibles |
| SAN-LAB 3000 | SAN-MAN-010, SAN-ERR-011, SAN-FAN-013, SAN-TEMP-014 | Codes différents du 2000 ; règle de récurrence nécessitant l'historique |
| Famille SAN | SAN-MPIO-006, SAN-PM-015, SAN-FAQ-016, SAN-CAB-012 | SAN-CAB-012 en **diffusion restreinte** (`responsables`) : test de contrôle d'accès |
| UPS-X500 | UPS-MAN-020, UPS-PROC-021, BT-2026-07, UPS-INST-022 | **Contradiction** volontaire entre la procédure et le bulletin |
| UPS-X900 | UPS-MAN-030, UPS-ERR-031, UPS-BYP-032, UPS-BAT-033 | Deux voyants orange voisins : nécessite l'analyse d'image ou une clarification |
| Onduleurs | UPS-GEN-034, UPS-PM-035, UPS-SEC-036, UPS-FAQ-037 | Documents transverses aux deux modèles |
| Commun | GLO-ORG-040 | Rôles et délais d'escalade, lisible par tous les profils |
| **Documents scannés** | SAN-SCAN-017, UPS-SCAN-038 | Sans couche texte : contenu accessible **uniquement par OCR** ; UPS-SCAN-038 contient une annotation piégée |
| Test sécurité | NOTE-EXT-099 | **Injection de prompt** : ne doit jamais être suivie |

Données associées :
- `mcp_server/data/` : 6 équipements, 21 interventions (dont une entrée piégée), 5 tickets (dont 4 ouverts) ;
- `eval/photos/` : 16 photographies simulées de panneaux, dont une volontairement ambiguë et une portant une
  instruction piégée.

## 5. Architecture

![Architecture](<../Architecture Agentique RAG pour la Maintenance SAN-1.png>)

| Couche | Choix réalisé |
|---|---|
| Interface et API | Streamlit, FastAPI (authentification JWT, rôles, contrôle d'accès aux pages) |
| Orchestration | LangGraph (état, budgets, interruption pour validation humaine) ; LangChain pour la récupération (`BaseRetriever`), les prompts (`PromptTemplate`) et la validation des sorties (`PydanticOutputParser`) |
| Extraction | PyMuPDF (texte, tableaux, figures) et **OCR local EasyOCR** (pages scannées, texte des figures, photos) |
| Recherche | Qdrant ; MiniLM multilingue (texte ; BGE-M3 sur une machine plus puissante) ; BM25 ; CLIP (photo → figures) et encodeur de texte CLIP multilingue (question → figures) ; fusion RRF ; reranker multilingue (version fine-tunée évaluée) |
| Modèle vision-langage | Qwen2.5-VL à poids ouverts, servi par Ollama sur le GPU intégré (3B par défaut ; 7B testé, recommandé sur GPU dédié) ; images des figures retrouvées jointes à la génération |
| Outils | Serveur MCP de maintenance (SQLite en développement, PostgreSQL en déploiement) |
| Stockage et suivi | PostgreSQL (métadonnées documentaires : tables `documents` et `elements` ; tickets et historique MCP), MinIO (documents), MLflow ; Docker Compose ; CI GitHub Actions |

## 6. Risques et parades

| Risque | Parade prévue |
|---|---|
| Réponse inventée ou citation non pertinente | Sortie JSON validée par Pydantic ; citations limitées aux éléments récupérés ; contrôle « la source soutient-elle l'affirmation ? » |
| Confusion entre modèles (voyant orange) | Métadonnées modèle et famille ; clarification si le modèle n'est pas identifié |
| Photo illisible ou ambiguë | Statut `needs_clarification` ; aucune confiance chiffrée inventée |
| Sources contradictoires | Signaler la contradiction et demander un arbitrage humain |
| Accès à un document non autorisé | Filtrage par groupe dans la requête Qdrant et à l'affichage |
| Injection de prompt (documents, OCR, réponses MCP) | Contenus traités comme des données ; outils sensibles soumis à approbation ; jeu de tests dédié |
| Ticket créé par erreur ou en double | Jeton d'approbation lié à l'utilisateur et au contenu exact ; clé d'idempotence |
| Matériel limité (PC sans GPU, 15 Go de RAM) | Petit VLM quantifié en local ; fine-tuning et démonstration sur GPU distant (Colab) |

## 7. Critères de réussite (objectifs, non acquis)

- Recall@5 et nDCG@5 mesurés avec et sans reranker adapté.
- Au moins 90 % de citations soutenant réellement l'affirmation associée.
- Aucune création de ticket sans approbation et aucune fuite entre groupes dans les tests.
- Latence p95 inférieure à 30 s pour une question simple sur le matériel de démonstration.

## 8. Planning

| Semaine | Travaux |
|---|---|
| 1 | Cadrage, corpus, données MCP, analyse des risques *(cette note)* |
| 2 | Ingestion multi-documents, Qdrant, recherche texte et image, filtrage d'accès, reranker |
| 3 | VLM, schéma de sortie Pydantic, prompts versionnés, vérification des citations |
| 4 | Serveur MCP, workflow LangGraph, validation humaine |
| 5 | Fine-tuning du reranker, jeu de 150 scénarios, tests d'injection |
| 6 | Docker Compose, MLflow, documentation, soutenance |
