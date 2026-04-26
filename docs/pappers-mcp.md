# Pappers MCP — Garde-fous techniques

Document de référence sur **ce que peut ou ne peut pas faire** le MCP Pappers, et
sur la manière de l'intégrer correctement. Source : documentation officielle
Pappers. Ce document est la seule source de vérité technique pour le
développement de l'agent.

---

## 1. Ce qu'est le MCP Pappers

Le MCP (Model Context Protocol) Pappers est un **serveur MCP distant** qui
expose les données Pappers (entreprises françaises, dirigeants, bilans, actes,
jurisprudences, etc.) à un client MCP (Claude, ChatGPT, Le Chat, HuggingChat,
Perplexity, ou tout SDK agent compatible MCP).

L'agent ne requête jamais l'API REST Pappers directement. Tous les accès aux
données Pappers passent par le serveur MCP.

---

## 2. Endpoint et authentification

### URL unique
```
https://mcp.pappers.fr/{votre-cle-api}
```

- La clé API est **incluse dans le path de l'URL** (pas dans un header, pas
  dans un body).
- **Aucune authentification supplémentaire** n'est requise (pas de Bearer, pas
  d'OAuth).
- La clé API se récupère sur `pappers.fr` → Mon compte → Mon API.

### Conséquences sécurité (à respecter dans l'implémentation)

- **Ne jamais logguer** l'URL complète du MCP : elle contient le secret.
- **Ne jamais commit** la clé. Toujours via variable d'environnement
  (`PAPPERS_API_KEY`) lue au runtime ; construire l'URL côté serveur
  uniquement.
- **Ne jamais exposer** l'URL au client (navigateur). L'appel MCP doit être
  server-side.
- En cas de fuite : régénérer la clé dans l'espace Pappers.

---

## 3. Transport : HTTP Streamable uniquement

> **Note technique officielle** : le serveur MCP Pappers utilise uniquement le
> transport Streamable HTTP. STDIO et SSE ne sont pas supportés — utilisez
> toujours le type `http` / `streamable-http`.

Conséquences :

- **Pas de subprocess local** à lancer (contrairement à beaucoup de MCP
  développés en Node/Python qui tournent en STDIO).
- Dans le Claude Agent SDK / OpenAI Agents SDK, il faut configurer le MCP
  comme un **remote HTTP MCP server** (type `streamable-http`), pas
  `stdio`.
- Dans les configs JSON Claude Desktop / Claude Code, utiliser la forme
  `"type": "http"` avec `"url"` et non `"command" + "args"`.

### Exemple de config (Claude Desktop / Claude Code)
```json
{
  "mcpServers": {
    "pappers": {
      "type": "http",
      "url": "https://mcp.pappers.fr/${PAPPERS_API_KEY}"
    }
  }
}
```

### Exemple Claude Agent SDK (Python / TS)
Passer `transport: "streamable-http"` + `url` au client MCP, pas de stdio.

---

## 4. Modèle économique : crédits

Pappers fonctionne par **crédits**, pas par abonnement API classique.

- **Pack mensuel** : crédits remis à jour chaque mois. Idéal pour usage
  régulier.
- **Pay-As-You-Go** : jetons sans date d'expiration, plus coûteux au jeton.
- Suivi consommation : `moncompte.pappers.fr/credits`.

### Conséquences pour l'agent

- **Chaque appel d'outil consomme des crédits** (le coût exact dépend de
  l'outil invoqué côté Pappers).
- En dev et pour les tests de démo, **cacher agressivement** les réponses sur
  les entités connues (LVMH, BNP, Carrefour, etc.) pour éviter de cramer des
  crédits lors des itérations.
- Limiter le nombre d'outils activés (cf. §6) pour éviter que l'agent
  n'enchaîne des appels non nécessaires.
- Ajouter un **garde-fou applicatif** : max N appels Pappers par conversation
  utilisateur (ex : N=10), au-delà on force une synthèse.

### 4.1 Coût par tool (probe live 2026-04-25)

| Tool MCP | Coût observé (crédits/appel) |
|---|---:|
| `sirenisateur` | 1 |
| `recherche-entreprises` | 1 |
| `comptes-entreprise` | 2 |
| `cartographie-entreprise` | 3 |
| `recherche-dirigeants` | 1 à 4 (selon `par_page`) |
| `conformite-personne-physique` | 0 (gratuit côté observation) |
| `recherche-beneficiaires` | nécessite **habilitation séparée** (hors scope) |

Implication : un tour U3 typique (2 `sirenisateur` + 2
`comptes-entreprise`) coûte **6 crédits**, pas 4. Bien dimensionner les
caps applicatifs à partir de cette base.

### 4.2 Anomalie de routage PAYG ↔ abonnement (2026-04-25)

La doc Pappers indique que les jetons Pay-As-You-Go **doivent prendre
le relais** quand le pack abonnement est saturé :

> *"Pay as You Go credits can take over in case the subscription
> credits are entirely consumed to ensure continuity of your services."*

