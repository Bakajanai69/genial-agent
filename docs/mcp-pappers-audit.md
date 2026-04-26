# Audit MCP Pappers — état des lieux

> **Date** : 2026-04-25 (review S09.5 post-fix)
> **Source** : ``mcp.pappers.fr`` via streamable-http, ``tools/list``
> (gratuit côté Pappers — 0 crédit consommé pour cet audit).
> **Reproductible** : ``uv run python scripts/audit_mcp_pappers.py``
> (génère un dump JSON complet dans ``traces/S095_mcp_audit_dump.json``).
>
> **Total tools exposés** : **31**
> **Retenus côté agent** (``RETAINED_TOOLS`` de ``mcp_pappers.py``) : **7**
> **Exclus** : **24**

---

## TL;DR

- Pappers MCP unifie **6 services** : Entreprise (core), Conformité,
  Justice, Politique, Territoire, et un volet juridique (codes, lois,
  conventions collectives).
- Notre agent ne couvre que **Entreprise + Conformité** — cohérent avec
  le brief (« info sur une entreprise française »). Les 24 exclus
  appartiennent à des services hors scope.
- **Aucun tool n'a d'``outputSchema``** : impossible pour le LLM de
  prévoir la structure de retour. C'est ce qui motive le Payload Vault
  S09.5 (offload + index générique).
- Sur les 7 retenus, **3 ont des schémas géants** (50 à 161 props) qui
  alourdissent le prompt mais que Pappers a anticipés via des
  instructions négatives explicites dans les descriptions.
- **Anomalie crédits** sur ``comptes-entreprise`` (cf.
  [`pappers-mcp.md`](./pappers-mcp.md) §4.2) : seul tool retenu qui
  refuse les jetons PAYG — bug serveur Pappers, pas de notre côté.
- **1 gap potentiel** : ``informations-entreprise`` (Premium-only) est
  exclu. Couvre l'identité juridique mieux que ``recherche-entreprises``,
  mais nécessite un pack supérieur.

---

## 1. Vue d'ensemble par domaine

| Domaine | Total | Retenus | Exclus | Pourquoi exclus |
|---|---:|---:|---:|---|
| **Entreprise (core)** | 7 | 6 | 1 | `informations-entreprise` Premium-only |
| **Conformité / KYC** | 1 | 1 | 0 | tout retenu |
| **Justice** | 4 | 0 | 4 | service Pappers Justice — hors scope cahier |
| **Politique** | 7 | 0 | 7 | service Pappers Politique — hors scope cahier |
| **Territoire** | 3 | 0 | 3 | service Pappers Territoire — hors scope cahier |
| **Juridique** (codes, lois, conventions collectives) | 5 | 0 | 5 | hors scope cahier (entreprises uniquement) |
| **Immobilier** (cadastre, lieux) | 2 | 0 | 2 | hors scope cahier |
| **Documents transverse** | 2 | 0 | 2 | `lire-documents` + `recherche-amendements` — utilisable mais hors scope U1-U5 |
| **Total** | **31** | **7** | **24** | |

Notre filtre est volontairement strict pour ne pas gonfler le contexte
Claude avec des tools jamais sollicités (cf. [`pappers-mcp.md`](./pappers-mcp.md)
§6 : « ne gardez que ceux dont vous avez réellement besoin pour réduire
la consommation de contexte »).

---

## 2. Tools retenus (7) — analyse détaillée

### 2.1 ``sirenisateur``

> Outils très précis permettant de trouver le SIREN d'une entreprise
> en fonction de son nom. Utiliser UNIQUEMENT quand on ne connaît pas
> encore le SIREN. Utiliser cet outil en priorité pour trouver le siren
> d'une entreprise.

| Critère | Valeur |
|---|---|
| Required | `country_code`, `company_name` |
| Props | 5 (compact) |
| Description claire | ✅ instruction positive + UNIQUEMENT/priorité |
| outputSchema | ❌ |
| Coût observé | **1 crédit** par appel |
| PAYG | ✅ |
| Taille payload | ~250-450 chars |
| Cas d'usage | toujours en pré-requis (résoudre nom → SIREN) |

