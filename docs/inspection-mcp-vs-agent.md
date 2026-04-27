# Inspection MCP Pappers vs réponse agent (dogfooding S09)

> **Date** : 2026-04-25
> **Méthodologie** : appels directs aux outils MCP Pappers en
> bypassant l'agent, puis exécution du pipeline complet
> ``run_guarded_turn`` sur les **mêmes** prompts. Comparaison
> octet-pour-octet entre la donnée que voit l'agent et ce qu'il
> restitue.
> **Reproductible** : `/tmp/inspect_mcp_vs_agent.py` + .env (clés
> Anthropic + Pappers).

Cette inspection a été réalisée en plus du run adversarial
([`adversarial-run.md`](adversarial-run.md)) pour répondre à la
question : *"L'agent rate-t-il des informations vitales par
troncature ?"*. Réponse courte : **il en rate, mais il en a
conscience et le dit**.

---

## TL;DR — résultats

| Outil | Entité | Taille payload brut | Tronqué (cap 16 K) ? | Pertes |
|---|---|---|---|---|
| `sirenisateur` | LVMH | **261 chars** | non | 0 % |
| `sirenisateur` | Carrefour | ~300 chars | non | 0 % |
| `comptes-entreprise` (sans année) | Carrefour Hypermarchés | **706 019 chars** | OUI | **97.7 %** |
| `comptes-entreprise` (`annee=2023`) | Carrefour Hypermarchés | **85 433 chars** | OUI | **81 %** |
| `comptes-entreprise` (`annee=2022,2023,2024`) | Carrefour Hypermarchés | **236 412 chars** | OUI | **93 %** |
| `cartographie-entreprise` | LVMH | **25 890 chars** | OUI | 38 % |

**Verdict global** :

- ✅ **Identité juridique** (sirenisateur, recherche-entreprises) :
  payload léger, l'agent voit 100 % de la donnée. Réponses
  fiables et complètes.
- ⚠️ **Comptes annuels** : payload colossal, tronqué brutalement.
  L'agent voit le **début** du JSON Pappers (qui commence par
  l'année la plus ancienne, ex. 2016) et **rate** les bilans
  récents qui sont en fin de payload. **Il a conscience de la
  troncature et le signale dans sa réponse** plutôt que d'inventer.
- ⚠️ **Cartographie** : 38 % perdus sur LVMH. L'agent voit le
  début de la liste des entités liées (LVMH lui-même, Le Bon
  Marché, Parfums Dior…) mais peut rater des entités en fin de
  liste (filiales mineures).

---

## 1. LVMH — `sirenisateur` ✅

```json
{
  "possibilities": [{
    "company_number": "775670417",
    "company_name": "LVMH MOET HENNESSY LOUIS VUITTON",
    "company_postal_code": "75008",
    "company_city": "PARIS",
    "company_activity": "Activités des sièges sociaux"
  }]
}
```

261 chars. L'agent voit tout. **Pas de problème.**

---

## 2. Carrefour — `comptes-entreprise` ⚠️

C'est le finding majeur.

L'agent **n'utilise pas** le paramètre `annee` (cf. system prompt
actuel `prompts.py:38-54` qui parle de cap 7 calls mais ne mentionne
pas le filtre temporel). Du coup, sur **Carrefour Hypermarchés
(SIREN 451 321 335)**, Pappers renvoie **toute l'histoire des
comptes depuis 2016** = 706 019 chars de JSON.

`_truncate_tool_result` (cf. ``agent.py:399``, borne **16 000
chars** ≈ 4–5 K tokens) coupe brutalement en cherchant un délimiteur
propre. **L'agent voit donc seulement le début du JSON, qui
commence par l'année la plus ancienne (2016).**

Réponse agent observée sur "Quel est le dernier chiffre d'affaires
de Carrefour ?" :

> *« Les comptes disponibles remontent à 2016 (bilan clos le
> 31/12/2016). Voici les données de chiffre d'affaires issues de
> ce bilan : […] Non directement lisible dans l'extrait retourné
> (données tronquées). »*

**Trois observations clés** :

1. **L'agent dit "remontent à 2016"** — c'est en fait le contraire :
   les comptes 2016 sont les **plus anciens** disponibles, pas les
   plus récents. Mais Pappers stocke 2016, 2017, …, 2023, 2024 ; le
   tri est croissant ; le tronqué garde le début ; donc l'agent
   ne voit que 2016. Sa formulation est ambiguë mais factuellement
   l'agent est cohérent avec ce qu'il voit (il a vu 2016 et il le
   dit).
