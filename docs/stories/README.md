# Stories — plan d'implémentation

Ce dossier contient le découpage en **stories verticales** du projet
genial-agent. Chaque story est un **slice fonctionnel indépendant**,
pensé pour être exécuté par un Claude Code CLI frais sans contexte
préalable.

## Sources de vérité

Avant toute story, l'agent doit avoir lu :

- [`docs/cahier-des-charges.md`](../cahier-des-charges.md) — spec produit et architecture.
- [`docs/pappers-mcp.md`](../pappers-mcp.md) — garde-fous techniques Pappers.
- Les stories précédentes listées dans les prérequis de la story courante.

**Règle d'or** : aucune implémentation simulée, aucun mock d'intégration tierce.
Tout appel réseau doit être testé contre les APIs réelles.

### Décisions de cohérence (à appliquer par toutes les stories)

1. **Cap tool calls par tour = 5** (conformément à §5.3 et §14.3 C4 du
   cahier). §4 mentionne 10 — c'est une erreur à corriger dans le
   cahier au passage. Les stories figent **5** (`guardrails/caps.py`).
2. **Tool schemas** : unique point d'entrée
   `mcp_pappers.to_anthropic_schema(tools)` (S02). Consommé par S03.
3. **Stats** : les compteurs sont instrumentés au call-site (S07 liste
   explicitement les call-sites dans `agent.py`, `routing.py`,
   `mcp_pappers.py`).
4. **Mode dégradé cache-only** : déclenché par
   `observability/credit_guard.degraded()` (S07), qui lit
   `DAILY_PAPPERS_CREDITS_CAP` depuis `guardrails/caps.py` (S05) et
   `stats.pappers_calls_today()` (S07). Le cache tool-level est en S02
   (`mcp_cache.py` TTL 24 h).
5. **Unique `@cl.on_chat_start`** : S06 le crée, S10 l'étend (pas de
   redéfinition).

---

## Workflow à 3 agents par story

Chaque story passe par 3 phases, chacune exécutée par un **Claude Code CLI
frais** dédié. On tue la session à la fin de chaque phase pour ne pas saturer
le contexte.

### Phase 1 — Elicitation Agent (préparation)

**Rôle** : raffiner la story. Combler les trous. Vérifier que les APIs,
versions de SDK et patterns d'intégration sont à jour en 2026.

**Tâches** :

1. Lire la story en l'état + les sources de vérité citées.
2. **Recherche en ligne** (WebSearch / WebFetch) pour vérifier :
   - versions actuelles des librairies,
   - breaking changes récents des SDKs,
   - endpoints et signatures des APIs tierces,
   - patterns best-practice 2026.
