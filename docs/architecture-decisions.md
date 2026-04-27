# Architecture Decision Records — genial-agent

> Récap des arbitrages techniques que j'ai faits sur ce week-end, en
> 1re personne. Format ADR condensé : pour chaque décision, ce que j'ai
> retenu, ce que j'ai écarté, et pourquoi. Le repo entier est consultable
> à plat (commits, stories `docs/stories/`, traces `traces/`) ; ce
> document est l'entrée recommandée pour un lecteur qui scanne en 5 min.
>
> **Contexte temps** : ~14 h effectives sur le week-end (cadre familial,
> enfants à la maison — ~3 h samedi matin, 3 h samedi après-midi, 2 h
> samedi soir, 2 h 30 dimanche matin, 1 h 30 dimanche après-midi, +1 jour
> et demi de polish lundi-mercredi pour S09.5 → S10). Brief de Fabien :
> *« agent IA qui donne de l'info sur une entreprise en utilisant le MCP
> Pappers fraîchement sorti, pas de contrainte UX ni techno »*.
>
> **Posture sur cet ADR** : je sépare **les décisions structurantes
> que je défends** (1 à 12 ci-dessous) des **décisions où j'ai accepté
> la proposition de l'outil sans en faire un signature pick** (regroupées
> en bas). Je préfère assumer cette nuance plutôt que m'attribuer
> uniformément 100 % du repo — c'est plus crédible et plus précis.

---

## 1 — Anthropic SDK + lib `mcp` Python (vs LangChain / LlamaIndex / framework custom)

**Retenu** : `anthropic` Python SDK + `mcp` lib officielle, en accès
direct.

**Pourquoi** : le brief reposait entièrement sur MCP. Côté Anthropic
2026, le tool-use est natif et le streamable-http MCP est une
intégration first-class. Aller chercher un wrapper LangChain ou
LlamaIndex aurait ajouté une couche d'abstraction qui masque les
vrais events (`tool_use`, `tool_result`, `text_delta`) sans bénéfice
en échange — debug plus difficile, surface de bug plus large.

**Écarté** : LangChain (overkill pour ce scope, debug indirect),
framework agent custom (temps perdu en plomberie au lieu de produit).

**Source** : `src/genial_agent/agent.py`, `src/genial_agent/mcp_pappers.py`.

---

## 2 — Anthropic Claude (Haiku 4.5 + Sonnet 4.6 dual) (vs OpenAI / Mistral / single model)

**Retenu** : Claude, en routing dual Haiku 4.5 / Sonnet 4.6, même clé
API.

**Pourquoi Anthropic vs OpenAI/Mistral** : dans mon expérience builder
des 2 dernières années, Claude est le modèle le plus *fiable* sur
MCP + tool-use — moins de "tool drift" (l'agent qui invente un
argument, qui appelle le mauvais tool), et l'auto-correction interne
est plus calibrée. Le brief était centré MCP, pas un cas d'usage où
GPT-4 ou Mistral auraient apporté quelque chose de différentiant.

**Pourquoi dual modèle** : l'expérience utilisateur prime sur la
vitesse brute *et* sur le coût. Haiku 4.5 me donne ~400 ms TTFT sur
les requêtes simples (U1 fiche LVMH) — c'est ce que l'utilisateur
ressent comme "instantané". Sonnet 4.6 sur les requêtes complexes
(U3 compare Carrefour vs Casino) me donne la fiabilité tool selection
+ raisonnement nécessaire. Pas de chemin "lent par défaut", pas de
chemin "léger qui lâche".

**Écarté** :
- Single Sonnet pour tout : overkill cher, latence inutile sur U1.
- Single Haiku pour tout : lâche sur U3 (mesuré).
- Routeur LLM dédié : ~400 ms d'overhead sur **100 %** des requêtes
  alors que ~80 % iraient bien en Haiku direct. Mauvais trade-off
  latence — j'ai préféré un keyword router code (0 ms) + auto-escalade.

---

## 3 — Routing 3 couches : keyword + auto-escalade Haiku + cap dur

**Retenu** : défense en profondeur sur 3 niveaux.