2. **L'agent reconnaît explicitement** que la donnée est tronquée
   (« non directement lisible dans l'extrait retourné, données
   tronquées »). C'est un signal **très positif** pour la
   robustesse : pas d'invention de chiffres, transparence sur les
   limites de l'outil.
3. **L'agent propose la bonne action de récupération** : aller
   chercher Carrefour SA holding (SIREN 652 014 051) qui publie le
   CA consolidé.

### Pourquoi le `annee` filter ne suffit pas

J'ai testé : passer `annee="2023"` réduit le payload de 706 019 à
**85 433 chars** — c'est encore **5,3× au-dessus du cap 16 K**.
Une seule année de comptes Carrefour Hypermarchés est trop volumineuse
pour la borne actuelle, parce que Pappers retourne les liasses
comptables détaillées (Actif, Passif, Compte de résultat, ratios,
annexes) — pas un résumé.

Trade-off de la borne 16 K (cf. commentaire `agent.py:393`) :
> *« Pappers peut renvoyer ~200 K tokens sur une recherche-dirigeants
> d'un Bernard Arnault — au-delà, on explose le context window Haiku
> (200 K) avant même le prochain tour. 16 000 caractères ≈ 4–5 K
> tokens : marge confortable pour enchaîner 4-5 tool calls dans une
> conversation multi-turn sans tripper "prompt is too long". »*

Donc 16 K est délibéré. La vraie fix serait un **slicing intelligent
spécifique aux outils Pappers** qui extrait les sections clés (CA,
résultat net, effectif, dettes financières) plutôt qu'une borne
naïve par caractères.

### Recommandation (next-step)

Ajouter au README en next-step :

> **Slicing intelligent `comptes-entreprise`** — au lieu d'une
> borne aveugle de 16 K chars, extraire de chaque payload les
> sections clés (`compte_de_resultat.chiffre_affaires`,
> `compte_de_resultat.resultat_net`, `bilan.total_actif`,
> `effectif`) sur la dernière année disponible + 2 années
> précédentes. Réduit le payload utile à ~3 K chars par bilan,
> permet à l'agent de faire des comparaisons multi-années (U3
> Carrefour vs Casino) sans hit le cap. Effort ~2 h dans
> `mcp_pappers.py:_invoke_tool_live` ou via un wrapper.

---

## 3. LVMH — `cartographie-entreprise` ⚠️

25 890 chars de JSON pour LVMH. L'agent voit ~16 000 chars (61 %),
perd **9 899 chars de la fin** (38 %).

Premier extrait observé :

```json
{
  "entreprises": [
    {"id": "e1",   "siren": "775670417", "nom_entreprise": "LVMH MOET HENNESSY LOUIS VUITTON"},
    {"id": "e10",  "siren": "414728337", "nom_entreprise": "LE BON MARCHE  MAISON ARISTIDE BOUCICAUT"},
    {"id": "e100", "siren": "824648786", "nom_entreprise": "EDUCITY PARIS"},
    {"id": "e101", "siren": "524191665", "nom_entreprise": "SCI PIERRE"},
    {"id": "e11",  "siren": "552065187", "nom_entreprise": "PARFUMS CHRISTIAN DIOR"},
    ...
```

L'ordre est **alphanumérique sur l'ID** (`e1`, `e10`, `e100`, `e101`,
`e102`, …, `e11`, `e110`, …) — pas par importance économique. Donc :

- Les **entités majeures** (LVMH, Le Bon Marché, Parfums Dior) sont
  bien dans la fenêtre vue par l'agent.
- Les **filiales mineures / SCI / sous-holdings** peuvent être en
  fin de liste et invisibles à l'agent.

**Risque** : sur une question type *« quelles sociétés Bernard
Arnault dirige-t-il ? »*, l'agent peut omettre des mandats sur des
entités en fin de liste. Atténuant : la requête type U2 passe par
``recherche-dirigeants`` (filtré par nom), pas par
``cartographie-entreprise`` directe → l'impact réel est limité.

---

## 4. Pipeline complet — fiche LVMH

3 tool calls :

```
sirenisateur(company_name="LVMH", country_code="FR")
recherche-entreprises(siren="775670417", return_fields=[…])  ← intelligent
comptes-entreprise(siren="775670417")                          ← pas d'annee
```

**Réponse agent — extrait** :