**Probe live 2026-04-25** (cf.
[`scripts/probe_payg_compatibility.py`](../scripts/probe_payg_compatibility.py))
sur les 7 tools retenus, abonnement à 0/500, PAYG = 94 :

| Tool | PAYG accepté ? | Comportement |
|---|---|---|
| `sirenisateur` | ✅ | débit normal sur PAYG |
| `recherche-entreprises` | ✅ | débit normal sur PAYG |
| `cartographie-entreprise` | ✅ | débit normal sur PAYG |
| **`comptes-entreprise`** | ❌ | **refuse l'appel** ("crédits insuffisants") sans débiter PAYG |
| `recherche-dirigeants` | ✅ | débit normal sur PAYG |
| `conformite-personne-physique` | ✅ | débit normal sur PAYG |
| `recherche-beneficiaires` | n/a | bloqué par habilitation indépendamment des crédits |
| **`informations-entreprise`** (exclu) | ❌ | **même comportement** que `comptes-entreprise` (probe 2026-04-25) |

→ **2 tools côté Entreprise présentent l'anomalie** :
``comptes-entreprise`` et ``informations-entreprise``. Hypothèse :
ces tools "lourds" côté Pappers ont leur propre routage qui ne consulte
pas les jetons PAYG (soit bug, soit politique Premium implicite). Tous
les autres tools acceptent les PAYG comme prévu par la doc. À signaler
au support
([[email protected]](mailto:[email protected]))
pour fix serveur. En attendant : voir §4.3.

### 4.2.1 Workaround applicatif : `recherche-entreprises` à la place de `comptes-entreprise`

Probe live 2026-04-25 (cf.
[`scripts/probe_comptes_entreprise_alternatives.py`](../scripts/probe_comptes_entreprise_alternatives.py))
sur 3 entités : `recherche-entreprises` avec
`return_fields=["chiffre_affaires", "resultat", "capital", "effectif",
"annee_finances", "annee_effectif"]` et `siren` ciblé retourne les
chiffres financiers **headline** de l'entité.

| Entité | CA (€) | Résultat (€) | Année |
|---|---:|---:|---:|
| LVMH (SIREN 775670417) | 651 000 000 | +9 587 500 000 | 2024 |
| Carrefour Hyper (451321335) | 11 770 276 417 | −408 055 834 | 2024 |
| Casino Guichard (554501171) | 98 000 000 | −2 231 000 000 | 2024 |

**Couverture** : CA + résultat + capital + effectif + année courante,
**accepté en PAYG** (1 crédit/appel). Suffit à répondre aux questions
"quel est le CA actuel" / "résultat net 202X" sans appeler
`comptes-entreprise` (PAYG-KO).

**Limites** :

- Pas d'historique multi-années dans un seul appel — pour comparer
  N années (U3 cahier), il faut `comptes-entreprise` (jetons abo
  uniquement) ou pré-warmer le cache disque (cf. §4.3).
- Pas de bilans détaillés (liasses fiscales) — juste les "headline".
- Retourne les **comptes sociaux de l'entité juridique précise**, pas
  les comptes consolidés du groupe (LVMH SA = 651 M€ vs LVMH groupe
  consolidé ~84 Md€). À utiliser sur le SIREN ciblé approprié.

### 4.3 Workaround : cache disque persistant (S09.5 post-fix)

Pour blinder une démo qui dépend de `comptes-entreprise` malgré le bug
de routage PAYG :

1. **Cache MCP persistant** sur disque ([`mcp_cache.py`](../src/genial_agent/mcp_cache.py)) :
   activable via la variable d'env `MCP_CACHE_PERSIST_PATH`
   (e.g. `data/mcp_cache.json`). TTL bumpé 24 h → 7 jours pour
   absorber les fenêtres de blocage.
2. **Pre-warm** ([`scripts/prewarm_persistent_cache.py`](../scripts/prewarm_persistent_cache.py)) :
   lit les traces existantes pour extraire les `(tool, args)` connus,
   ré-exécute les appels PAYG-compatibles → cache disque rempli.
   Skipper automatiquement les appels `comptes-entreprise` (PAYG-KO).
3. **Pre-warm `comptes-entreprise`** : à relancer le 30/04 au refill
   abonnement Pappers (les jetons abo couvrent ce tool, eux). Une fois
   en cache disque, le tool sert depuis le cache pendant 7 jours, sans
   ré-appel live.
4. **Mode dégradé** ([`credit_guard.degraded()`](../src/genial_agent/observability/credit_guard.py))
   peut être basculé manuellement avant la démo si on veut forcer le
   cache-only et éviter tout appel live.

---

## 5. Déclenchement : mention explicite "via Pappers"

La doc insiste sur ce point :

> Mentionnez toujours "via Pappers" ou "en utilisant Pappers" dans vos
> questions pour activer le connecteur. Vous pouvez également mentionner dans
> votre prompt system ou dans vos skills, l'utilisation prioritaire de
> Pappers pour forcer votre IA à utiliser le MCP Pappers en priorité.

### Conséquences pour l'agent

