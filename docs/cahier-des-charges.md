# Cahier des charges — Agent IA "Fiche entreprise" via MCP Pappers

Document unique de spécification pour l'exercice d'évaluation AI Builder.
Source de vérité produit + technique. Toute décision doit être cohérente avec
ce document ou le modifier explicitement.

Voir aussi : [`docs/pappers-mcp.md`](./pappers-mcp.md) — garde-fous techniques
Pappers.

---

## 1. Contexte

- **Cadre** : exercice d'évaluation pour une embauche AI Builder dans une
  start-up française.
- **Brief reçu** : "Un agent IA qui donne de l'info sur une entreprise en
  utilisant le MCP Pappers fraîchement sorti. Pas de contrainte UX, pas de
  contrainte techno. Livrable : un agent testable + brève explication des
  choix de techno."
- **Délai** : ~48h sur un week-end, avec contraintes familiales.
- **Public évaluateur** : équipe technique / produit d'une start-up IA
  française, probablement familière de l'écosystème agent en 2026 (MCP,
  Claude Agent SDK, etc.).

---

## 2. Vision produit

Un agent conversationnel spécialisé sur les **entreprises françaises**. Il
doit répondre, en langage naturel, à toute question métier qui peut se
résoudre à partir des données Pappers (identité juridique, dirigeants,
bilans, actes, liens inter-entités), en enchaînant intelligemment les
outils MCP exposés.

**Promesse en une phrase** : *"Pose une question sur une boîte française,
obtiens une réponse sourcée et structurée en quelques secondes."*

### Ce que l'agent n'est pas
- Pas un chatbot généraliste.
- Pas un CRM, pas un outil de prospection à grande échelle.
- Pas un moteur d'écriture / mise à jour sur Pappers (lecture seule).
- Pas un analyste financier qui fabrique des ratios absents des bilans.

---

## 3. Cas d'usage ciblés (MVP)

Trois scénarios représentatifs issus directement de la doc Pappers, à
supporter de bout en bout :

| # | Cas d'usage | Exemple utilisateur | Attendu |
|---|---|---|---|
| U1 | Fiche d'identité | "Donne-moi la fiche de LVMH" | SIREN, siège, forme juridique, dirigeants principaux, effectif, activité |
| U2 | Cartographie de dirigeant | "Quelles sociétés Bernard Arnault dirige-t-il ?" | Liste des mandats en cours, rôle, entités liées |
| U3 | Comparaison / due diligence | "Compare santé financière Carrefour vs Casino sur 3 ans, lequel présente le moins de risque ?" | Tableau CA / résultat / tendance, verdict sourcé, garde-fou sur données manquantes |

**Stretch (si temps dispo)** :
- U4 : Recherche par critères ("entreprises du secteur X, CA > Y, croissance > Z").
- U5 : Vérification KYC rapide ("avis sur l'entrée en relation avec cette SCI").

U1 à U3 sont **obligatoires** pour la démo. U4/U5 sont un bonus.

---

## 4. Capacités fonctionnelles de l'agent

L'agent doit être capable de :

- **Comprendre une requête en français naturel** mentionnant une
  entreprise par nom, SIREN ou secteur.
- **Appeler le MCP Pappers** via son client MCP pour récupérer les
  données pertinentes (fiches, bilans, dirigeants, actes).
- **Chaîner plusieurs appels MCP** quand la question l'exige (ex : récupérer
  l'entreprise → puis ses dirigeants → puis les autres mandats de ces
  dirigeants).
- **Refuser clairement** hors périmètre (entreprise non-française, données
  non couvertes par Pappers, question sans rapport).