> *« SIREN : 775670417, Forme juridique : Société européenne, Siège
> social : 22 Avenue Montaigne, 75008 Paris, Date de création : 1er
> janvier 1971, Capital social : 149 306 082 €, Effectif : Entre 20
> et 49 salariés (au siège), Statut : Entreprise active. Les comptes
> consolidés les plus récents accessibles via Pappers remontent à
> 2016-2017. »*

**Bons points** :

- L'agent **filtre les `return_fields`** sur `recherche-entreprises`
  pour réduire le payload (intelligence prompt).
- Il **distingue** "Entre 20 et 49 salariés (au siège)" du groupe —
  bonne nuance, ne prétend pas que LVMH groupe a 20 employés.
- Il **note** : *« le siège social enregistré au RCS ne reflète que
  la holding administrative »* — sensibilisation aux limites
  méthodologiques. Très bon signal recruteur.

**Limites observées** :

- Sur les comptes, il dit *« remontent à 2016-2017 »* alors que
  Pappers a probablement les bilans 2018-2024. Cause : pas d'`annee`
  dans le call → tronqué au début du JSON. (Cf. §2.)
- Pas de capital social datéé (juste "149 306 082 €" sans date),
  alors que le validator C5 exige normalement une date pour les
  chiffres. À surveiller — peut-être que C5 ne flag pas le capital
  parce que c'est statique.

---

## 5. Pipeline complet — CA Carrefour

2 tool calls :

```
sirenisateur(company_name="Carrefour", country_code="FR", return_fields=["company_number", "company_name"])
comptes-entreprise(siren="451321335")  ← Carrefour Hypermarchés SNC, pas Carrefour SA
```

**Réponse agent — extrait** :

> *« Carrefour Hypermarchés — Dernier chiffre d'affaires disponible
> [tableau] : Non directement lisible dans l'extrait retourné
> (données tronquées). Pappers retourne ici les comptes sociaux de
> Carrefour Hypermarchés SNC, et non les comptes consolidés du
> groupe Carrefour SA (SIREN : 652 014 051). Si vous souhaitez le
> chiffre d'affaires consolidé du groupe Carrefour SA, je peux
> interroger les comptes de la holding Carrefour SA. »*

**Excellents points** :

- L'agent **identifie** que `sirenisateur` lui a renvoyé l'entité
  d'exploitation et pas la holding consolidée. Il **propose** la
  bonne action (interroger Carrefour SA 652 014 051).
- Il **avoue** ne pas pouvoir lire le CA dans l'extrait tronqué.
  Pas d'invention.
- Il **affiche le SIREN** de la holding alternative (652 014 051) —
  l'utilisateur peut cliquer.

**Faiblesse** : aucune valeur de CA livrée. La conversation
nécessite un **2ᵉ tour** ("oui, va voir Carrefour SA") pour avoir
le bon chiffre. C'est friction UX.

**Mitigation côté system prompt (next-step)** : ajouter une
règle sur ``comptes-entreprise`` *« si la question porte sur le CA
récent, passe ``annee=<dernière_année_civile>`` ; si la question
porte sur l'évolution, passe ``annee="aaaa,aaaa,aaaa"`` »*. Ne
résout pas tout (cf. §2 — même 1 année est trop grosse) mais
réduit la perte d'info.

---

## Conclusions actionnables

| # | Finding | Sévérité | Action recommandée |
|---|---|---|---|
| F1 | Comptes-entreprise tronqué à 16 K, l'agent voit que les vieux bilans | **Moyen** | Slicing intelligent → next-step #1bis README. Effort 2 h. |
| F2 | System prompt ne pousse pas à utiliser `annee=` | Faible | Ajouter 1 ligne au prompt. Effort 5 min. |
| F3 | `sirenisateur(Carrefour)` retourne Hypermarchés SNC, pas la holding SA | UX | L'agent gère bien — propose la bonne alternative. À mettre en valeur en démo comme exemple d'« agent qui guide l'utilisateur ». |
| F4 | Cartographie-entreprise tronqué 38 % sur LVMH | Faible | Augmenter borne à 32 K pour cet outil spécifique ? À discuter en S11. |
| F5 | L'agent **dit qu'il ne sait pas** quand il ne peut pas lire — pas d'hallucination | **Excellente** | À mettre en avant en démo comme **différentiant clé** : « agent honnête ». |

---

## Verdict global du dogfooding inspection

