# Workflow Claude Code — méthodologie 3 phases

> Ce document explicite **comment j'utilise Claude Code (CLI Anthropic)
> sur ce projet**. Il s'adresse à un lecteur (CTO, lead engineer) qui
> veut comprendre où je trace la ligne entre *« le candidat a réfléchi »*
> et *« l'outil a généré »*. Spoiler : c'est de la méthodologie, pas du
> clic-bouton.
>
> Je l'ai mis à part pour que quiconque arrive sur le repo via le ADR
> [`docs/architecture-decisions.md`](./architecture-decisions.md) puisse
> comprendre **dans la même session de lecture** comment ce repo a été
> produit, sans avoir à chercher.

---

## Pourquoi 3 phases plutôt qu'une session marathon

J'ai construit ce repo en découpant chaque story (S01 → S10) en **3
sessions Claude Code distinctes** :

```
Phase 1 (Elicitation) → Phase 2 (Dev) → Phase 3 (Review)
   session fraîche       session fraîche      session fraîche
   commit → push          commit → push        commit → push
```

**La motivation est purement pragmatique** : un Claude Code en session
longue sature son contexte vers ~50 % et commence à oublier des
invariants. Sur un projet de cette taille (~700 K tokens de doc + code
+ traces), une session continue aurait dérivé après 2-3 stories.

En découpant chaque story en 3 mandats clairs, **chaque agent démarre
vide** avec une mission précise. Les inputs sont les mêmes pour les 3
(le fichier story, le cahier des charges, les sources de vérité
listées) — ce qui change c'est **le mandat** et **le livrable
attendu**.

C'est plus proche d'un *Scrum sprint avec Definition of Done* que d'un
*one-shot copilot*.

---

## Les 3 phases en détail

### Phase 1 — Elicitation

**Mandat** : raffiner la story avant que le dev commence. Combler les
trous techniques. Vérifier que les SDK / API / patterns évoqués sont
**à jour en 2026**.

**Tâches typiques** :
1. Lire la story en l'état + les sources de vérité (cahier des charges,
   `docs/pappers-mcp.md`, stories précédentes).
2. **Recherche en ligne** (WebSearch / WebFetch) pour vérifier :
   versions actuelles des libs, breaking changes, signatures d'API,
   patterns best-practice 2026.