- **Expliciter ses sources** : chaque donnée citée doit être rattachable à
  une entité Pappers (SIREN, date de bilan, type d'acte).
- **Gérer les erreurs gracieusement** : MCP indisponible, crédits épuisés,
  entreprise introuvable → message utilisateur clair, pas d'hallucination.
- **Router dynamiquement** entre un modèle rapide (Haiku 4.5) et un modèle
  plus puissant (Sonnet 4.6) selon la complexité de la requête.
- **Streamer la réponse** vers l'interface chat pour que l'utilisateur
  voie la progression et les appels de tool en temps réel.

### Exigences non-fonctionnelles
- **Latence cible** : première réponse visible < 2 s sur requête simple
  (U1), < 6 s sur requête complexe (U3).
- **Observabilité** : chaque appel MCP est tracé avec tool name + arguments
  dans le panneau latéral de la démo.
- **Sécurité** : aucune clé n'apparaît côté client, aucun log ne contient
  d'URL MCP complète.
- **Coût maîtrisé** : cap à 10 appels MCP par tour utilisateur.

---

## 5. Architecture

### 5.1 Stack technique

| Couche | Choix | Raison |
|---|---|---|
| Agent / LLM | **Claude Agent SDK (Python)** | Intégration MCP native, support du chaînage d'outils, tracing intégré |
| Modèles | **Claude Haiku 4.5** (router/simple) + **Claude Sonnet 4.6** (complexe) | Latence ↔ qualité, même clé API, même SDK |
| Data source | **MCP Pappers** (streamable-http) | Imposé par le brief, seul canal officiel |
| Interface | **Chainlit** | Chat out-of-the-box, streaming + step view native pour tool calls, déploiement 1 commande |
| Hébergement | **Railway (EU West / Amsterdam)** | Déploiement < 5 min, URL publique HTTPS, proche de Paris (~15 ms) |
| Secrets | Variables d'env (`.env` local + Railway vars) | Pas de secret commité |
| Dev tooling | `uv` pour les deps, `ruff` pour le lint | Rapide, standard Python 2026 |

### 5.2 Diagramme de flux

```
Utilisateur (navigateur, FR)
         │
         ▼ HTTPS
Chainlit UI (Railway EU-West, Amsterdam)
         │
         ▼
 Router Haiku 4.5 ──(simple)──▶ Agent Haiku 4.5
         │                              │
         └─(complexe)──▶ Agent Sonnet 4.6
                                        │
                                        ▼
                            MCP Client (streamable-http)
                                        │
                                        ▼
                    https://mcp.pappers.fr/{API_KEY}
```

### 5.3 Routing Haiku → Sonnet (validation de l'Option B)

Implémentation confirmée, tout fonctionne sur la **même clé API Anthropic** —
c'est le même compte, même SDK, seul le paramètre `model=` change à
l'invocation. Zéro friction supplémentaire.

**Mécanique** :

1. Pré-classifieur **Haiku 4.5** (~300 ms, ~0.5k tokens) :
   - Input : le message utilisateur.
   - Output structuré : `{ "complexity": "simple" | "complex", "reason": "…" }`.
   - Critères "complex" : multi-entités, comparaison, enchaînement >2 tools
     attendu, analyse financière, critères multiples.

2. Dispatch :
   - `simple` → boucle agent avec `model="claude-haiku-4-5-20251001"`.
   - `complex` → boucle agent avec `model="claude-sonnet-4-6"`.

3. **Escalade en cours de run** : si l'agent Haiku détecte qu'il est
   incapable de progresser (ex : >3 tool calls en échec, sortie non
   parsable, besoin de raisonnement trop profond), il termine et relance
   avec Sonnet en conservant le contexte. Badge visible en UI
   (`⚡ Haiku → 🧠 Sonnet`).

4. L'UI affiche quel modèle a traité la requête — effet démo fort, et
   surtout **signal explicite au recruteur** qu'on pense coût/latence.

### 5.4 MCP Pappers

- Transport : `streamable-http` (contrainte Pappers, cf. guardrails).
- URL : construite côté serveur à partir de `PAPPERS_API_KEY`.
- Au démarrage : l'agent liste les tools exposés par Pappers, logue cette
  liste (sans URL complète), et filtre à ceux utiles pour les cas U1–U5.
- Cache applicatif : clé = `(tool_name, args_hash)`, TTL 24 h, couvre les
  3 entreprises des tests officiels (LVMH, BNP, Carrefour) pour protéger
  les crédits en dev.

### 5.5 Système de prompt

- **System prompt** fige :
  - Le rôle (agent entreprises FR).
  - L'instruction "utilise prioritairement les tools Pappers pour toute
    question sur une société française" (satisfait la contrainte "via
    Pappers" côté Pappers sans demander à l'utilisateur de le taper).
  - Le format de réponse attendu (bullet points + sources SIREN + date
    de bilan quand applicable).
  - Les refus explicites (hors France, hors données Pappers, pas
    d'inventions).

---

## 6. Choix de modèle et de région

### 6.1 Modèles
- **Haiku 4.5** en default sur requêtes simples : ~400 ms TTFT, coût
  minime, suffisant pour U1 et U2.
- **Sonnet 4.6** sur complexe : meilleure qualité de tool selection et
  raisonnement financier, indispensable pour U3.

### 6.2 Région et data residency Claude

Décision après recherche :

- L'API Anthropic directe expose deux géographies d'inférence : `us` et
  `global`. **Pas d'option EU-only à date.**
- Pour une vraie résidence EU (RGPD, cas d'usage enterprise), il faut
  passer par **AWS Bedrock EU** (profils `eu.`, 6 régions dont Paris,
  Frankfurt, Ireland) ou **Vertex AI EU** (10 régions EU, dont
  europe-west3 Frankfurt).
- Microsoft Foundry EU est annoncé courant 2026.

**Choix pour cet exercice** : API Anthropic directe en géographie `global`.
Raisons :
- Time-to-build minimal (1 clé, aucune infra cloud à provisionner).
- Pour une démo interne de start-up, la résidence EU n'est pas un blocker.
- Coût plus bas qu'une inférence Bedrock/Vertex avec marge cloud.

**Mitigation documentée** : le README explique comment basculer vers
Bedrock EU (Paris) pour une mise en production RGPD — ~20 lignes de
changement via `anthropic[bedrock]`. C'est un signal "je connais la
contrainte, je sais la lever" qui compte en entretien.

### 6.3 Région hébergement app

- **Railway EU-West (Amsterdam)** — validé par toi.
- Latence Paris ↔ Amsterdam : 10–15 ms, négligeable.
- Le vrai goulet de latence reste le round-trip vers l'API Anthropic US
  (~80–150 ms par tool call). Ce goulet n'est pas réductible sans passer
  sur Bedrock EU.

---

## 7. Risques identifiés et mitigations

| # | Risque | Impact | Mitigation |
|---|---|---|---|
| R1 | Crédits Pappers épuisés pendant la démo | Agent muet, démo cassée | Cache local sur entités de test + cap 10 appels/tour + message d'erreur clair |
| R2 | Clé Pappers fuitée (URL dans path) | Compte compromis | Jamais loguer l'URL complète, server-side uniquement, `.env.example` sans valeurs, `.gitignore` strict, secret Railway |
| R3 | Haiku lâche sur une requête complexe | Mauvaise réponse en démo | Escalade automatique vers Sonnet + UI qui montre le switch |
| R4 | Hallucination sur entreprise inconnue | Perte de crédibilité immédiate | System prompt anti-hallucination + "je n'ai pas trouvé X sur Pappers" en fallback explicite |
| R5 | MCP Pappers downtime | Agent inutilisable | Healthcheck au boot, message utilisateur clair, pas de retry agressif |
| R6 | Latence Claude US trop visible | Ressenti "lent" | Streaming des tokens + affichage des étapes de tool en temps réel (Chainlit le fait nativement) |
| R7 | Over-engineering qui mange le week-end | Rien de livré | Planning strict + stretch uniquement si MVP ok samedi soir |
| R8 | Déploiement Railway qui casse dimanche soir | Pas de lien cliquable | Déployer en Day 1, pas en Day 2 ; Dockerfile testé localement avant push |
| R9 | Trigger "via Pappers" non respecté par le LLM | MCP ignoré | System prompt explicite + (si besoin) `tool_choice` forcé sur les premières requêtes |
| R10 | Entreprise étrangère demandée | Agent confus | Validation précoce + refus poli "Pappers couvre les entreprises françaises, essaie avec une entité FR" |

---

## 8. Ce qu'on montre à l'évaluateur

La démo doit, dans l'ordre, rendre **visible** les éléments suivants :

1. **Un lien cliquable** qui ouvre un chat HTTPS public (Railway). Zéro
   installation côté évaluateur.
2. **Les 3 tests officiels Pappers** qui passent (LVMH, dirigeants BNP,
   CA Carrefour).
3. **Une requête chaînée** (U3 comparaison Carrefour/Casino) qui montre
   l'agent qui enchaîne 4–6 appels MCP, avec steps visibles.
4. **Le switch de modèle** (badge UI Haiku vs Sonnet) selon la question.
5. **Un refus propre** sur une question hors-scope (ex : "parle-moi de
   Tesla" → réponse cadrée).
6. **Un README** qui explique en 1 page : choix techno + justifications
   + chemins d'évolution (Bedrock EU, ajout d'outils Pappers, éval).
7. **Un repo GitHub propre** : pas de secret, pas de code mort, commits
   lisibles, CI minimale (lint + import test).
8. **(Bonus)** un Loom de 2 min qui enchaîne les 6 points ci-dessus.

---

## 9. Ce qu'on NE fait PAS (scope négatif explicite)

- Pas d'authentification utilisateur, pas de gestion de compte.
- Pas de base de données persistante (cache en mémoire + SQLite si
  strictement nécessaire).
- Pas de vectorisation / RAG : Pappers est structuré, on n'en a pas
  besoin.
- Pas de multi-tenant.
- Pas de framework agent custom (LangChain, LlamaIndex…) — le Claude
  Agent SDK suffit et coûte 10x moins de friction.
- Pas de retry/backoff complexe : message d'erreur clair, on ne masque
  pas les pannes MCP.
- Pas de tests unitaires exhaustifs : 3–5 tests smoke suffisent (ils
  valent mieux qu'une suite bidon).

---

## 10. Livrables

| # | Livrable | Format |
|---|---|---|
| L1 | Repo GitHub public (ou partagé) | `github.com/Bakajanai69/genial-agent` |
| L2 | Agent déployé et testable | URL Railway publique |
| L3 | README avec choix techno | `README.md` à la racine |
| L4 | Cahier des charges (ce document) | `docs/cahier-des-charges.md` |
| L5 | Garde-fous Pappers | `docs/pappers-mcp.md` |
| L6 | `.env.example` documenté | racine du repo |
| L7 | Dockerfile testé | racine du repo |
| L8 | (Bonus) Loom 2 min | lien dans le README |

---

## 11. Planning week-end

### Samedi matin — 3 h (MVP fonctionnel)
- Scaffold repo (structure + deps + env).
- Claude Agent SDK + MCP Pappers branché en streamable-http.
- Premier end-to-end : Chainlit → agent Sonnet → MCP → réponse.
- Les 3 tests officiels passent en local.

### Samedi après-midi — 2 h (routing + UX)
- Pré-classifieur Haiku + dispatch.
- System prompt finalisé (trigger Pappers, refus hors-scope).
- Cache local pour entités de test.
- Badge modèle dans l'UI.

### Samedi soir — 1 h (déploiement)
- Dockerfile, Railway EU-West.
- Secrets Railway.
- Smoke test sur URL publique.

### Dimanche matin — 2 h (polish + doc)
- README avec choix techno (le vrai livrable écrit).
- `.env.example`, `.gitignore` stricts.
- Vérif anti-secret commit (`gitleaks` en pre-commit).
- 3 prompts de test écrits en `examples/`.

### Dimanche après-midi — 1 h (démo et bonus)
- Loom de 2 min.
- Dernier test à froid sur les 3 scénarios + un hors-scope.
- Envoi du lien à Fabien.

**Total** : ~9 h, tient dans un week-end avec enfants.

---

## 12. Arbre de décision résumé

- ✅ MCP Pappers via streamable-http (pas d'API REST directe).
- ✅ Claude Agent SDK Python (pas de framework custom).
- ✅ Haiku 4.5 + Sonnet 4.6, même clé API, routing explicite.
- ✅ Chainlit (pas de front custom Next.js pour un exo week-end).
- ✅ Railway EU-West Amsterdam (pas de Clever Cloud, choix validé).
- ✅ API Anthropic `global` (pas Bedrock EU pour le MVP, documenté en
  chemin d'évolution).
- ✅ Python, `uv`, `ruff`.
- ❌ Pas de RAG, pas de DB, pas d'auth, pas de multi-tenant.

---

## 13. Critères d'acceptation (definition of done)

L'exercice est livrable le dimanche soir si, et seulement si :

- [ ] Le lien Railway répond en HTTPS et affiche le chat Chainlit.
- [ ] Les 3 tests officiels Pappers (LVMH, BNP, Carrefour) donnent des
      réponses sourcées en < 6 s.
- [ ] La requête de comparaison (U3) fonctionne et enchaîne au moins 4
      tool calls visibles.
- [ ] Le badge Haiku / Sonnet change selon la complexité.
- [ ] Une question hors-scope (ex : Tesla) est refusée proprement.
- [ ] Le README explique en 1 page les choix techno et comment lancer
      localement.
- [ ] Aucun secret n'apparaît dans l'historique git (vérification
      `gitleaks` ou équivalent).
- [ ] Le repo est poussé sur GitHub sur la branche
      `claude/builder-evaluation-exercise-34Iyu`.