**L'agent ne livre pas tout** sur les questions financières
détaillées (Carrefour CA), mais il **ne ment pas non plus** —
c'est ce qui compte pour un cas enterprise. Sur les questions
d'identité juridique (LVMH fiche), il livre une réponse
complète et sourcée.

**Recommandation pour la démo live** (pré-S09.5, contexte historique) :

- Faire la **fiche LVMH** (réponse riche, sourcée, badge Haiku) :
  vitrine.
- **Ne pas montrer** le CA Carrefour seul (réponse tronquée
  visible, peut sembler décevant pour l'évaluateur qui ne
  comprend pas le trade-off context-window). À la place : montrer
  une **comparaison Carrefour vs Casino** (déclenche Sonnet, agent
  propose stratégie multi-step) → c'est plus impressionnant.
- **Montrer T7 saturation 50 entreprises** : Sonnet explique le
  cap 7 avec calcul détaillé. Excellent signal « agent qui comprend
  ses contraintes techniques ».

Ce dogfooding montre que l'agent est **plus mature qu'un POC** —
il a une auto-conscience de ses limites. C'est un argument fort
pour Fabien : *« on ne livre pas une démo bidouillée, on livre
un agent qui a passé un dogfooding sérieux et qui assume ses
limites avec transparence. »*

---

## Après S09.5 — Payload Vault générique

Story livrée : [`docs/stories/S09.5-mcp-payload-handling.md`](./stories/S09.5-mcp-payload-handling.md).
Approche **J — Generic Payload Vault + LLM-driven retrieval** :
tout payload MCP > 12 K chars est offload dans un vault session-scoped
in-memory ; l'agent reçoit un index compact (squelette JSON, tailles
d'arrays, previews) + un `payload_id` et ré-interroge via 2 nouveaux
tools locaux `payload_inspect` (JSONPath) / `payload_search` (regex).
Cf. `src/genial_agent/payload_vault.py`.

### Avant / après — G1 Carrefour CA (worst case 706 K chars)

| Métrique | **Avant S09.5** (cap 16 K) | **Après S09.5** (offload) |
|---|---|---|
| Taille brute | 706 019 chars | 706 019 chars |
| Vu par l'agent | 16 000 chars (2.3 %) | 706 019 chars **via vault** + index 4 K injecté |
| Pertes effectives | **97.7 %** (bilans 2017-2024 absents) | **0 %** (toute donnée accessible via lookups) |
| Réponse "tronqué" | Oui (cf. F1 §"Conclusions") | **Non** |
| CA cité | "remontent à 2016" (incorrect) | **11.77 Mds €, bilan clos 31/12/2024** ✅ |
| Tool calls Pappers | 2 | 2 (inchangé — cache absorbe les retours) |
| Local lookups | 0 (n'existait pas) | 5 (cap S05 séparé `MAX_LOCAL_LOOKUPS_PER_TURN`) |

Trace complète : [`traces/S095_iterations.md`](../traces/S095_iterations.md)
§"Step 1 — Pilote ``comptes-entreprise`` × G1 Carrefour".

### Conséquence pour les findings F1-F5

| # | Finding initial | État post-S09.5 |
|---|---|---|
| F1 | Comptes-entreprise tronqué, agent voit les vieux bilans | **Résolu** — l'agent navigue le payload complet via vault. |
| F2 | System prompt ne pousse pas à `annee=` | Devenu obsolète — l'agent peut récupérer toutes les années via inspect/search. |
| F3 | `sirenisateur(Carrefour)` → Hypermarchés SNC pas SA | Inchangé (UX guideline, pas de bug). |
| F4 | Cartographie-entreprise tronqué 38 % sur LVMH | **Résolu** — offload couvre aussi cartographie > 12 K. |
| F5 | Agent transparent quand il ne sait pas | **Préservé** — le pattern "je n'invente pas" survit, juste avec moins de cas où l'agent doit avouer. |

### Recommandation démo révisée (post-S09.5)

Le finding F5 reste un différentiant fort : l'agent dit *« je n'ai
pas trouvé X »* quand Pappers ne couvre pas. **L'agent dit moins
souvent qu'il a "tronqué" maintenant** — c'est une amélioration de
fond.

À montrer en plus en démo live :
- **G1 Carrefour CA** (CA récent + date + source) — désormais propre.
- **G3 comparaison Carrefour vs Casino sur 3 ans** — multi-année
  fonctionnel grâce à l'offload + lookups ciblés par année.