- Notre agent étant **spécialisé entreprises françaises**, on force l'usage
  prioritaire du MCP Pappers dans le **system prompt** dès que la requête
  utilisateur mentionne une entreprise, un dirigeant, un SIREN/SIRET, ou un
  secteur.
- L'utilisateur final n'a pas à écrire "via Pappers" — c'est le rôle du
  system prompt de l'imposer.
- Fallback explicite : si Pappers ne retourne rien (entité inconnue, crédits
  épuisés, erreur réseau), l'agent doit le dire clairement plutôt
  qu'halluciner.

---

## 6. Sélection des outils exposés

La doc précise :

> La plupart des clients MCP permettent d'activer ou de désactiver
> individuellement les outils exposés par le serveur : ne gardez que ceux dont
> vous avez réellement besoin pour réduire la consommation de contexte et
> améliorer la pertinence des réponses.

### Conséquences

- Au premier run de l'agent, **logguer la liste des outils MCP exposés** par
  Pappers (`list_tools` côté client MCP) pour auditer.
- Ne conserver que les outils nécessaires aux cas d'usage ciblés (cf. §7).
- Les outils non activés ne consomment ni tokens de prompt, ni crédits
  Pappers.

---

## 7. Cas d'usage supportés (officiels)

La doc Pappers liste les usages qui fonctionnent bien sur le MCP :

| Domaine | Exemple |
|---|---|
| Due diligence | Fiche complète d'une société avant RDV |
| M&A / Investissement | Filtrer entreprises par secteur + critères financiers |
| Recherche juridique | Derniers arrêts sur une question de droit |
| Comptabilité & Finance | Comparer CA, résultats, bilans multi-exercices |
| Journalisme | Cartographier groupes et liens entre dirigeants |
| Conformité / KYC | Vérification et avis d'entrée en relation |
| Immobilier & Notariat | Vérification identité juridique SCI, pouvoir de signature |
| Banque & Assurance | Évaluation solidité et probabilité de défaillance |
| Prospection | Entreprises similaires + contacts dirigeants |

Ces usages constituent le **périmètre recommandé** pour l'agent.

### Hors périmètre
- Entreprises non françaises (Pappers = données françaises).
- Données non accessibles publiquement ou non couvertes par Pappers (RH
  internes, CRM, etc.).
- Mise à jour / écriture : le MCP est **lecture seule**.

---

## 8. Tests de validation officiels

La doc fournit 3 questions de test pour valider la connexion :

1. "Via Pappers, donne-moi la fiche de la société LVMH" → SIREN, siège,
   dirigeants, forme juridique.
2. "Qui sont les dirigeants actuels de BNP Paribas selon Pappers ?" → Liste
   des représentants légaux + rôles.
3. "Quel est le dernier chiffre d'affaires de Carrefour sur Pappers ?" →
   Données financières issues des derniers bilans.

Ces 3 tests forment notre **smoke test** minimal. Le CI / script de santé les
exécute avant chaque démo.

---

## 9. Limites et erreurs courantes (doc officielle)

| Symptôme | Cause probable | Remède |
|---|---|---|
| "IA ne reconnaît pas Pappers" | URL MCP avec espace ou caractère en trop | Recopier proprement, valider le format |
| "Je n'ai pas accès à Pappers" | Trigger word absent | Ajouter "via Pappers" ou forcer via system prompt |
| Erreur de connexion | Mauvais type de transport | Forcer `http` / `streamable-http` (pas SSE ni STDIO) |
| Option MCP introuvable | Plan insuffisant côté client (ex: ChatGPT) | Sans impact pour nous (on utilise l'API Claude directement) |

---

## 10. Do / Don't — récap pour l'implémentation

### Do
- Lire `PAPPERS_API_KEY` depuis une variable d'environnement.
- Construire l'URL MCP côté serveur uniquement.
- Utiliser le transport `streamable-http`.
- Forcer l'usage prioritaire de Pappers dans le system prompt de l'agent.
- Logguer la liste des tools exposés au démarrage, les filtrer.
- Capper le nombre d'appels Pappers par conversation.
- Cacher les réponses sur entités fréquentes en dev.
- Afficher les sources Pappers (SIREN, dates de bilan) dans la réponse finale.

### Don't
- Ne pas logguer l'URL complète du MCP (fuite de clé).
- Ne pas tenter STDIO ou SSE — non supportés.
- Ne pas appeler l'API REST Pappers en parallèle : tout passe par le MCP.
- Ne pas exposer l'URL MCP côté client navigateur.
- Ne pas hallucinier : si Pappers ne répond pas, le dire.
- Ne pas commit la clé, ni dans `.env`, ni dans un test, ni dans un commentaire.

---

## 11. Références

- Page d'accueil : `pappers.fr`
- Espace API : `pappers.fr` → Mon compte → Mon API
- Suivi crédits : `moncompte.pappers.fr/credits`
- Transport : Streamable HTTP uniquement (pas SSE, pas STDIO)
- Endpoint : `https://mcp.pappers.fr/{votre-cle-api}`