**Verdict** : excellent tool, description bien écrite avec triggers
explicites. Aucun risque d'usage abusif.

### 2.2 ``recherche-entreprises``

> Recherche des entreprises françaises en utilisant la base de données
> Pappers et plusieurs critères basés sur l'entreprise ou ses
> dirigeants. Les valeurs d'enum return_fields doivent être strictement
> respectées et ne peuvent pas être inventées.

| Critère | Valeur |
|---|---|
| Required | `return_fields` |
| Props | **161** (énorme — filtres entreprise ET dirigeant ET bénéficiaire) |
| Description claire | ⚠ pas d'exemple, ne dit pas quand l'utiliser vs `sirenisateur` |
| outputSchema | ❌ |
| Coût observé | 1 crédit |
| PAYG | ✅ |
| Taille payload | 3-4 K chars (avec `return_fields` filtré) |
| Cas d'usage | U1 (avec champs enrichis), U4 (filtrage par critères) |

**Verdict** : 161 props c'est massif. Le schéma ajoute ~3 K tokens au
prompt agent. Pappers a anticipé le risque d'invention d'enums avec
l'instruction "ne peuvent pas être inventées".

**Découverte review S09.5 post-fix** : ce tool peut retourner
`chiffre_affaires`, `resultat`, `capital`, `effectif`, `annee_finances`
via `return_fields` → **workaround `comptes-entreprise`** pour les
questions "headline" mono-année (cf. [`pappers-mcp.md`](./pappers-mcp.md)
§4.2.1).

### 2.3 ``comptes-entreprise``

> Récupère les comptes annuels d'une entreprise française à partir de
> son numéro SIREN. Retourne les données comptables structurées
> incluant les sections du bilan (liasses), les ratios financiers et
> les métadonnées pour chaque exercice. Utiliser le paramètre annee
> pour filtrer par une ou plusieurs années (ex. "2022" ou
> "2020,2021,2022").

| Critère | Valeur |
|---|---|
| Required | `siren` |
| Props | 2 (compact) |
| Description claire | ✅ avec exemples (ex. "2022" ou "2020,2021,2022") |
| outputSchema | ❌ |
| Coût observé | 2 crédits par appel |
| **PAYG** | ❌ **bug serveur Pappers** (cf. [`pappers-mcp.md`](./pappers-mcp.md) §4.2) |
| Taille payload | **50 K - 706 K chars** (motivation Payload Vault) |
| Cas d'usage | U3 (comparaison multi-années) — **seul tool dépendant** |

**Verdict** : description correcte mais **2 problèmes** :

1. **Bug PAYG** : refuse les jetons PAYG quand abonnement saturé,
   contredisant la doc Pappers. Workaround documenté §4.2.1 +
   pre-warm cache disque §4.3.
2. **Payloads massifs** : jusqu'à 706 K chars (Carrefour Hyper sans
   filtre). Justifie complètement l'architecture Payload Vault
   livrée en S09.5.

### 2.4 ``cartographie-entreprise``

> Récupère les données permettant d'établir une cartographie d'une
> entreprise française à partir de son SIREN. Retourne les noeuds
> (entreprises et personnes) et les liens entre eux, permettant de
> visualiser les relations entre dirigeants, bénéficiaires effectifs
> et entreprises liées.

| Critère | Valeur |
|---|---|
| Required | `siren` |
| Props | 4 (`siren`, `inclure_entreprises_dirigees`, `inclure_entreprises_citees`, `inclure_sci`) |
| Description claire | ✅ explicite sur la structure (noeuds/liens) |
| outputSchema | ❌ |
| Coût observé | 3 crédits par appel |
| PAYG | ✅ |
| Taille payload | 25-45 K chars |
| Cas d'usage | U2 (cartographie dirigeant), U4 bonus (filiales) |

