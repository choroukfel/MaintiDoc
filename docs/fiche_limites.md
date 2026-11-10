# MaintiDoc : fiche de limites et conditions d'utilisation

*Livrable 5. Version 1.0 du 9 novembre 2026.*

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
| Baies de stockage | SAN-LAB 2000, SAN-LAB 3000 | SAN-MAN-001, SAN-ALM-002, SAN-PROC-003, SAN-MAN-010, SAN-ERR-011, SAN-CAB-012 (restreint) |
| Onduleurs | UPS-X500, UPS-X900 | UPS-MAN-020, UPS-PROC-021, BT-2026-07, UPS-MAN-030, UPS-ERR-031 |

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
- **Lecture d'image imparfaite** : le modèle 3B utilisé sur le poste sans GPU confond parfois les étiquettes ;
  l'identification combine donc similarité visuelle (CLIP), codes lus à l'écran et noms de voyants, puis demande
  une clarification en cas de doute. Un modèle 7B sur GPU reste recommandé.
- **Citations** : leur validité structurelle (elles pointent vers des preuves réellement récupérées) est contrôlée
  automatiquement. Le fait qu'elles **soutiennent** réellement l'affirmation n'est vérifié que partiellement (codes
  cités, comparaison avec un jeu de référence) : une relecture humaine reste nécessaire.
- **Détection d'injection par motifs** : efficace sur les formulations testées, contournable par des formulations
  nouvelles. La protection principale est architecturale : les outils sont déclenchés par le code du workflow, et
  la création de ticket exige un jeton d'approbation signé côté serveur.
- **Authentification de démonstration** : connexion par identifiant fictif sans mot de passe ; à remplacer par un
  fournisseur d'identité en production.
- **Performance** : sur CPU, une réponse prend de 1 à 5 minutes, loin de l'objectif de 30 s (p95) visé sur GPU 24 Go.

## Conditions d'utilisation
1. Vérifier les références citées avant toute intervention.
2. Respecter les consignes de sécurité du site et l'habilitation requise.
3. Ne créer un ticket qu'après relecture complète de son contenu ; toute modification fait l'objet d'une nouvelle approbation.
4. Signaler toute réponse erronée pour enrichir le jeu d'évaluation.