1. **Pré-routeur keyword** (regex, code pur, < 1 ms) : tag "complex"
   sur des patterns explicites (`compare`, `versus`, `dossier complet`,
   `évolution sur X ans`, ≥ 2 noms d'entité) → dispatch Sonnet direct.
2. **Haiku par défaut** sur le reste, avec accès à un tool meta
   `escalate_to_sonnet(reason)` qu'il appelle lui-même quand il
   détecte que la requête le dépasse (ex : après 2 calls il voit qu'il
   en faut 5+ de plus). Sonnet reprend avec le contexte complet
   (tool results déjà obtenus).
3. **Cap dur backend** : 7 tool calls / 60 s wall-clock / 200 K tokens
   par session → escalade forcée côté code. Filet de sécurité quand
   la métacognition LLM échoue.

**Pourquoi 3 couches plutôt qu'un seul LLM router** : keyword router
attrape ~80 % des cas complexes en gratuit (0 ms), Haiku rattrape le
reste via auto-escalade, le cap dur est le filet ultime. Overhead nul
sur le chemin court. **Transparence UX** : badge `⚡` / `🧠` / `⚡→🧠`
visible côté utilisateur — je voulais que le CTO voie quel modèle
sert chaque réponse, pas une boîte noire.

**Construit en tandem** avec Claude (échanges techniques), validé
et défendu par moi : pattern que je connais et qui matche ma
philosophie *« coût/latence aware par design »*.

**Source** : `src/genial_agent/routing.py`.

---

## 4 — Chainlit 2.11 (vs Next.js custom / Streamlit / Gradio)

**Retenu** : Chainlit pour l'UI chat.

**Pourquoi** : 2 jours = je vais vers les briques faciles autant que
possible. Chainlit me donne, out-of-the-box : chat avec streaming,
step view dépliable des tool calls (pour la transparence MCP),
persistance SQLite des conversations (sidebar threads cross-session,
livrée S09.6), auth callback, hooks de personnalisation CSS.
Customisations livrées : logo Genial dual-theme, footer RGPD, splash
anti-FOUC, palette violette/bleue Genial, anti-zigzag visuel sur le
1er paint.

**Trade-off assumé** : moins de contrôle UI fine que Next.js. Pour
un client en prod long-terme, je passerais sur **Next.js + Vercel AI
SDK** côté front (UI sur mesure, A/B testing produit, intégration
plus naturelle avec un design system client) et garderais Chainlit en
mode "outil de démo / debug interne".

**Décision faite en validant une proposition Claude** — je le note
honnêtement, je n'aurais pas eu le temps de comparer Streamlit /
Gradio / build perso à la main, et Chainlit a été la bonne brique
pragmatique au regard de la stack.

---

## 5 — API Anthropic `global` (vs Bedrock EU / Vertex AI EU pour le MVP)

**Retenu** : API Anthropic directe, géographie `global`.

**Pourquoi** : 1 clé, aucune infra cloud à provisionner, ~20 lignes
de SDK. Pour une démo destinée à Fabien sur 48 h, la résidence EU
n'est pas un blocker. Coût plus bas qu'une inférence Bedrock/Vertex
avec marge cloud.

**Mitigation documentée** : `docs/cahier-des-charges.md` §6.2 décrit
comment basculer vers **Bedrock EU (Paris ou Frankfurt)** pour une
résidence RGPD prod-ready — ~20 lignes de changement via
`anthropic[bedrock]`. C'est explicitement listé en next-step #3 du
README. Pour un client enterprise FR (Cegid / Crédit Agricole), c'est
le premier pivot que je ferais.

---

## 6 — Railway EU-West Amsterdam (MVP) **vs** AWS Fargate (cible prod)

C'est le choix dont je suis le **moins fier** au sens "fit prod" et
celui où je veux être le plus transparent.

**Retenu pour le MVP** : Railway EU-West (Amsterdam), 1 replica,
plan Hobby. Justification temps : déploiement < 5 min, region EU,
secrets env, port binding auto via `${PORT:-8000}`, healthcheck
géré, volume `/data` pour persistance cache MCP + SQLite threads.
Fonctionnel pour la démo. **Ne scale pas** : 1 replica, sticky
sessions à configurer en multi-replica, plan Trial peut endormir
l'app, capacité concurrent réelle non stress-testée (cf. limites).

**Architecture cible si productionnisation** (j'ai mon AWS SAA, c'est
le terrain que je préfère) :

```
Route 53 (latency-based routing)
   │
   ▼
CloudFront (edge cache, WAF Shield Standard)
   │
   ▼
ALB (health checks /health, sticky sessions WebSocket Chainlit)
   │
   ▼
ECS Fargate (auto-scaling target tracking CPU/mem, multi-AZ)
   │
   ├─► Aurora Postgres OU DynamoDB (selon arbitrage coût/latence
   │    pour la persistance threads ; Aurora pour SQL relationnel,
   │    DynamoDB pour scale lecture/écriture pure)
   │
   ├─► Bedrock Claude (région EU Frankfurt, accès via VPC endpoint
   │    backbone AWS — pas de sortie internet pour l'inférence)
   │
   ├─► Secrets Manager + KMS (PAPPERS_API_KEY, ELEVEN_AGENT_SHARED_TOKEN)
   │
   └─► S3 (audit trail append-only, manifest signé pour SOC 2)
```

VPC mono-AZ pour un MVP client, multi-AZ avec NAT Gateway si scaling
réel. IAM roles cross-service, pas de credentials hardcoded. WAF
règles managées + rate limit IP. Tracing distribué CloudWatch +
X-Ray (ou Langfuse / OpenTelemetry si on veut un vendor neutre).

**Honnêteté** : je n'ai pas pu construire cette architecture en 48 h.
Railway = compromis temps assumé. Si on engage une mise en prod chez
GENIAL, c'est par cette cible AWS que je passerais — c'est ce que
je sais faire et c'est ce que je préconiserais à un client enterprise.

---

## 7 — Cap-as-UX-event (S09.7)

**Retenu** : tout cap qui se déclenche émet un event
`cap_continuation_proposed` qui expose côté UI Chainlit les actions
**« 🔄 Continuer »** (relance du tour avec `ConversationState`
préservé, Payload Vault inclus) ou **« 📋 Synthèse partielle »**
(Sonnet condense les tool results déjà accumulés). Pas de dead-end
conversationnel.

**Origine** : proposition Claude que j'ai retenue après analyse — le
pattern me parlait, il match la philosophie 3U *Used* de GENIAL : un
agent qui assume ses limites *et propose une suite* est adopté par
l'utilisateur, un agent qui meurt sur un cap est abandonné.

---

## 8 — Payload Vault session-scoped (S09.5)

**Mon arbitrage le plus structurant post-MVP. Je le défends à 100 %.**

**Observation déclenchante** : pendant le dogfooding S09 dimanche
matin, j'ai vu que sur U3 « Compare Carrefour vs Casino sur 3 ans »
l'agent renvoyait systématiquement les chiffres 2016 au lieu de 2024.
J'ai donc demandé à Claude d'**inspecter manuellement les payloads
MCP Pappers** vs ce que voyait l'agent dans son contexte.

Résultat de l'inspection (cf. `docs/inspection-mcp-vs-agent.md`) :
- ``comptes-entreprise`` sur Carrefour Hyper sans `annee` →
  **706 019 chars** de JSON (toute l'histoire des comptes depuis 2007).
- ``cartographie-entreprise`` sur LVMH → **25 890 chars**.
- La borne agent `_TOOL_RESULT_MAX_CHARS=16_000` coupait
  brutalement. L'agent voyait le **début** du JSON (années anciennes)
  et **ratait** les bilans récents en fin de payload.
- Robustesse comportementale OK : il signalait *« données tronquées »*
  et n'inventait pas. Mais **complétude** dégradée.

**5 patterns évalués** (matrice complète dans
`docs/stories/S09.5-mcp-payload-handling.md` §"Recherche") :

| Pattern | Verdict |
|---|---|
| Programmatic Tool Calling Anthropic (sandbox `code_execution`) | **Pas supporté sur Haiku 4.5** → casse le routing S04 si on force Sonnet |
| Filesystem offload Deep Agents (LangChain) | Pas de FS persistant Railway free + dépendance LangChain |
| Sub-agent synthesizer (1 LLM qui résume avant retour) | × 2 latence + risque de perte d'info structurée |
| Wrapper déterministe per-tool | Pas générique, à maintenir à chaque ajout d'outil Pappers |
| **Offload générique session-scoped (Payload Vault)** | ✅ retenu |

**Architecture livrée** (`src/genial_agent/payload_vault.py`) : tout
payload MCP > 12 K chars est rangé dans un vault in-memory attaché au
`ConversationState` ; l'agent reçoit un index JSON compact (squelette,
cardinalités, chemins jsonpath candidats) + un `payload_id`, et
ré-interroge à la demande via 2 tools locaux :

- `payload_inspect(payload_id, jsonpath)` — extraction ciblée.
- `payload_search(payload_id, regex)` — recherche libre.

Le raisonnement métier (Pappers → français → réponse sourcée) reste
inchangé, on a juste donné à l'agent un mécanisme générique pour
naviguer un gros JSON sans le saturer.

**Résultat mesurable** :
- Carrefour CA : avant *« remontent à 2016 »* (incorrect) → après
  **« 11.77 Mds € au bilan clos 31/12/2024 »** ✅
- Pertes effectives sur les payloads gros : avant 97.7 % → après 0 %.
- Tool calls Pappers : 2 (inchangé, le cache absorbe).

C'est cette story qui m'a fait sortir le projet du POC et le rendre
livrable comme **vrai assistant entreprise**, pas une démo qui dit
*« données tronquées »*.

---

## 9 — Cache disque baked Docker + volume Railway (S09.6)

**Mon arbitrage**, motivé par une contrainte de coût.

**Réalité budget MCP** :
- 1 100 crédits Pappers achetés pour ce week-end (~80 € avec abo).
- Il m'en reste **254** pour la démo de Fabien.
- Soit **~846 crédits consommés en dev/dogfooding** sur 48 h.

À ce rythme, en production multi-client GENIAL, le poste MCP
exploserait. C'est une **contrainte de design**, pas une plainte —
et c'est exactement le genre d'arbitrage qu'un staff doit savoir
faire dès le jour 1.

**Solution livrée** :
- Cache disque persistant `data/mcp_cache.json` baked dans Docker
  (committé) + volume Railway `/data` pour la persistance
  cross-deploy. Bootstrap volume idempotent au boot (idempotent — ne
  réécrase pas les conversations utilisateur accumulées).
- 4 entités golden (LVMH, BNP, Carrefour, Casino) × 3 années
  (2022-2024) **pré-warmées** côté repo, TTL 7 j.
- La démo U3 tourne **sans toucher l'abo Pappers**, même si saturé.
- Pré-warm rejouable via `make prewarm-comptes` après refill mensuel.

**Effet de bord positif** : un bug serveur Pappers identifié 2026-04-25
(le tool ``comptes-entreprise`` refuse par intermittence les jetons
PAYG quand l'abo est à 0) devient **invisible** côté utilisateur
final pour les entités golden. Pour les autres entités, j'ai ajouté
un fallback agent : retour d'un `workaround_hint` qui dirige Claude
vers `recherche-entreprises` (1 crédit PAYG) ou un refus poli sourcé.

---

## 10 — Voice mode duplex Eleven Agents (S10) — **pivot assumé + perfectible assumé**

**Décision initiale (samedi soir)** : si gating §19.1 vert, brief
vocal radio TTS post-réponse (~30 s, simple).

**Pivot 2026-04-26** : après lecture détaillée de la doc Eleven Agents
(Conversational AI), j'ai vu que je pouvais faire un **vrai chat vocal
duplex** (style ChatGPT Voice — ASR + turn-taking propriétaire +
custom LLM SSE + TTS streaming) pour le même budget (~3 $) sans
toucher la logique agent. J'ai pivoté.

**Architecture** : agent Genial **100 % inchangé** côté logique (MCP,
vault, caps, routing) ; voice mode est une couche I/O wrapper sous
`src/genial_agent/voice/` qui fait :
- adaptation OpenAI Chat Completions ↔ Anthropic ConversationState ;
- injection d'un `VOICE_SUFFIX` au system prompt (pas de SIREN à voix
  haute, chiffres arrondis, ~100-120 mots, style narratif) ;
- streaming SSE OpenAI Chat Completions vers Eleven ;
- chunks narratifs sur les events `tool_use` (« Je cherche le SIREN…»,
  « Je consulte les comptes…») pour combler les latences U3.

POC end-to-end validé 2026-04-27 depuis l'API
``simulate-conversation`` ElevenLabs → ngrok local → Chainlit.

**Limites que j'assume franchement** :
- **Latence end-to-end élevée** : pipeline Pass 1 / Pass 2 / TTFT
  audio / TTLB audio non optimisés, je n'ai pas eu le temps de
  benchmarker chaque étape.
- **Voix robotique, manque de naturel** : la stratégie de chunking
  SSE n'est pas optimisée, certains fillers passent au TTS de manière
  hachée. J'ai introduit un buffer phrases (sentence buffer) en hotfix
  mais c'est perfectible.
- **Pas creusé en profondeur** : Deepgram (mentionné dans la JD
  GENIAL) comme ASR alternatif, LiveKit comme alternative au widget
  Eleven, retravail du `voice/narrate.py` mapping.

**C'est une feature wow factor, pas une feature finie.** Désactivée
par défaut côté prod (`ENABLE_VOICE_MODE=false` → l'endpoint
`/v1/chat/completions` n'est même pas monté côté serveur, surface
d'attaque nulle). Activable à chaud.

Avec une journée de plus dessus, je creuserais d'abord la latence
ASR ↔ custom LLM (probablement bottleneck), puis le chunking
sentence-aware côté SSE, enfin un benchmark Eleven vs LiveKit/Deepgram
sur le critère "naturel ressenti".

---

## 11 — Workflow 3 phases (Elicitation → Dev → Review) avec sessions Claude Code distinctes

**Mon choix méthodologique**, c'est mon habitude de travail. Détail
dans [`docs/workflow-claude-code.md`](./workflow-claude-code.md).

**Pourquoi** : un Claude Code en session longue sature son contexte
vers 50 % et commence à oublier des invariants (par ex la règle des
caps, le contrat des events, etc). En découpant chaque story en
3 sessions fraîches avec des mandats distincts (raffiner → implémenter
→ auditer), chaque agent démarre vide avec une mission claire. Les
3 commits qui en résultent (`story(Sxx): refine`, `feat(Sxx): …`,
`review(Sxx): approved/fix`) tracent le workflow dans le git log.

**Bénéfices observés sur ce projet** :
- Moins d'oublis (chaque review attrape ce que le dev a manqué).
- Moins de raccourcis (chaque elicitation force à vérifier les
  versions SDK 2026, qui ont changé entre 2025 et 2026).
- Plus de robustesse (ex : S05 review a trouvé 3 bypass d'input
  injection que le dev avait laissés passer).

C'est une méthode plus proche du *Scrum sprint avec Definition of
Done* que du *one-shot copilot*. Pour de l'AI builder en équipe
GENIAL, je pense qu'elle scale.

---

## 12 — Pourquoi pas de RAG ici (et où je l'aurais sorti)

**Le RAG est dans la JD must-have, je n'en ai pas fait, je m'en
explique.**

Pappers expose des données **structurées** via MCP : SIREN, bilans
horodatés, dirigeants, actes, mandats. Tout est *consultable par
requête*, pas par recherche sémantique. Faire du RAG dessus aurait
été du **sur-engineering** :

- Dupliquer les données Pappers dans un vector store qu'il faut
  synchroniser → drift entre Pappers et l'index local.
- Ajouter de la latence (embed + retrieve + rerank) sur des données
  déjà adressables par clé.
- Diluer le sourçage : *« CA 2024 = 11.77 Mds € (Pappers, bilan clos
  31/12/2024) »* est traçable octet pour octet ; *« CA 2024 = 11.77
  Mds € (chunk #4f8a retrouvé par embedding cosine 0.89) »* ne l'est
  plus.

Le **Payload Vault** (ADR-08) est *« RAG sans embedding »* : on garde
les payloads MCP réels en mémoire et l'agent navigue via jsonpath/regex.
Plus exact, plus traçable, plus simple. C'est une décision d'archi,
pas un manque de skill.

**Où je sortirais le RAG chez GENIAL** : un client avec un corpus non
structuré (documentation interne PDF, base de connaissance wiki,
emails archivés client) où l'information est *latente dans le langage*
et pas *adressable par clé*. Là, le pipeline RAG canonique
(chunking + embeddings + vector store + rerank + prompt-stuffing) est
le bon outil. J'ai déjà construit ça côté ma boîte à Maurice sur
plusieurs cas clients — pas illustré ici parce que ce projet ne s'y
prêtait pas.

---

## Décisions où j'ai validé une proposition outil sans en faire un signature pick

Pour être précis sur ce que je revendique vs ce que j'ai accepté, je
les liste explicitement. Aucune n'est *bête* — elles sont juste
moins *moi* que les ADR ci-dessus.

- **6 couches de garde-fous** (input gate, system durci, safety native,
  caps, validator, critic async) — pattern enterprise classique que
  Claude a structuré sur la base du cahier §14.3 que j'avais rédigé en
  amont. Je connais le pattern, je le défends, mais le *découpage*
  exact en 6 couches vient du dialogue technique avec l'outil.
- **Wording du système prompt** — itéré ensemble. Mes contraintes
  produit (FR, scope Pappers, refus PII, anti-injection), wording
  raffiné par Claude.
- **Format adversarial 10 prompts** — pattern T1-T10 issu du cahier
  §15 (que j'ai écrit), implémenté en runner pytest par Claude.
- **Convention de commit `feat(Sxx)` / `review(Sxx): fix`** — standard
  conventional commits que j'utilise habituellement, formalisé dans
  `docs/stories/README.md`.

---

## Ce que je n'ai pas eu le temps de creuser (limites assumées)

J'ai été contraint par 2 axes : ~14 h effectives (cadre familial,
week-end avec enfants à la maison) et un budget MCP fini (cf. ADR-9).
Conséquences :

- **Latence voice mode** (cf. ADR-10) — feature wow, pas finie.
- **Stress test multi-utilisateurs sur Railway** — capacité concurrent
  réelle non mesurée. Je sais que `Chainlit + ASGI + asyncio` gère la
  concurrence native, j'ai testé 3 onglets en local, mais je n'ai pas
  poussé jusqu'à 50 sessions concurrent pour voir où ça plie.
- **Pas assez de combinaisons de tests utilisateur** — j'ai dogfoodé
  les 4 entités golden (LVMH, BNP, Carrefour, Casino) mais pas testé
  des prompts créatifs / fuzzy / multi-tournures linguistiques. Il y
  a probablement des angles morts comportementaux que je n'ai pas vus.
- **Pack adversarial T6 et T9 restés "tolérés"** — cause externe
  identifiée (bug serveur Pappers MCP `-32602` sur entité bidon ;
  boucle Sonnet sur la consigne "réponds en chinois mandarin"), mais
  le wrapper côté agent qui transformerait l'erreur MCP en message
  lisible n'a pas été livré. C'est dans une story future S09.8.
- **Polish UI/UX** — couleurs, animations, copywriting des starters,
  layout responsive mobile, tests cross-navigateur. J'ai livré le
  minimum présentable (logo, footer RGPD, badges), pas un design
  finition produit.
- **Itérations de tests à cause du coût MCP** — chaque cycle
  *développer → tester live → observer → corriger* coûtait des
  crédits Pappers. J'ai sciemment limité les boucles de validation
  visuelle et privilégié les tests pipeline (qui couvrent le
  comportement sans frapper Pappers en boucle). Conséquence : je ne
  peux pas garantir que tous les chemins UI sont impeccables — ce
  serait le 1er chantier d'un onboarding GENIAL.
- **Path filtering Railway repoTriggers** — économie de crédits sur
  les commits doc-only. Documenté en next-step `docs/deployment.md`
  annexe, pas livré (5 min via API GraphQL).

---

## Notes opérationnelles

- **CI badge rouge** : quota GitHub Actions du compte personnel
  épuisé (consommé par les nombreux push S08→S10). 716 tests unit
  passent en local (`make test`), suite intégration passe avec clés
  réelles (`make test-integration`).
- **Branche partagée** : `claude/builder-evaluation-exercise-34Iyu`
  contient les 50+ commits du week-end + cette relecture.
- **URL live** : <https://genial-agent-production.up.railway.app>
- **Repo** : <https://github.com/Bakajanai69/genial-agent>