**Verdict** : tool propre, description guide bien. Tarif élevé
(3 crédits) — à utiliser parcimonieusement.

### 2.5 ``recherche-dirigeants``

> Recherche les dirigeants français en fonction de nombreux filtres
> possibles. Le paramètre q est UNIQUEMENT pour rechercher par
> nom/prénom du dirigeant. NE SURTOUT PAS utiliser pour rechercher
> des entreprises. Les valeurs d'enum return_fields doivent être
> strictement respectées et ne peuvent pas être inventées.

| Critère | Valeur |
|---|---|
| Required | aucun |
| Props | 50 (filtres dirigeant + entreprise + bénéficiaire) |
| Description claire | ⚠ pas de required, mais instructions négatives explicites |
| outputSchema | ❌ |
| Coût observé | 1 à 4 crédits (selon `par_page`) |
| PAYG | ✅ |
| Taille payload | 75 K - 225 K chars |
| Cas d'usage | U2 (mandats d'une personne) |

**Verdict** : Pappers a anticipé deux erreurs LLM courantes :

1. **« NE SURTOUT PAS utiliser pour rechercher des entreprises »** —
   évite que l'agent confonde avec `recherche-entreprises`.
2. **« Les valeurs d'enum ne peuvent pas être inventées »** — évite
   les hallucinations de filtres.

C'est de la **bonne ingénierie de prompt côté serveur MCP**, on n'a pas
besoin de redoubler côté agent.

### 2.6 ``conformite-personne-physique``

> Vérifie le statut de personne politiquement exposée (PPE) et la
> présence de sanctions internationales pour une personne physique à
> partir de son nom, prénom et date de naissance.

| Critère | Valeur |
|---|---|
| Required | `nom`, `prenom`, `date_de_naissance` |
| Props | 3 (compact) |
| Description claire | ✅ scope précis (PPE + sanctions) |
| outputSchema | ❌ |
| Coût observé | 0 crédit (gratuit ou null result) |
| PAYG | ✅ |
| Taille payload | minimal (souvent `null`) |
| Cas d'usage | U5 KYC |

**Verdict** : tool simple et bien défini. Hauteur d'usage probablement
faible dans la démo (U5 = stretch).

### 2.7 ``recherche-beneficiaires``

> Recherche les bénéficiaires effectifs français en fonction de
> nombreux filtres possibles. Le paramètre q est UNIQUEMENT pour
> rechercher par nom/prénom du bénéficiaire, surtout pas une
> entreprise. NE PAS utiliser pour rechercher une entreprise.

| Critère | Valeur |
|---|---|
| Required | aucun |
| Props | 50 |
| Description claire | ⚠ pas d'exemple, pas de required |
| outputSchema | ❌ |
| Coût observé | n/a |
| **Accès** | ❌ **nécessite habilitation séparée** (cf. ci-dessous) |

**Verdict** : Pappers refuse l'accès avec
*« L'accès aux bénéficiaires effectifs nécessite une habilitation. Elle
peut être demandée via cette page <https://www.pappers.fr/acces-beneficiaires-effectifs> »*.
Indépendant des crédits — c'est une restriction réglementaire (RGPD /
LCB-FT). Probablement non débloquable pour l'exercice MVP. Ce tool
**reste exposé** dans `RETAINED_TOOLS` mais sera systématiquement
refusé en runtime, ce qui est correctement géré par le pipeline
(`PappersToolError`).

→ **Suggestion** : retirer de `RETAINED_TOOLS` pour économiser ~3 K
tokens de schéma sur chaque tour. À discuter en S09.6 (workaround
tools / cache crédits) ou plus tard.

---

## 3. Tools exclus (24) — synthèse par domaine

### 3.1 Entreprise (core) — 1 exclu

