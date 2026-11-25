# MaintiDoc : fiche de limites et conditions d'utilisation

*Livrable 5. Version 1.3 du 25 novembre 2026 (version finale v3.1 : corpus de 30 documents, OCR, GPU intégré).*

## Usages autorisés
- Rechercher une information dans la documentation **autorisée** (manuels, procédures, bulletins) et obtenir une
  réponse **citant** le document, la page et l'élément (tableau, figure, passage).
- Interpréter, **à titre documentaire**, un voyant ou un code visible sur une photographie de panneau.
- Consulter l'historique des interventions et les tickets ouverts d'un équipement auquel l'utilisateur a accès.
- Préparer un **brouillon** de ticket ; la création effective exige l'approbation d'un responsable.

## Usages interdits ou hors périmètre
- Piloter, reconfigurer ou réarmer un équipement (failover, zoning, suppression de volume, désactivation d'alarme).
  Ces demandes sont refusées.
- Considérer une réponse comme un **diagnostic garanti** : une ressemblance visuelle ou un voyant seul ne suffisent pas.
- Remplacer les procédures de sécurité, l'habilitation électrique ou l'avis d'un technicien habilité.
- Traiter des données réelles : la démonstration utilise exclusivement des équipements, tickets et utilisateurs fictifs.

## Équipements couverts
| Famille | Modèles (fictifs) | Documents |
|---|---|---|
| Baies de stockage | SAN-LAB 2000, SAN-LAB 3000 | SAN-MAN-001, SAN-ALM-002, SAN-PROC-003, SAN-FW-004, SAN-PSU-005, SAN-MPIO-006, SAN-CAP-007, SAN-MAN-010, SAN-ERR-011, SAN-CAB-012 (restreint), SAN-FAN-013, SAN-TEMP-014, SAN-PM-015, SAN-FAQ-016, SAN-SCAN-017 (scanné) |
| Onduleurs | UPS-X500, UPS-X900 | UPS-MAN-020, UPS-PROC-021, BT-2026-07, UPS-INST-022, UPS-MAN-030, UPS-ERR-031, UPS-BYP-032, UPS-BAT-033, UPS-GEN-034, UPS-PM-035, UPS-SEC-036, UPS-FAQ-037, UPS-SCAN-038 (scanné) |
| Commun | Tous | GLO-ORG-040 (organisation, escalade) ; NOTE-EXT-099 (source externe non vérifiée, test de sécurité) |

Toute question sur un autre équipement conduit à une abstention (« aucune source autorisée suffisamment pertinente »).

## Comportements de prudence intégrés
| Situation | Comportement |
|---|---|
| Photo floue ou équipement non identifiable | Demande de clarification (modèle, code affiché, photo plus nette) |
| Aucune source pertinente accessible | Abstention, sans réponse inventée |
| Documents contradictoires (BT-2026-07 / UPS-PROC-021) | Contradiction signalée, arbitrage du responsable demandé |
| Instruction cachée dans un document, une photo ou une réponse d'outil | Contenu neutralisé, signalé, jamais exécuté |
| Demande d'action dangereuse | Refus avec renvoi vers un technicien habilité |
| Ticket déjà ouvert sur le même sujet | Pas de doublon proposé |

## Risques résiduels connus
- **Lecture d'image imparfaite** : le modèle 3B utilisé sur le poste sans GPU dédié confond parfois les étiquettes
  (73 % de réussite sur les questions avec photo, contre 92 % sur les questions textuelles) ;
  l'identification combine donc similarité visuelle (CLIP), codes lus à l'écran et noms de voyants, puis demande
  une clarification en cas de doute. Un modèle 7B sur GPU reste recommandé.
- **Fidélité des réponses** : une réponse qui autorise une action interdite par une consigne de sécurité est bloquée par un garde-fou déterministe. Les autres erreurs de fond (mauvaise ligne d'un tableau, confusion fixe / clignotant) ne sont pas détectées automatiquement : le juge LLM testé, avec le modèle 3B, ne repère aucune erreur. Il est activable (`MAINTIDOC_FAITHFULNESS_CHECK=1`) avec un modèle plus grand.
- **Tickets** : aucun brouillon n'est proposé sans code d'alarme, pour éviter les doublons.
- **OCR** : les documents scannés et le texte des photos sont lus par OCR local (EasyOCR). Les erreurs de lecture
  sont possibles (confusions O/0, mots dans le désordre sur une photo) : les passages issus de l'OCR sont signalés
  comme tels, et un texte lu sur une photo n'est jamais exécuté comme une instruction.
- **Citations** : leur validité structurelle (elles pointent vers des preuves réellement récupérées) est contrôlée
  automatiquement. Le fait qu'elles **soutiennent** réellement l'affirmation n'est vérifié que partiellement (codes
  cités, comparaison avec un jeu de référence) : une relecture humaine reste nécessaire.
- **Détection d'injection par motifs** : efficace sur les formulations testées, contournable par des formulations
  nouvelles. La protection principale est architecturale : les outils sont déclenchés par le code du workflow, et
  la création de ticket exige un jeton d'approbation signé côté serveur.
- **Réseau** : dans Docker, les bases de données n'ont aucun accès sortant ; l'API garde un accès sortant (téléchargement
  des modèles, Ollama de l'hôte), qu'un pare-feu ou un proxy devrait restreindre en production.
- **Données personnelles** : les journaux ne contiennent ni question ni réponse ; les utilisateurs y sont pseudonymisés (HMAC) et les lignes peuvent être chiffrées (`MAINTIDOC_LOG_KEY`). La base des tickets conserve les noms (fictifs) des auteurs et approbateurs, en clair.
- **Modèle 7B** : testé sur le GPU intégré (5,35 Go en mémoire GPU) ; environ 2 fois plus lent que le 3B, sans gain de qualité mesuré sur 10 scénarios clés. Il n'est utile qu'avec un GPU dédié.
- **Quotas** : appliqués en mémoire par l'unique processus d'API de la démonstration ; avec plusieurs instances,
  un stockage partagé des compteurs (Redis, PostgreSQL) serait nécessaire.
- **Authentification de démonstration** : connexion par identifiant fictif sans mot de passe ; à remplacer par un
  fournisseur d'identité en production.
- **Performance** : avec le GPU intégré Intel activé (`OLLAMA_IGPU_ENABLE=1`), un scénario complet prend en médiane
  22 s (texte) et 48 s (photo), p95 41 s et 64 s (sur processeur seul : p95 64 s et 175 s). L'objectif de 30 s (p95)
  reste visé sur un GPU dédié de 24 Go.
- **Budget de temps** : 120 s de traitement automatique par demande au lieu des 60 s du sujet, qui supposent un GPU
  dédié. Au-delà, l'agent s'arrête et le signale. Sur processeur seul, relever la limite (`MAINTIDOC_MAX_SECONDS=300`).
- **Variabilité** : le modèle 3B n'est pas parfaitement déterministe ; une même question peut être réussie à une
  exécution et manquée à la suivante (1 à 2 scénarios sur 160 d'un passage à l'autre).
- **Options évaluées mais désactivées** : image de la figure jointe au modèle (trompe le 3B), recherche texte → image
  (sans gain), reranker fine-tuné (moins bon sur les questions rédigées à la main). Elles restent activables par
  variable d'environnement pour un modèle plus grand.

## Conditions d'utilisation
1. Vérifier les références citées avant toute intervention.
2. Respecter les consignes de sécurité du site et l'habilitation requise.
3. Ne créer un ticket qu'après relecture complète de son contenu ; toute modification fait l'objet d'une nouvelle approbation.
4. Signaler toute réponse erronée pour enrichir le jeu d'évaluation.
