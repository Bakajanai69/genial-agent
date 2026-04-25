# S08 — Déploiement Railway + keep-alive UptimeRobot

> **Statut** : 🟢 raffinée (phase 1 terminée 2026-04-25) — phase 2 prête
> **Durée estimée** : 1 h (était 45 min — ré-évaluée à la hausse :
> finalisation Dockerfile, écriture `railway.json`, doc déploiement,
> configuration UptimeRobot keyword monitor, smoke test post-déploiement)
> **Parallélisable avec** : S07 (mais S08 lit `/health` qui vient de S07,
> donc partir S08 quand S07 phase 2 est mergée évite les allers-retours)

---

## 📍 Contexte

Mettre l'agent en ligne sur une URL publique HTTPS. Railway EU-West
(Amsterdam), keep-alive UptimeRobot pour éviter le cold start pendant
le week-end d'évaluation.

Sources de vérité :

- `docs/cahier-des-charges.md` §6.3 (région Railway), §10 L2/L9
  (livrables URL + healthcheck), §13 "Opérations" (URL HTTPS +
  UptimeRobot dans le DoD), §17.1 (keep-alive Railway), §17.5 (plan B).
- Story `S01-scaffold.md` — Dockerfile squelette **déjà mergé** (commit
  cc562a1), à finaliser ici.
- Story `S07-observability.md` — endpoint `/health` (4 clés, HTTP 200
  toujours, body `status` reflète l'état MCP) **déjà branché** via
  `mount_routes()` au top-level de `src/genial_agent/app.py` (ligne 58).
  S08 le **consomme** sans modification.

---

## 🔒 Prérequis

- [x] S01 mergée (Dockerfile squelette + `.dockerignore` partiel +
      Makefile `docker-build`).
- [x] S07 mergée (`/health` riche prepend-é sur `chainlit.server.app` ;
      contrat 4 clés vérifié par `tests/unit/test_S07_routes.py`).
- [x] `.env` local valide (3 clés API testées E2E, cf. README global).

## 🔑 Inputs utilisateur requis

À cocher au fur et à mesure que Lancelot fournit / configure chaque élément.

- [ ] **Compte Railway créé** (GitHub SSO recommandé pour lier le repo
      sans token PAT). Vérification : `railway whoami` côté CLI ou
      profile Railway dashboard.
- [ ] **Projet Railway créé** et lié au repo
      `Bakajanai69/genial-agent`. Service unique (pas de DB, pas de
      Redis — cf. cahier §9 scope négatif).
- [ ] **Plan tarifaire confirmé** :
  - Plan **Hobby** (5 $/mois) → pas de sleep, ressources confortables.
    Recommandé pour la démo si Lancelot a un crédit gratuit ou accepte
    le coût ponctuel.
  - Plan **Trial** (gratuit, $5 de crédits) → sleep après ~10 min
    d'inactivité, **keep-alive UptimeRobot indispensable** pour éviter
    le cold start dimanche.
  - Décision : peu importe lequel, l'architecture S08 (keep-alive
    actif) couvre les deux.
- [ ] **Variables d'env recopiées** dans Railway → Variables du
      service. Liste exhaustive (déjà validées localement —
      cf. `.env.example`) :
  - `ANTHROPIC_API_KEY` (Haiku 4.5 + Sonnet 4.6).
  - `PAPPERS_API_KEY` (handshake MCP OK, 31 tools exposés en S02).
  - `ELEVENLABS_API_KEY` (tier `growing_business`, quota large —
    inutilisé tant que `ENABLE_VOICE_BRIEF=false`).
  - `ELEVENLABS_VOICE_GAELLE=tKaoyJLW05zqV0tIH9FD`.
  - `ELEVENLABS_VOICE_GUILLAUME=ohItIVrXTBI80RrUECOD`.
  - `ELEVENLABS_MODEL_ID=eleven_multilingual_v2`.
  - `ENABLE_VOICE_BRIEF=false` (à flipper `true` **après** merge S10 +
    gating §19.1 vert).
  - `LOG_LEVEL=INFO`.
  - **Optionnel** `STATS_TOKEN=<token>` — recommandé en prod pour
    auth-gate `/stats` (cf. `routes.py:69` review B3, comparaison
    timing-safe `hmac.compare_digest`). Générer via :
    `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
  - **Pas** de `WALL_CLOCK_S_OVERRIDE` côté Railway : la prod tourne
    en EU-West avec latence basse, le cap cahier 15 s tient.
- [ ] **Compte UptimeRobot créé** (plan Free OK — 50 monitors max,
      intervalle min 5 min, keyword monitor inclus en GET).
- [ ] **Domaine Railway noté** dans `docs/deployment.md` (et plus tard
      `docs/stories/README.md` ligne S08, après merge phase 3).

> ⚠️ **Ne jamais coller les clés dans un canal Railway public** (issue,
> PR, log). Les ajouter uniquement via le panel Variables (masquées par
> défaut). Idem côté UptimeRobot : ne pas mettre la clé Pappers dans
> le keyword monitor.

---

## 🎯 Scope

### Dans le scope

- Finalisation du `Dockerfile` (multi-stage, slim, non-root, healthcheck
  Docker, shell-form CMD pour expansion `${PORT}` Railway, flag
  `chainlit run -h` pour empêcher l'ouverture browser server-side).
- Création de `railway.json` avec schéma 2026 (`$schema:
  https://railway.com/railway.schema.json`), `multiRegionConfig`
  Amsterdam (`europe-west4-drams3a`), `healthcheckPath: "/health"`,
  `restartPolicyType: ON_FAILURE`, `sleepApplication: false`.
- `.dockerignore` durci pour exclure `.env`, `.venv`, `tests/`, doc,
  caches.
- Build Docker local validé avant push (`make docker-build` + `docker
  run` + `curl /health` 200).
- Déploiement initial via Railway CLI (`railway up`) **ou** via auto-
  deploy GitHub (déclenché au push sur la branche). MVP : auto-deploy
  pour tracer le build dans le commit history.
- URL publique HTTPS fonctionnelle : ouvre l'UI Chainlit, le
  `/health` répond JSON 4 clés, le 1er chat passe.
- UptimeRobot configuré : monitor **Keyword** type `exists` sur
  `https://<domain>/health` avec keyword `"status":"ok"`, intervalle
  5 min, alerting email Lancelot.
- `docs/deployment.md` : procédure complète (création projet, vars,
  région, premier push, récupération URL, UptimeRobot, troubleshooting).
- README mis à jour avec le badge URL + le pointeur vers
  `docs/deployment.md`.
- Test smoke post-deploy (script bash documenté, idempotent).

### Hors scope

- CI/CD multi-environnements (staging + prod). MVP : un seul service
  prod sur la branche `claude/builder-evaluation-exercise-34Iyu`.
- Auto-scaling, multi-replicas (besoin sticky sessions WebSocket
  Chainlit). Single replica EU-West suffit pour Fabien + son équipe.
- Custom domain (`genial-agent.lancelotoudin.fr` ou similaire). MVP :
  `*.up.railway.app` suffit, plus rapide à mettre en place.
- Mise en place CDN devant l'app (Cloudflare). Hors scope week-end.
- Migration vers Bedrock EU pour résidence RGPD (documenté §6.2 du
  cahier comme next step, ~20 lignes de bascule).
- Tracing Langfuse / OpenTelemetry — `next step` README S09.