- **`informations-entreprise`** (`siren` required) :
  *« Récupère les informations juridiques générales d'une entreprise
  française à partir de son numéro SIREN. Ne peut pas fournir de
  données historiques. »*

  Initialement identifié **Premium** dans
  [`mcp_pappers.py:107-110`](../src/genial_agent/mcp_pappers.py).

  **Probe live 2026-04-25** (post-souscription) : le tool **refuse
  toujours les jetons PAYG** avec le même message d'erreur que
  `comptes-entreprise` (« crédits suffisants pour exécuter cet
  outil ») et **ne consomme pas de PAYG** (refus instantané).

  Hypothèse : `informations-entreprise` est dans la **même classe** que
  `comptes-entreprise` côté serveur Pappers — soit tous les deux sont
  Premium-only, soit tous les deux sont touchés par le même bug de
  routage PAYG. Diagnostic définitif possible **uniquement après refill
  abonnement** (30/04) — si l'un marche et pas l'autre, on tranche.

  Recommandation : laisser exclu pour l'instant. Re-évaluer avec
  abonnement actif.

### 3.2 Conformité — 0 exclu

Tout retenu (`conformite-personne-physique`).

### 3.3 Justice — 4 exclus (service Pappers Justice)

Hors scope cahier (« entreprises françaises »). Liste pour info :

- `recherche-decisions-justice` : décisions de justice par filtres.
- `details-decision-justice` : détail d'une décision.
- `question-juridique` : note de synthèse IA juridique Pappers.
- `recherche-articles-loi`, `recherche-textes-loi`,
  `details-article-loi`, `sommaire-texte-loi` : codes et conventions
  collectives.

Pertinent uniquement si on étend le scope vers du due diligence
juridique — non demandé par Fabien.

### 3.4 Politique — 7 exclus (service Pappers Politique)

Hors scope cahier. Tous concernent les parlementaires français /
européens, les amendements, les dossiers législatifs, les votes.

- `recherche-acteurs-politiques`, `details-acteur-politique`,
  `recherche-documents-politiques`, `details-document-politique`,
  `recherche-interventions-politiques`, `recherche-amendements`,
  `filtres-amendements`, `recherche-votes`, `cartographie-politique`,
  `details-dossier-politique`.

### 3.5 Territoire — 3 exclus (service Pappers Territoire)

Hors scope cahier. Documents émis par les collectivités locales
(arrêtés, comptes rendus municipaux, délibérations).

- `recherche-documents-territoire`, `details-document-territoire`,
  `document-territoire-pdf`.

### 3.6 Immobilier — 2 exclus (service Pappers Immobilier)

Hors scope cahier.

- `recherche-parcelles` : cadastre.
- `recherche-lieux` : géocodage adresses.

### 3.7 Transverse — 1 exclu

- `lire-documents` (`documentIds` required) : récupère le contenu
  textuel de documents Pappers à partir de tokens. Utile uniquement
  comme follow-up à un autre tool qui retourne des `documentIds` —
  hors flux MVP.

---

## 4. Couverture des cas d'usage U1-U5 (cahier §3)

| Cas | Tools requis | Couverture | Notes |
|---|---|---|---|
| **U1** Fiche identité (LVMH) | `sirenisateur` + `recherche-entreprises` | ✅ | Enrichi avec CA/résultat via `return_fields` (workaround §4.2.1) |
| **U2** Mandats Bernard Arnault | `sirenisateur` + `recherche-dirigeants` | ✅ | Limite : 39 homonymes Arnault à disambiguer (cf. journal G2) |
| **U3** Compare Carrefour vs Casino 3 ans | `sirenisateur` + `comptes-entreprise` | ⚠ | Bug PAYG `comptes-entreprise`. Workaround : `recherche-entreprises` (1 année), pre-warm cache (3 ans). |
| **U4** Recherche par critères | `recherche-entreprises` | ✅ | 161 props couvrent tous les filtres usuels |
| **U5** KYC ponctuel | `sirenisateur` + `conformite-personne-physique` | ✅ | Tool gratuit, retour souvent `null` |

