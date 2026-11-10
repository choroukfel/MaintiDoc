### Analyse qualitative des échecs

| Cause | Scénarios | Explication |
|---|---|---|
| Photo : plusieurs voyants de même couleur | IMA-08, IMA-10, IMA-12, IMA-14 (SAN-LAB 3000), IMA-23, IMA-24 (UPS-X900) | Le modèle est bien identifié, mais le panneau porte plusieurs voyants orange (FAN, TEMP, FAULT ; BATT, SURCHARGE). Le VLM 3B lit rarement les étiquettes sur ces photos sombres, et la seule couleur détectée ne permet pas de savoir lequel est allumé. La bonne conduite, prévue par le manuel, serait de demander le code affiché ; le modèle choisit souvent une ligne au hasard. |
| Photo : état sans voyant d'alerte | IMA-05 (tout vert), IMA-21 (message « BATT REPLACE » à l'écran, voyant vert) | L'information utile n'est pas un voyant coloré : le modèle décrit un voyant vert au lieu de conclure à l'état nominal ou de lire l'écran. |
| Photo : modèle non identifié | IMA-27 | Marge CLIP très faible (0,003) et écran non lu : la demande de clarification est prudente mais attendue ici comme réponse. |
| Hallucination sur une information absente | ABS-04 (prix d'un disque), ABS-05 (voyant « violet ») | La question cite un vrai modèle : la recherche trouve des passages proches (score élevé), le seuil d'abstention ne se déclenche pas et le petit modèle invente une réponse au lieu de dire que l'information manque. |
| Mauvaise exploitation de l'historique MCP | MCP-05, MCP-08 | L'historique est correctement récupéré, mais le modèle mélange historique et procédure documentaire (date de remplacement de la batterie confondue avec l'alerte A-18). |
| Raisonnement ou réponse partielle | TEX-21, TEX-38, TEX-36 | Règle de récurrence générale oubliée au profit du cas particulier E-302 ; lecture erronée du bargraphe ; bonne réponse (« non remplaçable à chaud ») mais statut « clarification » au lieu de « répondu ». |
| Faux négatif du correcteur automatique | TEX-10 | La réponse « Vous ne pouvez pas retirer deux disques en même temps » est correcte ; l'expression attendue (« ne pas ») ne couvre pas « ne pouvez pas ». Score laissé inchangé. |

**Corrections apportées pendant l'évaluation** (chaque correction a été suivie d'une nouvelle exécution des scénarios concernés) :
1. Lecture des photos : variantes « façon photo » dans l'index CLIP, prompt `visual_analysis_v2` (le 3B recopiait le gabarit), détecteur de voyants par traitement d'image, alignement couleur détectée / lignes des tableaux.
2. Abstention déterministe quand un code cité (ex. Z-99) n'existe dans aucun document autorisé.
3. Gestion propre d'une panne ou d'un délai dépassé d'Ollama (réponse « non générée » au lieu d'un plantage).
4. Détection d'intention : « Quels tickets **sont** ouverts » n'était pas reconnu (MCP-06 relancé).
5. Détection de doublon de ticket : elle se fondait aussi sur les codes cités dans la réponse ; elle se fonde désormais d'abord sur la demande et la photo (18 scénarios de ticket relancés, tous réussis).

Incidents d'exécution exclus puis relancés : arrêt du serveur Ollama (mémoire disponible faible sur le poste) et mise en veille du PC pendant un appel. Les résultats affectés ont été retirés et les scénarios rejoués.

**Lecture du résultat** : les garanties portées par le code (droits d'accès, approbation humaine, budgets, neutralisation des injections, citations limitées aux preuves récupérées) atteignent 100 %. Les échecs restants viennent de la qualité de génération du modèle 3B sur CPU, surtout pour l'interprétation fine des photos. Le passage à un modèle 7B sur GPU est la première amélioration à évaluer.