---

## 🧭 Phase 1 — Elicitation Agent

### ✅ Conclusions elicitation (2026-04-25)

Recherches effectuées via :

- [Railway Config as Code](https://docs.railway.com/reference/config-as-code) (schéma + champs).
- [Railway Deployment Regions](https://docs.railway.com/reference/deployment-regions) (codes région).
- [Railway "Application Failed to Respond"](https://docs.railway.com/networking/troubleshooting/application-failed-to-respond) (gotchas PORT / 0.0.0.0).
- [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles) (détection, ARG, cache mounts).
- [Schéma JSON officiel](https://backboard.railway.app/railway.schema.json) — ouvert directement pour figer les enums.
- [Chainlit Deploy Overview](https://docs.chainlit.io/deploy/overview) (flag `-h`, host 0.0.0.0, websockets sticky).
- [UptimeRobot Pricing 2026](https://www.saaspricepulse.com/tools/uptimerobot) + [Keyword Monitoring](https://uptimerobot.com/keyword-monitoring/) (free 50 monitors, 5 min, GET).

#### ⚖️ Décision A — `railway.json` plutôt que `railway.toml`

Les deux formats sont supportés par Railway. JSON :

- **Choisi** : compatible avec `$schema` IDE autocomplete (VSCode,
  PyCharm le picke automatiquement via ce champ → DX immédiat).
- TOML aurait offert des commentaires inline, mais la config est
  courte (15 lignes) — pas de gain pratique.

#### ⚖️ Décision B — `multiRegionConfig` plutôt que `region` simple

Le schéma 2026 expose **les deux** :

- `deploy.region: "europe-west4-drams3a"` — historique, single region.
- `deploy.multiRegionConfig: { "<region>": { "numReplicas": N } }` —
  nouveau pattern, multi-region ready, supporte `numReplicas` par
  région. C'est ce que Railway documente comme exemple à jour
  ([repo `railwayapp/docs`](https://github.com/railwayapp/docs/blob/main/railway.json)
  l'utilise pour ses propres docs).

**Choix** : `multiRegionConfig` avec une seule entrée `europe-west4-drams3a`.
Future-proof : ajouter une région secondaire = ajouter une clé.

```json
"deploy": {
  "multiRegionConfig": {
    "europe-west4-drams3a": { "numReplicas": 1 }
  }
}
```

#### 📍 Code région Amsterdam confirmé

| Code Railway | Localisation | Source |
|---|---|---|
| `europe-west4-drams3a` | **Amsterdam** | docs.railway.com/reference/deployment-regions |
| `us-west2` | California | id. |
| `us-east4-eqdc4a` | Virginia | id. |
| `asia-southeast1-eqsg3a` | Singapore | id. |

Le suffixe `drams3a` = identifiant data-center interne Railway. **Ne
pas tenter** d'autres variantes (`europe-west`, `eu-west`, `amsterdam`)
— l'API Railway accepte uniquement les codes ci-dessus.

#### 🔌 PORT et binding — gotchas Railway

- Railway injecte la variable d'env **`PORT`** (valeur par défaut
  observée : `8080`). Notre Dockerfile pose `ENV PORT=8000` qui sert
  de **fallback local** ; Railway l'override au runtime.
- L'app **doit binder sur `0.0.0.0`** (pas `127.0.0.1`) — sinon
  "Application failed to respond" garanti.
- L'`EXPOSE 8000` est purement déclaratif (Railway ignore et utilise
  PORT). On le garde pour `docker run -p 8000:8000` local.
- **Critique** : `${PORT}` ne s'expand que si CMD est en **shell-form**
  (`CMD ["sh", "-c", "..."]`). En exec-form (`CMD ["chainlit",
  "run", "--port", "${PORT}"]`), la var n'est pas expansée et
  Chainlit crashe avec "invalid port `${PORT}`". Le Dockerfile S01
  est déjà en exec-form → **changement requis** en shell-form.

#### 🛡️ Healthcheck Railway vs Docker

Deux mécaniques distinctes — bien les séparer :

| Mécanisme | Configuré dans | Rôle | Action sur échec |
|---|---|---|---|
| `HEALTHCHECK` Dockerfile | `Dockerfile` | local `docker run` (status `healthy`/`unhealthy`) | Aucune (info seulement) |
| `healthcheckPath` Railway | `railway.json` | gate du déploiement (post-build) + redéploi à la coupure | Nouveau déploiement / restart |

Pour Railway, on configure :

```json
"healthcheckPath": "/health",
"healthcheckTimeout": 60,
"restartPolicyType": "ON_FAILURE",
"restartPolicyMaxRetries": 3
```

`healthcheckTimeout: 60` = Railway attend **60 s max** que `/health`
réponde 2xx au boot avant de marquer le déploiement KO. Marge
confortable vs notre `/health` qui répond en < 3 s grâce au timeout
interne MCP (`routes.py:_HEALTH_MCP_TIMEOUT_S = 3.0`).

**Important** : notre `/health` retourne **toujours HTTP 200**, même
quand `body.status == "ko"` (cf. décision S07 — un flap MCP transient
ne doit pas page UptimeRobot ni redéployer Railway). Conséquence :
Railway considère l'app `healthy` dès que le code 200 sort. Le statut
métier "MCP up/down" est porté par UptimeRobot via keyword match (cf.
décision E ci-dessous).

#### 🔑 UptimeRobot — keyword monitor en GET (free plan OK)

- Plan **Free** 2026 : 50 monitors, intervalle **min 5 min**, monitors
  HTTP/Keyword/Ping inclus, GET only (les méthodes POST/PUT/PATCH +
  body JSON sont Pro-only).
- Keyword monitor = GET, scanne le **body de la réponse** pour la
  présence (ou absence) d'un mot.
- Notre `/health` retourne :
  ```json
  {"status":"ok","mcp":{...},"version":"0.1.0","uptime_s":42}
  ```
  → on configure le keyword monitor pour **alerter quand `"status":"ok"`
  n'est PLUS présent** (mode "Keyword does not exist").

**Gotcha** : UptimeRobot supporte les caractères spéciaux dans le
keyword (guillemets, deux-points). Mais pour robustesse, on utilise
le keyword `"status":"ok"` (avec les guillemets et le `:`) — plus
spécifique que juste `ok` qui pourrait apparaître ailleurs (ex :
`"tools":[{"name":"ok_check"...}]` dans un futur retour).

Configuration UptimeRobot précise :

| Champ | Valeur |
|---|---|
| Monitor Type | **Keyword** |
| URL (or IP) | `https://<railway-domain>/health` |
| Keyword Type | **Keyword does not exist** |
| Keyword | `"status":"ok"` |
| HTTP Method | `GET` (par défaut) |
| Monitoring Interval | **5 minutes** |
| Alert Contacts | email Lancelot |

> Bénéfice : UptimeRobot alertera **dès** que le MCP Pappers tombe
> (le body passe à `"status":"ko"` → keyword absent → alerte). On
> capte aussi les downtime Railway (timeout HTTP).

#### 🐳 Chainlit prod — flag `-h`

[Doc Chainlit](https://docs.chainlit.io/deploy/overview) : *"When
running a Chainlit app in production, you should always add `-h` to
the chainlit run command"*. Sans `-h`, Chainlit tente d'ouvrir un
navigateur côté serveur → log d'erreur bénin mais pollue la sortie.

Notre `CMD` actuel S01 :
```
CMD ["chainlit", "run", "src/genial_agent/app.py", "--host", "0.0.0.0", "--port", "8000"]
```

Devient en S08 (shell-form + `-h` + `${PORT}`) :
```
CMD ["sh", "-c", "chainlit run src/genial_agent/app.py -h --host 0.0.0.0 --port ${PORT:-8000}"]
```

Le `${PORT:-8000}` garde un fallback si PORT n'est pas défini (build
local sans Railway).

#### 🧹 `.chainlit/config.toml` à embarquer dans l'image

S06 a versionné des customizations :

- `allow_origins = ["*"]` — **à durcir** post-déploiement (review S06
  flagué). Décision S08 : on **garde `["*"]` pour la démo** (Fabien
  doit pouvoir tester depuis n'importe où, y compris un proxy
  d'entreprise) et on documente le durcissement comme `next step`.
- `[features.mcp.*]` désactivé (défense en profondeur S06).
- `unsafe_allow_html = false`, `spontaneous_file_upload.enabled = false`.

Sans ces réglages, Chainlit en prod tournerait avec ses défauts
(spontaneous upload activé, ports MCP côté client activés). **Le
Dockerfile doit COPY `.chainlit/config.toml`** — le `.dockerignore`
S01 actuel ignore tout `.chainlit/*` : à corriger en S08.

#### 🧪 Sticky sessions / WebSockets

Chainlit utilise WebSockets pour le streaming. En multi-replica avec
load balancer : sticky sessions requis (sinon les frames WebSocket
peuvent atterrir sur un autre replica que celui qui a la session).

Railway en single replica + ingress unique = pas de problème. **MVP :
1 replica EU-West, pas de config sticky session nécessaire.** Le
header `X-Chainlit-Session-id` est posé automatiquement par Chainlit
2.10+ — utile uniquement si on scale plus tard. Documenté en next
step.

#### 📦 Taille image cible

Mesurée localement après `make docker-build` sur le squelette S01 :

```
$ docker images | grep genial-agent
genial-agent   local   <hash>   ~310 MB
```

Marge confortable vs cible 500 MB. Composantes :

- `python:3.12-slim-bookworm` base : ~125 MB.
- `.venv` builder copié (anthropic + mcp + chainlit + pydantic +
  structlog + tenacity + python-dotenv + transitives) : ~155 MB.
- `src/` : < 1 MB.
- `curl` (ajouté en S08 pour `HEALTHCHECK`) : ~3 MB.

**Pas de bump nécessaire** (target < 400 MB respecté).

#### 🧱 `.dockerignore` à corriger

État S01 actuel (à inspecter — la story S08 originale pose un nouveau
contenu, on le valide) :

```
.git/
.venv/
.env
.env.*
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
.chainlit/                 ← BUG : exclut config.toml versionné
docs/
tests/
*.md
!README.md
!chainlit.md
```

**Correction S08** : ne pas exclure `.chainlit/` en bloc, lister les
sous-paths à exclure. Idem que la stratégie `.gitignore` :

```
.git/
.venv/
.env
.env.*
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Chainlit — exclure les artefacts générés mais GARDER config.toml,
# translations/ et public/ versionnés (cf. S06 customizations).
# .chainlit/config.toml est inclus implicitement (pas dans la liste
# d'exclusion). On exclut uniquement les sous-paths runtime :
.chainlit/.session_files/
.files/

# Documentation et tests : pas dans l'image runtime.
docs/
tests/
*.md
!chainlit.md          # affiché dans l'UI au démarrage (cf. S06)

# Caches OS / IDE
.DS_Store
.idea/
.vscode/
*.swp
```

> Note : `README.md` est exclu de l'image (le `*.md` + l'absence
> d'une réinclusion `!README.md`) — le `pyproject.toml` cite
> `readme = "README.md"` mais ce champ ne sert qu'à PyPI build, pas
> au runtime Chainlit. Le COPY de `README.md` du Dockerfile S01 reste
> nécessaire pour `uv sync` (hatchling vérifie le readme à
> l'install). On garde donc `COPY pyproject.toml uv.lock README.md
> ./` dans le builder stage. Le `.dockerignore` n'empêche pas le
> COPY explicite (il filtre le contexte de build mais pas les fichiers
> nommément cités après — wait, c'est faux). Test : si `.dockerignore`
> exclut `README.md`, le `COPY pyproject.toml uv.lock README.md ./`
> échouera (`failed to compute cache key: README.md not found`).
> **Solution** : ajouter `!README.md` à `.dockerignore` pour le
> ré-inclure.

Version finale `.dockerignore` :

```
.git/
.venv/
.env
.env.*
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
.chainlit/.session_files/
.files/
docs/
tests/
*.md
!README.md
!chainlit.md
.DS_Store
.idea/
.vscode/
*.swp
```

#### 🚀 Stratégie de déploiement — push GitHub vs Railway CLI

Deux options évaluées :

| Option | Avantage | Inconvénient |
|---|---|---|
| **Auto-deploy GitHub** | trail commit-by-commit, pas de CLI à installer, feature standard Railway | Dépend du trigger Railway (peut prendre 30-60 s à kicker) |
| **Railway CLI (`railway up`)** | Contrôle direct, pas de mismatch branch | Demande `npm i -g @railway/cli` ou Docker-based, état CLI à valider |

**Choix** : auto-deploy GitHub. Lancelot lie le repo via Railway
dashboard (1 clic GitHub OAuth), branche `claude/builder-evaluation-
exercise-34Iyu` configurée comme branche de déploiement. Chaque push
déclenche un build+deploy automatique.

CLI mentionnée dans `docs/deployment.md` comme **fallback** si la
console UI Railway plante.

#### 📑 `docs/deployment.md` — squelette validé

Sections retenues (Lancelot ou un dev tiers doit pouvoir relancer
le déploiement de zéro) :

1. **Pré-requis** : compte Railway, compte UptimeRobot, repo Github
   accessible.
2. **Création projet Railway** :
   - New Project → Deploy from GitHub repo → autoriser GitHub OAuth.
   - Sélectionner repo + branche `claude/builder-evaluation-exercise-34Iyu`.
   - Région : laisser le défaut, on l'override par `railway.json`.
3. **Variables d'env** : capture d'écran type + checklist des 9 vars
   obligatoires.
4. **Vérification région** post-déploiement : `Settings → Region` doit
   montrer `Europe West (Amsterdam)`.
5. **Premier deploy** : Railway build au push. Suivre les logs
   `Deployments → Build Logs` pour debug.
6. **Récupération URL** : `Settings → Networking → Generate Domain` →
   noter `https://genial-agent-production.up.railway.app` (exemple).
7. **Smoke test** :
   ```bash
   curl -fsS https://<domain>/health | jq
   # → {"status":"ok","mcp":{"status":"ok",...},"version":"0.1.0",...}
   ```
8. **UptimeRobot** : New Monitor → Keyword type → URL `<domain>/health`
   → Keyword `"status":"ok"` → "Keyword does not exist" → 5 min →
   alert email.
9. **Troubleshooting** :
   - **Application Failed to Respond** : check binding 0.0.0.0 + PORT
     env (cf. décision Railway gotchas).
   - **Build qui timeout** : `uv sync --frozen` peut échouer si
     `uv.lock` non committé → vérifier `git ls-files | grep uv.lock`.
   - **Healthcheck échoue** : ouvrir les logs Railway, vérifier
     que Pappers répond (clé API valide).
   - **Région ignorée** : Railway peut prendre la région globale du
     compte si `multiRegionConfig` est absent ou mal formé. Vérifier
     `Settings → Region` dans le dashboard.
10. **Rollback** : Railway garde les déploiements précédents
    (`Deployments → ...`). Cliquer "Redeploy" sur un déploiement N-1
    rollback en < 30 s.

#### 🧪 Tests — stratégie

Trois niveaux :

1. **Unit `test_S08_railway_config.py`** : parse `railway.json`,
   asserte que :
   - `$schema == "https://railway.com/railway.schema.json"`.
   - `build.builder == "DOCKERFILE"`.
   - `build.dockerfilePath == "Dockerfile"`.
   - `deploy.healthcheckPath == "/health"`.
   - `deploy.healthcheckTimeout >= 30` (assez de marge).
   - `deploy.restartPolicyType in {"ON_FAILURE","ALWAYS"}`.
   - `deploy.multiRegionConfig` contient bien
     `"europe-west4-drams3a"`.
   - `deploy.sleepApplication is False` (explicit anti-sleep).
2. **Unit `test_S08_dockerfile.py`** : grep le Dockerfile pour :
   - Présence d'un `USER agent` non-root.
   - CMD en shell-form (`["sh", "-c", ...]`).
   - Présence du flag `-h` dans la commande chainlit.
   - Présence d'un `HEALTHCHECK` directive.
   - Pas de `ENV ANTHROPIC_API_KEY` ou autre secret hardcodé.
3. **Integration `test_S08_docker.py`** (skip si Docker absent) :
   build l'image, lance le conteneur, attend `/health` 200 < 30 s,
   vérifie le body 4 clés. Skip si `ANTHROPIC_API_KEY` ou
   `PAPPERS_API_KEY` absent. Marker `integration`, opt-in via
   `make test-integration`.

Pas de test live "post-déploiement Railway" automatisé : c'est manuel,
documenté dans `docs/deployment.md` étape 7.

#### 🔐 Sécurité — durcissements à inscrire dans la review S08

- `gitleaks` pré-commit déjà actif (S01) — vérification post-commit
  S08 obligatoire.
- L'`Authorization: Bearer` du `/stats` est déjà timing-safe (S07
  review B3). Si Lancelot configure `STATS_TOKEN` côté Railway, le
  noter dans le README ("`/stats` protégé en prod").
- `.dockerignore` couvre `.env*` (vérifié dans `_purge_existing_routes`
  équivalent S07 — non, c'est S08, nouveau test à ajouter).
- Le Dockerfile **ne doit jamais** contenir de `ENV ANTHROPIC_API_KEY=…`
  ni `ARG …_KEY=…`. Vérification grep dans le test unit.
- Image en uid 1000 non-root (déjà OK S01).

### Points résolus

- [x] **Format railway.json** : JSON, schéma `https://railway.com/railway.schema.json`.
- [x] **Région Amsterdam** : `europe-west4-drams3a` via
      `multiRegionConfig` (pattern 2026, future-proof).
- [x] **PORT** : injecté par Railway (8080 par défaut), shell-form CMD
      avec `${PORT:-8000}` fallback.
- [x] **Host binding** : `--host 0.0.0.0` obligatoire (Railway gotcha).
- [x] **Healthcheck Railway** : `/health` (S07), timeout 60 s,
      restart `ON_FAILURE` 3 retries.
- [x] **Healthcheck Docker** : conservé via directive `HEALTHCHECK`
      (info-only, utile en local).
- [x] **Chainlit `-h`** : ajouté à la commande pour empêcher l'ouverture
      browser server-side.
- [x] **`.chainlit/config.toml`** : doit être COPY-é dans l'image
      (S06 customizations versionnées).
- [x] **`.dockerignore`** : revu pour ne pas exclure `.chainlit/config.toml`
      ni `README.md` (requis par hatchling à `uv sync`).
- [x] **UptimeRobot** : Keyword monitor `"status":"ok"` en mode
      "does not exist", interval 5 min, plan Free OK.
- [x] **Sticky sessions** : non requis en single replica (MVP).
      Documenté next step.
- [x] **`sleepApplication: false`** : explicite dans `railway.json`,
      même si défaut, pour éviter qu'un toggle dashboard ne casse la
      démo silencieusement.
- [x] **CLI vs auto-deploy** : auto-deploy GitHub (tracé commit-by-commit).
- [x] **Tests** : unit `railway.json` parse + Dockerfile grep, integration
      `docker run + curl /health` opt-in.

### Commit phase 1

`story(S08): refine — railway.json schema 2026, multiRegionConfig Amsterdam, Dockerfile shell-form $PORT, UptimeRobot keyword`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

**Créer** :

- `railway.json` (config as code).
- `docs/deployment.md` (procédure complète — cf. squelette §
  "`docs/deployment.md`" ci-dessus).
- `tests/unit/test_S08_railway_config.py`.
- `tests/unit/test_S08_dockerfile.py`.
- `tests/integration/test_S08_docker.py` (marker `integration`,
  skip si Docker absent ou si une des clés API manque).

**Modifier** :

- `Dockerfile` (shell-form CMD + `-h` flag + `HEALTHCHECK` directive
  + `apt-get install curl` côté runtime stage + `COPY .chainlit/config.toml`).
- `.dockerignore` (créer s'il n'existe pas — vérifier d'abord ;
  S01 ne l'avait pas listé explicitement, donc à créer).
- `README.md` : section "Déploiement" pointant vers `docs/deployment.md`
  + ajout du badge URL Railway une fois obtenue.
- `.env.example` : ajout du commentaire pour `STATS_TOKEN` recommandé
  en prod (S07 a déjà la ligne, S08 confirme).
- `docs/stories/README.md` : ligne S08 → ✅ après merge.

### `Dockerfile` final

```dockerfile
# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12

# ─── Stage builder ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
WORKDIR /app

# uv via image officielle Astral (évite pip install + cache miss)
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

# Layer cache : pyproject.toml + uv.lock + README.md (hatchling lit
# ce dernier au build — si .dockerignore l'exclut, COPY échoue).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Sync sans dev deps, sans editable install (image plus légère)
RUN uv sync --frozen --no-dev --no-editable

# ─── Stage runtime ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm

# curl requis par HEALTHCHECK Docker (slim-bookworm ne l'a pas).
# --no-install-recommends garde l'image fine ; on rm les apt lists
# pour économiser ~10 MB.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 agent

USER agent
WORKDIR /app

# Copy venv + sources + Chainlit assets (config + chainlit.md + public)
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --chown=agent:agent src ./src
COPY --chown=agent:agent chainlit.md ./
COPY --chown=agent:agent public ./public
COPY --chown=agent:agent .chainlit ./.chainlit

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# EXPOSE déclaratif pour `docker run -p 8000:8000`. Railway ignore
# cette directive et bind sur le PORT injecté (8080 par défaut).
EXPOSE 8000

# HEALTHCHECK = info-only en local (`docker ps` montre healthy/unhealthy).
# Railway utilise son propre healthcheck via railway.json (healthcheckPath).
# Le ${PORT} se résout au runtime via le shell de l'instruction.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8000}/health" >/dev/null || exit 1

# Shell-form CMD : ${PORT} expansé par sh. Avec exec-form, Chainlit
# recevrait littéralement la chaîne "${PORT}" et crasherait.
# Flag -h : empêche Chainlit d'ouvrir un browser côté serveur (cf.
# docs.chainlit.io/deploy/overview).
CMD ["sh", "-c", "chainlit run src/genial_agent/app.py -h --host 0.0.0.0 --port ${PORT:-8000}"]
```

> **Choix `--start-period=15s`** : le boot Chainlit + le 1er ping
> Pappers depuis `routes.py:health` peut prendre ~5-8 s à froid.
> Sans `start-period`, Docker marquerait le conteneur `unhealthy`
> dès la 1ère retry échouée (avant que l'app ait fini de démarrer).

### `railway.json`

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "healthcheckPath": "/health",
    "healthcheckTimeout": 60,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3,
    "sleepApplication": false,
    "multiRegionConfig": {
      "europe-west4-drams3a": {
        "numReplicas": 1
      }
    }
  }
}
```

> Pas de `startCommand` : Railway utilise le `CMD` du Dockerfile.
> Pas de `region` simple : `multiRegionConfig` couvre le besoin et est
> le pattern 2026 documenté.

### `.dockerignore` final

```
# Secrets et venv local — JAMAIS dans l'image
.env
.env.*
.venv/
venv/
env/

# Git
.git/
.gitignore
.gitleaks.toml
.github/

# Caches
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/
.cache/

# Chainlit runtime artifacts (mais on garde config.toml et translations
# versionnés — cf. S06)
.chainlit/.session_files/
.files/

# Documentation et tests : hors image runtime
docs/
tests/
chainlit.md.bak

# Markdown : exclu sauf README (requis par hatchling) et chainlit.md
# (affiché dans l'UI au démarrage)
*.md
!README.md
!chainlit.md

# OS / IDE
.DS_Store
.idea/
.vscode/
*.swp
*.swo

# CI workflows hors image
.python-version
.pre-commit-config.yaml

# Local dumps
*.log
*.local

# Image et artefacts Docker
Dockerfile
.dockerignore
```

> `Dockerfile` et `.dockerignore` peuvent être exclus du contexte de
> build (Docker les lit avant le COPY, ils ne servent à rien dans
> l'image runtime). C'est cosmétique — n'impacte pas la taille.

### Tests à produire

#### `tests/unit/test_S08_railway_config.py`

```python
"""Validation statique de ``railway.json`` (S08).

On parse le fichier comme JSON pur et on assert sur les champs critiques :

- builder Docker.
- healthcheck path et timeout cohérents avec /health (S07).
- politique de restart bornée.
- région Amsterdam explicite via ``multiRegionConfig``.
- pas de sleep (cahier §17.1 keep-alive).
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAILWAY_JSON = REPO_ROOT / "railway.json"


def _load() -> dict:
    return json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))


def test_schema_url_pinned() -> None:
    cfg = _load()
    assert cfg["$schema"] == "https://railway.com/railway.schema.json"


def test_dockerfile_builder() -> None:
    cfg = _load()
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["build"]["dockerfilePath"] == "Dockerfile"


def test_healthcheck_targets_S07_endpoint() -> None:
    cfg = _load()
    deploy = cfg["deploy"]
    assert deploy["healthcheckPath"] == "/health"
    # Marge confortable vs notre cap interne 3s sur le ping MCP
    # (routes.py:_HEALTH_MCP_TIMEOUT_S) + le boot Chainlit.
    assert deploy["healthcheckTimeout"] >= 30


def test_restart_policy_bounded() -> None:
    cfg = _load()
    deploy = cfg["deploy"]
    assert deploy["restartPolicyType"] in {"ON_FAILURE", "ALWAYS"}
    assert 1 <= deploy["restartPolicyMaxRetries"] <= 10


def test_region_amsterdam_explicit() -> None:
    cfg = _load()
    multi = cfg["deploy"]["multiRegionConfig"]
    assert "europe-west4-drams3a" in multi, (
        "La région Amsterdam (europe-west4-drams3a) doit être présente "
        "dans multiRegionConfig — cf. cahier §6.3."
    )
    # Single replica MVP (sticky sessions hors scope).
    assert multi["europe-west4-drams3a"]["numReplicas"] == 1


def test_sleep_disabled() -> None:
    """Cahier §17.1 : keep-alive UptimeRobot pour éviter le cold start.
    L'inverse — laisser sleepApplication à True — réveille le débat sur
    le Hobby vs Trial plan. On le force à False côté config-as-code."""
    cfg = _load()
    assert cfg["deploy"].get("sleepApplication") is False
```

#### `tests/unit/test_S08_dockerfile.py`

```python
"""Vérifications statiques du ``Dockerfile`` (S08).

On ne build pas l'image (test integration séparé) — on grep le contenu
pour les invariants critiques : non-root, shell-form CMD, flag -h
Chainlit, HEALTHCHECK directive, pas de secret hardcodé.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _read() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_exists() -> None:
    assert DOCKERFILE.is_file()


def test_non_root_user() -> None:
    content = _read()
    assert re.search(r"^USER\s+agent\s*$", content, re.MULTILINE), (
        "Le runtime stage doit basculer en USER agent (uid 1000)."
    )
    assert "useradd" in content


def test_cmd_shell_form_for_port_expansion() -> None:
    """Railway injecte $PORT au runtime. L'expansion ne fonctionne
    qu'en shell-form CMD (sh -c '...'). En exec-form, ${PORT} est
    passé littéralement à Chainlit qui crashe."""
    content = _read()
    # Cherche un CMD qui commence par ["sh", "-c", ...]
    assert re.search(r'CMD\s*\[\s*"sh"\s*,\s*"-c"', content), (
        "CMD doit être en shell-form ['sh', '-c', '…'] pour expansion ${PORT}."
    )
    assert "${PORT" in content, "Le CMD doit référencer ${PORT}."


def test_chainlit_dash_h_flag() -> None:
    """Cf. docs.chainlit.io/deploy/overview — `-h` empêche l'ouverture
    browser server-side en prod."""
    content = _read()
    assert re.search(r"chainlit\s+run[^\n]*\s-h\b", content), (
        "Le flag -h doit être présent dans la commande chainlit run."
    )


def test_healthcheck_directive_present() -> None:
    content = _read()
    assert re.search(r"^HEALTHCHECK\s", content, re.MULTILINE)
    assert "/health" in content


def test_no_hardcoded_secret() -> None:
    """Aucune clé API ne doit fuiter via ENV/ARG dans l'image."""
    content = _read()
    forbidden = [
        "ANTHROPIC_API_KEY=",
        "PAPPERS_API_KEY=",
        "ELEVENLABS_API_KEY=",
        "STATS_TOKEN=",
    ]
    # On accepte ENV PORT=… et ARG PYTHON_VERSION=… (pas de secret).
    for pat in forbidden:
        assert pat not in content, f"Secret hardcodé détecté : {pat!r}"


def test_chainlit_config_copied() -> None:
    """`.chainlit/config.toml` (S06 customizations) doit être dans l'image."""
    content = _read()
    assert re.search(r"COPY[^\n]*\.chainlit", content), (
        ".chainlit/ doit être COPY-é dans le runtime stage."
    )
```

#### `tests/integration/test_S08_docker.py`

```python
"""Build + run du conteneur, smoke /health (marker integration).

Skip si :
- ``docker`` introuvable dans le PATH ;
- ``ANTHROPIC_API_KEY`` ou ``PAPPERS_API_KEY`` absent (Pappers MCP
  ping requis pour /health → status:ok).

Test opt-in : ``make test-integration``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture(scope="module")
def _api_keys_present() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))


@pytest.fixture(scope="module")
def docker_container(_docker_available, _api_keys_present):
    if not _docker_available:
        pytest.skip("Docker non disponible — install + start docker daemon.")
    if not _api_keys_present:
        pytest.skip("ANTHROPIC_API_KEY ou PAPPERS_API_KEY absent.")

    image = "genial-agent:test-S08"
    subprocess.run(
        ["docker", "build", "-t", image, "."],
        check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    )

    cid = subprocess.check_output(
        [
            "docker", "run", "-d", "--rm",
            "-p", "8765:8000",
            "-e", f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}",
            "-e", f"PAPPERS_API_KEY={os.environ['PAPPERS_API_KEY']}",
            "-e", "LOG_LEVEL=INFO",
            "-e", "ENABLE_VOICE_BRIEF=false",
            image,
        ],
        text=True,
    ).strip()

    # Attente boot — max 30 s
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + 30
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:8765/health", timeout=2) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, ConnectionRefusedError, TimeoutError) as exc:
            last_err = exc
            time.sleep(1)
    else:
        subprocess.run(["docker", "logs", cid], check=False)
        subprocess.run(["docker", "stop", cid], check=False)
        pytest.fail(f"/health pas up en 30 s : {last_err}")

    yield cid

    subprocess.run(["docker", "stop", cid], check=False)


def test_health_returns_ok(docker_container):
    import json
    import urllib.request

    with urllib.request.urlopen("http://localhost:8765/health", timeout=5) as r:
        body = json.loads(r.read())
    assert r.status == 200
    assert set(body.keys()) >= {"status", "mcp", "version", "uptime_s"}
    assert body["status"] == "ok"
    assert body["mcp"]["tools_count"] >= 1


def test_chainlit_root_serves(docker_container):
    """`/` doit servir l'UI Chainlit — preuve que le catch-all natif
    n'est pas shadow-é par notre prepend route."""
    import urllib.request

    with urllib.request.urlopen("http://localhost:8765/", timeout=5) as r:
        assert r.status == 200
        body = r.read().decode("utf-8", errors="ignore")
    # L'UI Chainlit charge index.html avec quelques signes distinctifs.
    assert "<html" in body.lower()
```

### Smoke test post-déploiement (manuel, dans `docs/deployment.md`)

```bash
# 1. Health endpoint
DOMAIN="genial-agent-production.up.railway.app"  # à remplacer
curl -fsS "https://${DOMAIN}/health" | jq

# Doit afficher :
# {
#   "status": "ok",
#   "mcp": {"status": "ok", "latency_ms": 250, "tools_count": 31, "error": null},
#   "version": "0.1.0",
#   "uptime_s": 12
# }

# 2. UI Chainlit accessible
curl -fsI "https://${DOMAIN}/" | grep -E "HTTP|content-type"
# → HTTP/2 200 + content-type: text/html

# 3. Stats compteurs
curl -fsS "https://${DOMAIN}/stats" | jq
# → JSON avec uptime_s, total_turns (0 au boot), etc.
# → Si STATS_TOKEN configuré : ajouter -H "Authorization: Bearer <token>"
```

### Modifications complémentaires

#### `README.md` — section Déploiement

Ajouter sous "Configuration" :

```markdown
## Déploiement

L'agent tourne en prod sur **Railway EU-West (Amsterdam)** :

🌐 **URL publique** : https://<railway-domain>  *(à compléter post-deploy)*

Config-as-code dans `railway.json` (région, healthcheck, restart).
Procédure complète + troubleshooting dans
[`docs/deployment.md`](docs/deployment.md).

Keep-alive **UptimeRobot** (plan Free) ping `/health` toutes les
5 min pour empêcher le cold start pendant le week-end d'évaluation.
```

#### `docs/stories/README.md` — checklist S08

Cocher au fur et à mesure :

```markdown
### Avant S08 (déploiement)

- [x] Compte Railway créé (GitHub SSO OK), repo `Bakajanai69/genial-agent`
      lié comme projet.
- [x] Region EU-West (Amsterdam) confirmée dans le projet Railway.
- [x] **Recopier** toutes les variables du `.env` local dans Railway
      Project Variables (identiques à celles déjà validées localement).
- [x] Compte UptimeRobot créé (plan free) + URL `/health` du déploiement
      Railway configurée en ping 5 min.
```

### Commandes de vérification (avant commit phase 2)

```bash
# Static checks
make lint
make test-unit                                        # tests/unit S08 verts

# Docker build local — image < 400 MB
docker build -t genial-agent:s08 .
docker images | grep genial-agent
# Attendu : SIZE ~310-400 MB

# Smoke local
docker run --rm -d --name ga -p 8000:8000 \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e PAPPERS_API_KEY=$PAPPERS_API_KEY \
  -e LOG_LEVEL=INFO \
  genial-agent:s08
sleep 8
curl -fsS http://localhost:8000/health | jq
docker stop ga

# Integration (opt-in, consomme ~1 crédit Pappers)
make test-integration -- tests/integration/test_S08_docker.py
```

### Commit phase 2

`feat(S08): Dockerfile final + railway.json multiRegion + deployment docs`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

#### Configuration

- [ ] `railway.json` :
  - `$schema` pinné `https://railway.com/railway.schema.json`.
  - `build.builder == "DOCKERFILE"`, `dockerfilePath` correct.
  - `deploy.healthcheckPath == "/health"`, timeout ≥ 30 s.
  - `deploy.restartPolicyType == "ON_FAILURE"` (pas `ALWAYS` —
    sinon une boucle de crash répétée brûle des crédits).
  - `multiRegionConfig` contient bien `europe-west4-drams3a`,
    `numReplicas == 1`.
  - `sleepApplication == false` explicite.
  - `tests/unit/test_S08_railway_config.py` vert.

#### Dockerfile

- [ ] User non-root (`USER agent`, uid 1000).
- [ ] Image < 500 MB (`docker images | grep genial-agent` →
      ~310-400 MB attendu).
- [ ] CMD en shell-form (`["sh", "-c", ...]`) + flag `-h` Chainlit
      + référence `${PORT}` avec fallback 8000.
- [ ] `HEALTHCHECK` directive présente avec `--start-period=15s`
      (sinon Docker marque unhealthy avant fin du boot).
- [ ] `COPY .chainlit ./` présent (S06 customizations).
- [ ] `curl` installé côté runtime stage (apt install).
- [ ] **Aucun secret en ARG ou ENV** (grep `ANTHROPIC_API_KEY=`,
      `PAPPERS_API_KEY=`, `ELEVENLABS_API_KEY=`, `STATS_TOKEN=` →
      uniquement des `<empty>` ou références shell, pas de valeur
      littérale).
- [ ] `tests/unit/test_S08_dockerfile.py` vert.

#### `.dockerignore`

- [ ] Couvre `.env`, `.env.*`, `.venv/`, `__pycache__/`, `.git/`.
- [ ] **Ne couvre PAS** `.chainlit/config.toml` (sinon Chainlit
      tourne avec ses défauts, perte des hardenings S06).
- [ ] **Ne couvre PAS** `README.md` (sinon `uv sync --frozen` dans le
      builder stage échoue : hatchling le requiert).
- [ ] Couvre `tests/`, `docs/` (image runtime ne doit pas embarquer).

#### Déploiement réel

- [ ] URL publique HTTPS répond 200 sur `/health`, body `status: "ok"`.
- [ ] `tools_count` du body `/health` ≥ 1 (preuve handshake MCP OK).
- [ ] L'UI Chainlit charge sur `/` (les 4 starters apparaissent).
- [ ] Région réelle dans Railway dashboard = **Europe West (Amsterdam)**
      — pas le défaut compte (US East).
- [ ] Variables d'env Railway : 9 vars présentes, valeurs masquées
      (cliquer "Show" pour vérifier équivalence avec `.env` local).
- [ ] `gitleaks detect` clean sur le commit phase 2 (vérifié avant
      push).

#### UptimeRobot

- [ ] Monitor créé, type **Keyword**, mode **does not exist**,
      keyword `"status":"ok"`, interval 5 min.
- [ ] Statut affiché "up" après le 1er ping.
- [ ] Email de test reçu (depuis le dashboard UptimeRobot).

#### Documentation

- [ ] `docs/deployment.md` :
  - 10 sections (cf. squelette phase 1).
  - Procédure UptimeRobot complète avec captures ou descriptions
    pas-à-pas.
  - Section troubleshooting actionnable (Application Failed to
    Respond, build timeout, healthcheck KO, région ignorée).
  - Smoke test bash documenté.
- [ ] `README.md` :
  - Lien vers `docs/deployment.md`.
  - URL publique notée.
  - Mention UptimeRobot + intervalle 5 min.
- [ ] `docs/stories/README.md` :
  - Ligne S08 → ✅.
  - Check-list "Avant S08" cochée.

### Commit phase 3

`review(S08): approved` (ou `review(S08): fix — …` si ajustements
mineurs : typos, lint, image > 400 MB à investigate, etc.).

---

## ✅ Critères d'acceptation

- [ ] `make docker-build` retourne 0, image taggée `genial-agent:local`.
- [ ] `docker run` local répond `200` + JSON 4 clés sur
      `http://localhost:8000/health`.
- [ ] URL publique Railway répond `200` HTTPS sur `/health` avec
      `body.status == "ok"`.
- [ ] L'UI Chainlit charge à la racine `/` (HTTP 200, content-type
      `text/html`).
- [ ] Région Railway dashboard = **Europe West (Amsterdam)**.
- [ ] UptimeRobot monitor actif, keyword `"status":"ok"`, intervalle
      5 min, statut "up".
- [ ] `tests/unit/test_S08_railway_config.py` et
      `tests/unit/test_S08_dockerfile.py` verts (`make test-unit`).
- [ ] `tests/integration/test_S08_docker.py` vert via
      `make test-integration` (skip propre si Docker / clés absent).
- [ ] `gitleaks detect` clean sur le commit phase 2.
- [ ] `ruff check src tests` + `ruff format --check src tests` clean.
- [ ] `docs/deployment.md` permet à un dev tiers de relancer le
      déploiement de zéro (test : Lancelot le relit en oubliant le
      contexte → toutes les étapes claires).

---

## 📦 Done when

- [x] Phase 1 commitée
      (`story(S08): refine — railway.json schema 2026, multiRegionConfig Amsterdam, Dockerfile shell-form $PORT, UptimeRobot keyword`).
- [x] Phase 2 commitée + image build + déploiement effectué
      (`feat(S08): Dockerfile final + railway.json multiRegion + deployment docs`,
      commit `d8c88d0` ; doc patch URL `docs(S08): URL Railway publique + annexe API + warning shared vs service vars`,
      commit `d221ee9`).
- [ ] Phase 3 approuvée.
- [x] URL publique notée dans `docs/deployment.md` ET `README.md`
      (https://genial-agent-production.up.railway.app).
- [ ] Ligne S08 mise à jour dans `docs/stories/README.md` → ✅
      (actuellement 🟡 en cours dev done, à passer ✅ post-review).
- [ ] Check-list "Avant S08" cochée dans `docs/stories/README.md`
      (UptimeRobot non encore configuré côté Lancelot).
- [x] Push effectué sur `claude/builder-evaluation-exercise-34Iyu`.

---

## 📝 Notes post-déploiement (2026-04-25)

> Section ajoutée par le Dev Agent S08 phase 2 après le déploiement
> programmatique Railway. **Lecture obligatoire pour S09 et S10** —
> contient des gotchas qui n'apparaissaient pas en phase 1 elicitation.

### A. Déploiement programmatique via Railway GraphQL API

Le déploiement initial a été automatisé via l'API publique
`https://backboard.railway.com/graphql/v2` (pas via la console UI).
Les IDs de ressources sont stockés dans `.env` local (gitignoré) +
documentés en commentaires dans `.env.example` :

```bash
RAILWAY_API_TOKEN=<account-scoped, dropdown "No workspace" à la création>
RAILWAY_PROJECT_ID=b7c9ba07-9381-4f6f-8ff4-1fb388c08cde
RAILWAY_SERVICE_ID=5345b27d-4377-4e1b-8eda-2d1f50e9cf46
RAILWAY_ENVIRONMENT_ID=ad05f291-c453-4cee-a029-03487a62c5bf
RAILWAY_PUBLIC_DOMAIN=genial-agent-production.up.railway.app
```

> ⚠️ Le projet Railway s'appelle **`discerning-perfection`** (nom
> auto-généré). Le **service** dans ce projet s'appelle `genial-agent`
> et c'est lui qui porte le déploiement. Ne pas confondre `project.name`
> et `service.name` dans les queries.

**Snippets utiles** dans `docs/deployment.md` § « Annexe — API Railway »
(whoami, deployments, deploymentLogs, domains). Les agents S09 / S10
peuvent réutiliser ces snippets pour auditer un déploiement sans
ouvrir la console UI Railway.

#### A.1 Choix du token — gotcha au démarrage

Trois tentatives ont été nécessaires pour avoir un token utilisable :

| Tentative | Type    | Résultat                                                                  |
| --------- | ------- | ------------------------------------------------------------------------- |
| `d1070d66-...` | inconnu | révoqué / expiré, fail sur tous les types d'auth                     |
| `123f2303-...` | Workspace | scope limité au workspace, **pas** d'accès aux projects via top-level |
| `79ca7d8c-...` | **Account** | dropdown « No workspace » à la création → marche via `Authorization: Bearer` + CLI `RAILWAY_API_TOKEN=` env var |

**Règle** : pour une intégration programmatique large (lister projects,
lire vars, déclencher redeploy), il faut un **Account token** (« No
workspace » dans le dropdown du formulaire création). Workspace token
seul ne suffit pas — `me`, `projects(workspaceId)` et même
`project(id)` retournent `Not Authorized`.

### B. Gotcha critique — Shared vars NON héritées par les services

**Symptôme** : déploiement initial `d8c88d0` avait `body.status == "ko"`
sur `/health` avec `error: "RuntimeError"` et `latency_ms: 0`.

**Diagnostic** : `RuntimeError("PAPPERS_API_KEY not set")` levé par
`mcp_pappers._build_url()` (ligne 237 de `mcp_pappers.py`). Les vars
existaient bien côté Railway mais au scope **shared (project-level)**,
pas au scope **service**. Contrairement à ce qu'on pourrait penser,
**Railway ne propage PAS automatiquement les shared vars aux services** :
chaque service doit soit redéclarer les vars, soit poser une référence
`${{ shared.VAR_NAME }}` qui acte explicitement la dépendance.

**Fix appliqué via API** : mutation `variableCollectionUpsert` au
scope service avec 8 vars pointant `${{ shared.X }}` :

```graphql
mutation Upsert($input: VariableCollectionUpsertInput!) {
  variableCollectionUpsert(input: $input)
}
# variables.input :
{
  "projectId": "...", "serviceId": "...", "environmentId": "...",
  "replace": false, "skipDeploys": false,
  "variables": {
    "ANTHROPIC_API_KEY": "${{ shared.ANTHROPIC_API_KEY }}",
    "PAPPERS_API_KEY": "${{ shared.PAPPERS_API_KEY }}",
    # ... 6 autres ...
  }
}
```

`skipDeploys: false` (défaut) déclenche un redeploy auto. Le redeploy
a été SUCCESS en ~70s, `/health` est passé à `status:"ok"` avec
`mcp.tools_count: 7`, `latency_ms: ~376ms`.

**Pour S09 et S10** :
- Si vous ajoutez une nouvelle env var (ex : feature flag, secret
  ElevenLabs additionnel pour S10), ajoutez-la **au scope service**,
  pas au shared. Ou pensez à poser la ref `${{ shared.VAR }}` après.
- Le warning `pappers_healthcheck_failed` sans détails est un signal
  faible — toujours croiser avec un `curl /health` pour avoir
  `error_type` exact.

### C. Auto-deploy GitHub vérifié end-to-end

Le `repoTriggers` du service a été automatiquement câblé par Railway
au moment de la création du projet (lien GitHub OAuth) :

```json
{
  "repository": "Bakajanai69/genial-agent",
  "branch": "claude/builder-evaluation-exercise-34Iyu",
  "checkSuites": false
}
```

Validation end-to-end (commit `d221ee9` poussé après le 1er deploy) :

```
T+0s   git push origin claude/builder-evaluation-exercise-34Iyu
T+2s   Railway: deployment created, status=BUILDING
T+72s  Railway: status=SUCCESS
T+78s  curl /health → status:"ok"
```

→ Tout push sur la branche redéploie en < 90 s. **Aucune action manuelle
nécessaire pour les commits S09 / S10**.

### D. Smoke test U3 sur l'URL publique — caps trop serrés (à arbitrer S09)

Premier test live de Lancelot sur l'URL publique :

> *"Compare la santé financière de Carrefour et Casino sur 3 ans,
> lequel présente le moins de risque ?"*

Résultat : **double cap hit** sur 1 seul tour.

```
[warn] routing_cap_tool_calls   # 5/5 atteint
       cap_token_budget         # 50_000 atteint
✗ Critic confiance 30%
   Issues: pas de données financières, comparaison incomplète,
   recommandation implicite de risque
```

#### D.1 Trace du tour (Sonnet)

5 tool calls observés en moins de 2s (parallel `tool_use` blocks) :

1. `sirenisateur(Carrefour)` ✅
2. `sirenisateur(Casino)` ✅
3. `sirenisateur(Carrefour SA holding)` ❌ **redondant** — SIREN déjà obtenu en (1)
4. `comptes-entreprise(Carrefour)` ✅
5. `comptes-entreprise(Casino)` ✅ → mais ce 5ème call déclenche le cap

#### D.2 Trois causes empilées

**1. Stratégie sous-optimale Sonnet** : 3 appels SIREN au lieu de 2.
Le 3ème (« Carrefour SA holding ») était redondant. Sans cette erreur,
le tour passait à 4/5.

**2. Cap `MAX_TOOL_CALLS_PER_TURN = 5` trop serré pour U3** :
[`src/genial_agent/guardrails/caps.py:30`](../../src/genial_agent/guardrails/caps.py).
Spec ambivalente — cahier §4 dit 10, §5.3 + §14.3 C4 disent 5
(README "Décisions de cohérence" §1 a tranché à 5). Or §13 critères
d'acceptation **demande** *"enchaîne au moins 4 tool calls visibles"*.
Marge réelle = 1 call. Toute inefficacité agent = échec.

**3. Cap `MAX_TOKENS_PER_SESSION = 50_000` cumulatif** :
[`src/genial_agent/guardrails/token_budget.py`](../../src/genial_agent/guardrails/token_budget.py).
Le tool `comptes-entreprise` retourne ~3 ans de bilans → 5-10K tokens
par appel. Cumul rapide vers 50K dès le 1er tour U3
(system prompt durci ~2K + schémas 7 tools ~3K + 4 tool results lourds
~25-35K + reasoning Sonnet).

#### D.3 Recommandations pour S09 (polish + adversarial)

| Fix | Fichier | Effort | Impact |
|-----|---------|--------|--------|
| Bumper `MAX_TOOL_CALLS_PER_TURN` à **7** (entre §4=10 et §5.3=5) | `caps.py:30` | 1 ligne | Marge 2 calls pour U3, esprit cap conservé |
| Bumper `MAX_TOKENS_PER_SESSION` à **80_000** | `caps.py:55` | 1 ligne | Permet U3 + 1 follow-up multi-turn |
| Prompt fix anti-redondance SIREN : *"un seul `sirenisateur` par entité, ne re-cherche pas un SIREN déjà obtenu"* | system prompt agent | ~5 lignes | Élimine le call gaspillé |
| Documenter §4 vs §5.3 dans le cahier (figer 7 ou 5, mais pas les deux) | `cahier-des-charges.md` §4 et §5.3 | 5 min | Cohérence spec |

> **Décision à prendre par S09** : faut-il bumper les caps pour
> qu'U3 passe robustement (signal "ça marche") OU garder les caps
> serrés pour montrer les hardenings (signal "ça se défend") ?
> Mon avis : bump à 7 calls / 80K tokens + prompt fix. Les caps
> restent visibles dans la démo si l'évaluateur force un cas
> extrême (T7 « dossier complet sur 50 entreprises du CAC40 »).

#### D.4 Le critic a fait son job

Score 30% avec issues correctement listées. C'est exactement le
comportement attendu du C6 (Haiku-critic §14.3) : un signal rouge
visible quand la réponse n'est pas exploitable, plutôt qu'une
fausse confiance. **Ne pas baisser le seuil orange/rouge en S09**
pour cacher cet échec — au contraire, on garde le critic strict et
on fix les caps en amont.

### E. État UptimeRobot

Toujours **non configuré** (Lancelot doit le faire côté UI). Procédure
inchangée dans `docs/deployment.md` §8. À cocher dans
`docs/stories/README.md` § « Avant S08 » une fois fait.

### F. Tokens partagés en chat — à révoquer

3 tokens Railway sont apparus en clair dans la conversation Dev Agent :
`d1070d66-...`, `123f2303-...`, `79ca7d8c-...`. Le dernier est dans
`.env` local (gitignoré) pour permettre aux agents S09/S10 d'auditer.
**Action recommandée Lancelot** : recréer un token frais Account-scope,
mettre à jour `.env`, révoquer les anciens.