→ **5/5 cas couverts**, dont **1 partiellement (U3)** à cause du bug
serveur Pappers, mitigé par 2 workarounds documentés.

---

## 5. Qualité des descriptions MCP — bilan

### 5.1 Métriques globales

| Métrique | Valeur | Lecture |
|---|---|---|
| Description < 80 chars | 1/31 | ✅ Pappers a soigné les descriptions |
| Pas de paramètre `required` | 9/31 | ⚠ ouvre la porte aux combinaisons floues d'args |
| **Pas d'`outputSchema`** | **31/31** | ❌ **gap structurel** — l'agent doit deviner la shape |
| Description avec exemple (`ex.`/`ex:`/`exemple`) | 5/31 | ⚠ peu d'exemples concrets |

### 5.2 Le gap `outputSchema`

C'est le **seul vrai défaut systémique** du MCP Pappers. Sans
`outputSchema`, le LLM ne sait pas si un tool retourne :

- un dict ou un array
- 1 entité ou N
- un payload de 250 chars ou 700 K chars

Conséquences mesurées dans S09 :

- Findings F1 (troncature `comptes-entreprise`), F2 (cartographie),
  F4 (recherche-dirigeants).
- Motivation directe du Payload Vault S09.5 (offload + index générique
  permet de gérer l'inconnu structurel sans hardcoder).

### 5.3 Bonnes pratiques constatées

- **Instructions négatives explicites** : "NE SURTOUT PAS",
  "UNIQUEMENT", "ne peuvent pas être inventées" — anti-hallucination
  côté serveur.
- **Tool `sirenisateur` qui se déclare prioritaire** : *« Utiliser
  cet outil en priorité pour trouver le siren d'une entreprise »* —
  guide bien le pipeline en 2 étapes (sirenisateur d'abord, puis tool
  spécifique).
- **Exemples dans `comptes-entreprise`** sur le format de `annee` —
  utile pour le LLM.

---

## 6. Recommandations (S09.6 / S09.7)

### 6.1 Court terme (avant démo)

1. **Re-tester `informations-entreprise`** avec le nouvel abonnement
   (1 crédit max) — si débloqué, ça enrichit U1 avec un payload
   compact dédié à l'identité juridique. Décision après test.
2. **Retirer `recherche-beneficiaires` de `RETAINED_TOOLS`** :
   habilitation requise, jamais utilisable, pollue le prompt avec
   3 K tokens de schéma. ~5 lignes de code, 0 régression.
3. **Garder le filtre actuel** sinon : 6 tools retenus + 1 (suite
   suggestion 2) = 6, ce qui est optimal.

### 6.2 Moyen terme

4. **Adapter le system prompt** pour pousser l'agent à utiliser
   `recherche-entreprises` quand `comptes-entreprise` retourne une
   erreur de crédit (fallback explicite, cf. workaround §4.2.1).
   Couvert par la story
   [`S09.6`](./stories/S09.6-mcp-workaround-and-credit-cache.md).
5. **Documenter la stratégie multi-tier** : Premium tools
   (`informations-entreprise`, peut-être autres) à activer plus tard
   avec un pack supérieur.

### 6.3 Hors scope MVP

6. Si extension scope vers Justice / Politique : 14+ tools
   mobilisables, mais c'est un autre produit.

---

## 7. Annexes

- **Dump JSON brut** des 31 tools :
  `traces/S095_mcp_audit_dump.json` (gitignoré).
- **Script reproductible** :
  [`scripts/audit_mcp_pappers.py`](../scripts/audit_mcp_pappers.py).
- **Probe matrice PAYG** :
  [`scripts/probe_payg_compatibility.py`](../scripts/probe_payg_compatibility.py).
- **Probe alternatives `comptes-entreprise`** :
  [`scripts/probe_comptes_entreprise_alternatives.py`](../scripts/probe_comptes_entreprise_alternatives.py).