3. Identifier les ambiguïtés dans la story et les résoudre via les docs.
4. **Mettre à jour directement le fichier story** avec les précisions
   (versions pinnées, snippets d'exemple, liens docs officielles).
5. Cocher les prérequis "user inputs" encore manquants (et ne pas démarrer
   la phase 2 avant qu'ils soient fournis).
6. Commit final : `story(Sxx): refine — <résumé>`.

**Sortie attendue** : story dont la phase 2 peut démarrer sans aucune
question restante.

---

### Phase 2 — Dev Agent (implémentation)

**Rôle** : implémenter la story conformément à la version raffinée.

**Tâches** :

1. Lire la story raffinée + les sources de vérité citées.
2. Créer / modifier les fichiers listés dans la section "Fichiers".
3. Écrire les **tests unitaires et d'intégration** spécifiés.
4. Lancer les tests localement — **aucun test skipé silencieusement**.
   Si un test requiert une clé API et qu'elle est absente, le test skip
   avec un message explicite visible dans le rapport pytest.
5. Vérifier tous les critères d'acceptation (§ "Critères d'acceptation").
6. Lancer `make lint` et `make test` — tout doit être vert.
7. Pas de secret dans le code ou les tests (vérifié par `gitleaks`).
8. Commit final : `feat(Sxx): <résumé de l'implémentation>`.
9. Push sur la branche `claude/builder-evaluation-exercise-34Iyu`.
10. Cocher la story dans le tableau de suivi ci-dessous.

**Règle stricte** : si un test contre API réelle échoue pour une raison
légitime (API down, rate limit, etc.), le dev agent **ne maquille pas**
le test. Il log l'échec, attend ou signale le blocage.

---

### Phase 3 — Review Agent (code review)

**Rôle** : traquer les coquilles, les trous de sécurité, les écarts au
cahier des charges.

**Check-list type** :

1. La story est-elle fonctionnellement complète ?
2. Tous les critères d'acceptation sont-ils vérifiables ?
3. **Fuites de secret** : recherche active de clés, URL MCP complète,
   tokens en clair dans le code ou les logs.
4. **Error handling** : chaque appel réseau a-t-il un retry + timeout ?
   Les codes d'erreur sont-ils mappés explicitement ?
5. **Tests** : couverture raisonnable ? Mocks documentés ? Intégrations
   réelles présentes et conditionnellement skip ?
6. **Conformité spec** : chaque exigence référencée dans la story est-elle
   implémentée ? Aucune sur-implémentation hors scope ?
7. **Style** : `ruff check` + `ruff format` clean. Pas de code mort, pas de
   TODO/FIXME oubliés.
8. **Dépendances** : toute nouvelle lib est justifiée. Versions pinnées.
   Pas de dep inutile.
9. **Docs** : docstrings où c'est non trivial, mais sans sur-commenter.
10. **Sécurité** : inputs user validés, pas d'injection possible, pas
    d'eval dynamique, pas de désérialisation pickle d'origine non sûre.

**Sortie attendue** : soit un commit `review(Sxx): approved` avec
éventuellement quelques fix mineurs (typos, lint), soit un fichier
`docs/stories/reviews/Sxx-rework.md` listant les points bloquants à
repasser par le dev agent.

---

## Check-list des inputs utilisateur

À cocher au fur et à mesure que tu (Lancelot) fournis chaque élément.
**Aucune story ne démarre tant que ses inputs ne sont pas cochés ici.**

### ✅ Déjà fourni dans `.env` local (2026-04-24)

Le fichier `/home/lancelot/projects/genial-agent/.env` est présent et
**gitignoré** (validé : `git check-ignore .env` matche la ligne 2 du
`.gitignore`). Les 3 clés ont été testées end-to-end et sont
opérationnelles.

- [x] `ANTHROPIC_API_KEY` — testée OK sur `claude-sonnet-4-6` **et**
      `claude-haiku-4-5` (résolu vers `claude-haiku-4-5-20251001`).
      `inference_geo=global` côté Sonnet.
- [x] `PAPPERS_API_KEY` — handshake MCP OK, protocole `2024-11-05`,
      **31 tools exposés**. Liste détaillée dans
      [`S02-mcp-pappers.md`](./S02-mcp-pappers.md).
- [x] `ELEVENLABS_API_KEY` — tier `growing_business`, quota
      5 922 075 chars/mois (largement au-dessus des ~5 000 attendus).
- [x] Constantes publiques (`ELEVENLABS_VOICE_GAELLE`,
      `ELEVENLABS_VOICE_GUILLAUME`, `ELEVENLABS_MODEL_ID`,
      `ENABLE_VOICE_BRIEF=false`, `LOG_LEVEL=INFO`) en place.

### ✅ Avant S01 (fondation) — validé 2026-04-24

- [x] **Python 3.12.3** détecté (`/usr/bin/python3`). Choix figé : 3.12
      (pas 3.13) — cf. décisions phase 1 dans `S01-scaffold.md`.
- [x] **uv 0.10.6** détecté (`~/.local/bin/uv`). Dernière stable PyPI
      `0.11.7` — pas de blocage, pin côté CI via `setup-uv@v8`.
- [x] **Docker 27.4.0** détecté.

### Avant S08 (déploiement)

- [ ] Compte Railway créé (GitHub SSO OK), repo `Bakajanai69/genial-agent`
      lié comme projet.
- [ ] Region EU-West (Amsterdam) confirmée dans le projet Railway.
- [ ] **Recopier** toutes les variables du `.env` local dans Railway
      Project Variables (identiques à celles déjà validées localement).
- [ ] Compte UptimeRobot créé (plan free) + URL `/health` du déploiement
      Railway configurée en ping 5 min.

### Avant S10 (stretch vocal — optionnel)

- [ ] MVP vert samedi soir (gating §19.1 du cahier des charges respecté).
- [x] `ELEVENLABS_API_KEY` fournie dans `.env` (cf. plus haut).
- [x] Solde crédits ElevenLabs vérifié (tier `growing_business`, quota
      mensuel 5.9 M chars).
- [ ] Passer `ENABLE_VOICE_BRIEF=true` dans `.env` local + Railway après
      merge S10 + gating vert.

### Optionnel

- [ ] Compte Loom (enregistrement démo backup, 2 min).

---

## Liste et état des stories

| # | Story | Statut | Dépend de | Parallèle avec |
|---|---|---|---|---|
| S01 | [Scaffold repo](./S01-scaffold.md) | ✅ approved | — | — |
| S02 | [Client MCP Pappers](./S02-mcp-pappers.md) | ✅ approved | S01 | — |
| S03 | [Agent Claude core](./S03-agent-core.md) | ⬜ à faire | S02 | — |
| S04 | [Routing Haiku↔Sonnet](./S04-routing.md) | ⬜ à faire | S03 | — |
| S05 | [Garde-fous 6 couches](./S05-guardrails.md) | ⬜ à faire | S03 | S06 |
| S06 | [UI Chainlit](./S06-chainlit-ui.md) | ⬜ à faire | S03 | S05 |
| S07 | [Observabilité + healthcheck](./S07-observability.md) | ⬜ à faire | S01, S03 | S08 |
| S08 | [Déploiement Railway](./S08-deployment.md) | ⬜ à faire | S01, S07 | S07 |
| S09 | [Polish : README, EVALUATION, Loom](./S09-polish.md) | ⬜ à faire | S04, S05, S06, S08 | — |
| S10 | [🎯 Stretch : brief vocal ElevenLabs](./S10-voice-brief.md) | ⬜ bloqué (gating) | S09 | — |

**Légende** : ⬜ à faire · 🟡 en cours · ✅ approuvée · ⏸ bloquée

---

## Convention de commit

- `story(Sxx): refine — …` (phase 1 elicitation)
- `feat(Sxx): …` (phase 2 dev)
- `test(Sxx): …` (phase 2 dev, tests seulement)
- `review(Sxx): approved` ou `review(Sxx): fix — …` (phase 3 review)

Toujours sur la branche `claude/builder-evaluation-exercise-34Iyu`.

---

## Stratégie d'exécution recommandée

Avec un Claude Code CLI par phase par story :

| Créneau | Stories | Mode |
|---|---|---|
| Samedi matin (3 h) | S01 → S02 → S03 | séquentiel, 3 sessions × 3 phases |
| Samedi après-midi (2 h) | S04 puis S05 \|\| S06 | S05 et S06 en parallèle sur worktrees git |
| Samedi soir (2 h) | S07 \|\| S08 | en parallèle |
| Dimanche matin (2 h) | S09 | séquentiel |
| Dimanche après-midi (1-2 h) | S10 si gating vert | séquentiel |

Au total : ~30 sessions Claude Code CLI (3 phases × 10 stories). Chaque
session dure 10-40 min selon la story.