3. Identifier les ambiguïtés et les résoudre via les docs officielles.
4. **Réécrire la story** avec les précisions (versions pinnées,
   snippets d'exemple, liens docs officielles).
5. Lister les inputs utilisateur encore manquants (et bloquer la
   phase 2 tant qu'ils ne sont pas fournis).

**Commit final** : `story(Sxx): refine — <résumé>`.

**Sortie attendue** : story dont la phase 2 peut démarrer sans aucune
question restante. Si une ambiguïté reste, c'est documenté avec un
TODO explicite et la phase 2 ne démarre pas dessus.

### Phase 2 — Dev

**Mandat** : implémenter conformément à la version raffinée.

**Tâches typiques** :
1. Lire la story raffinée + les sources de vérité.
2. Créer / modifier les fichiers listés dans la section "Fichiers".
3. Écrire les **tests unitaires et d'intégration** spécifiés.
4. Lancer les tests localement — **aucun test skipé silencieusement**.
   Si un test requiert une clé API absente, le test skip avec un
   message explicite visible dans le rapport pytest.
5. Vérifier tous les critères d'acceptation.
6. Lancer `make lint` et `make test` — tout vert.
7. Pas de secret dans le code ou les tests (vérifié par `gitleaks`).

**Commit final** : `feat(Sxx): <résumé>`.

**Règle stricte** : si un test contre API réelle échoue pour une
raison légitime (API down, rate limit), le dev agent **ne maquille
pas** le test. Il log l'échec, attend ou signale le blocage.

### Phase 3 — Review

**Mandat** : auditer le code livré par la phase 2. Traquer les
coquilles, les trous de sécurité, les écarts au cahier.

**Check-list typique** :
1. Story fonctionnellement complète ?
2. Tous les critères d'acceptation vérifiables ?
3. **Fuites de secret** : recherche active de clés, URL MCP complète,
   tokens en clair dans le code ou les logs.
4. **Error handling** : chaque appel réseau a-t-il un retry + timeout ?
5. **Tests** : couverture raisonnable ? Mocks documentés ?
   Intégrations réelles présentes et conditionnellement skip ?
6. **Conformité spec** : chaque exigence du cahier est-elle
   implémentée ? Aucune sur-implémentation hors scope ?
7. **Style** : `ruff check` + `ruff format` clean. Pas de code mort,
   pas de TODO/FIXME oubliés.
8. **Sécurité** : inputs user validés, pas d'injection possible, pas
   d'eval dynamique, pas de désérialisation pickle d'origine non sûre.

**Commit final** : `review(Sxx): approved` (si OK) ou
`review(Sxx): fix — <résumé>` (si correction nécessaire).

**Effet observé** : la phase 3 a attrapé sur ce projet, en moyenne,
2-4 trous par story que la phase 2 avait laissé passer. Ex notable :
S05 review a trouvé 3 bypass d'input injection que le dev avait
ratés (word-prefix, zero-width, ligature NFKD). C'est exactement ce
que la méthode est supposée produire.

---

## Où je tranche, où je laisse l'outil proposer

C'est la question la plus importante de ce document. Voici ma
distribution réelle des décisions sur ce projet :

### Décisions où **je tranche** (pas de délégation)

- **Spec produit** : cahier des charges (`docs/cahier-des-charges.md`)
  écrit par moi avant la moindre ligne de code. C'est le contrat
  qu'aucune phase ne peut contourner.
- **Stack core** : Anthropic SDK + MCP natif, dual modèle Haiku/Sonnet,
  Chainlit, Railway → AWS. Cf. ADR-1 à ADR-6.
- **Pivots produit** : ouverture S09.5 (Payload Vault après
  dogfooding), S09.6 (cache disque après bug Pappers PAYG), S10 pivot
  brief radio → voice duplex.
- **Arbitrages budget** : cap journalier crédits MCP, choix de
  pré-warmer 4 entités golden vs stress test exhaustif, désactivation
  voice mode par défaut côté prod.
- **Architecture cible AWS** (cf. ADR-6) — j'ai mon SAA, c'est mon
  terrain.

### Décisions construites **en tandem** (dialogue technique itératif)

- Routing 3 couches (j'ai défini les 3 niveaux, Claude a proposé les
  patterns regex et le contrat `escalate_to_sonnet`).
- Découpage des 6 couches de garde-fous (j'ai cadré C1/C5/C6 d'abord
  dans le cahier, Claude a structuré l'implémentation).
- Format ADR / format stories — proposé par moi, raffiné par Claude.

### Décisions où **j'accepte la proposition outil**

- Wording exact des system prompts (mes contraintes produit, leur
  formulation).
- Implémentation du runner adversarial pytest (mon contrat T1-T10,
  leur code).
- Détails Chainlit (hooks, event types, conventions de logging
  `structlog`).
- Convention de commit conventional-commits (que j'utilise déjà
  habituellement, mais formalisé par Claude dans le doc).

---

## Anti-patterns que cette méthode évite

C'est *parce que* j'ai vu ces anti-patterns sur des projets précédents
(client + autres builders) que je tiens à cette discipline.

- **One-shot copilot** : *« Claude, écris-moi un agent qui fait X »*.
  Résultat : du code plausible mais qui ignore les contraintes du
  brief et qu'on doit reverse-engineer pour comprendre. **Méthode
  3 phases l'évite** : la phase 1 figure le brief par écrit, la phase 2
  doit la respecter, la phase 3 audite l'écart.
- **Session marathon** : 8 h sur la même session Claude. Le contexte
  sature, l'agent oublie ses invariants, refait des trucs déjà faits,
  contredit ses propres décisions précédentes. **Méthode 3 phases
  l'évite** : sessions fraîches systématiques.
- **Pas de review** : *« le test passe → on push »*. Manque les
  bypass de sécurité, les fuites de secret, les TODO oubliés.
  **Méthode 3 phases l'évite** : phase 3 obligatoire avec check-list
  sécurité explicite.
- **Mock toujours, jamais de live** : tests qui passent en CI mais
  cassent en prod. **Évité ici** : la suite intégration tape les
  vrais MCP Pappers + Anthropic API + ElevenLabs (opt-in via
  `make test-integration` pour ne pas brûler des crédits côté CI).

---

## Limites de la méthode

À transparence assumée, voici ce que la méthode 3 phases **ne fait
pas** bien :

- **Lente** : chaque story coûte ~3× le temps d'une session unique.
  Sur ce projet, c'était le bon trade-off (qualité > vélocité). Sur
  un sprint client GENIAL avec deadline serrée, j'ajusterais : phases
  1 et 3 condensées sur les stories simples, méthode complète
  uniquement sur les stories à enjeu (sécurité, perf, integration
  tierce).
- **Coûteuse en tokens Anthropic** : chaque session fraîche relit le
  cahier + les sources. Sur du long terme, je couplerais avec
  l'Anthropic prompt caching côté API (que j'ai justement livré
  côté agent en S09.7 — cf. ADR-3).
- **Pas miracle** : la phase 3 attrape 80 % des trous, pas 100 %. Un
  pack adversarial dédié (T1-T10 livré ici) reste indispensable pour
  les 20 % restants. Les 2 cas tolérés du pack (T6 entité bidon, T9
  chinois mandarin) sont passés à travers les 3 phases — ce sont des
  cas où aucune review automatisée ne pouvait remplacer une session
  de débug live.
- **Marche bien avec Claude, pas testé sur d'autres modèles** : la
  fiabilité tool-use et la métacognition de Claude rendent la
  méthode efficace. Sur GPT-4 ou Mistral, il faudrait peut-être plus
  de garde-fous explicites côté prompts.

---

## Ce que cette méthode dit de moi (en tant que builder)

Je préfère expliciter pour ne pas laisser place à l'interprétation :

- **Je sais utiliser l'IA en outil de production**, pas en gadget. La
  JD GENIAL parle de *« AI-first problem solver »* et *« daily usage
  of AI tools »* — c'est exactement ce workflow.
- **Je trace ce que je décide vs ce que l'outil propose**. Cf. la
  section "Où je tranche" ci-dessus, et l'ADR
  [`docs/architecture-decisions.md`](./architecture-decisions.md)
  qui sépare explicitement les deux.
- **Je sais où la méthode atteint ses limites**. Cf. section "Limites"
  ci-dessus.
- **Je préfère une méthode discutable et explicite à un workflow
  invisible**. Si vous voulez challenger ce workflow en entretien, j'ai
  des arguments. Si vous voulez le challenger sur le repo, vous voyez
  exactement ce qu'il a produit, étape par étape.
