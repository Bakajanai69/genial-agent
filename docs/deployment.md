# Déploiement Railway — genial-agent

Procédure de déploiement de l'agent sur Railway EU-West (Amsterdam) +
keep-alive UptimeRobot pour éviter le cold start pendant le week-end
d'évaluation.

> **Public visé** : Lancelot, ou un dev tiers qui doit rejouer le
> déploiement de zéro. Toutes les étapes sont actionables, aucune
> connaissance préalable du projet n'est requise.

Sources de vérité :

- [`docs/cahier-des-charges.md`](./cahier-des-charges.md) §6.3, §17.1.
- [`docs/stories/S08-deployment.md`](./stories/S08-deployment.md) —
  décisions techniques (railway.json schema, multiRegion, port binding,
  UptimeRobot keyword monitor).

---

## 1. Pré-requis

- [ ] Compte **Railway** ([railway.com](https://railway.com)) — login
  recommandé via GitHub SSO (évite la gestion d'un PAT).
- [ ] Compte **UptimeRobot** ([uptimerobot.com](https://uptimerobot.com))
  — plan Free OK (50 monitors, intervalle min 5 min).
- [ ] Repo GitHub `Bakajanai69/genial-agent` accessible (privé ou
  public, peu importe — Railway demande l'autorisation OAuth GitHub
  une fois).
- [ ] `.env` local valide (3 clés API testées E2E — cf. `README.md`
  + `.env.example`) : on va recopier ces valeurs dans Railway.

---

## 2. Création du projet Railway

1. Aller sur [railway.com/new](https://railway.com/new).
2. Cliquer **« Deploy from GitHub repo »**.
3. Autoriser GitHub OAuth si première fois — restreindre l'accès au
   seul repo `Bakajanai69/genial-agent`.
4. Sélectionner le repo + la branche `claude/builder-evaluation-exercise-34Iyu`.
5. Railway détecte `Dockerfile` à la racine + `railway.json` →
   le builder `DOCKERFILE` est sélectionné automatiquement.
6. **Ne pas** lancer le déploiement tout de suite — on configure les
   variables d'env d'abord (sinon le boot Chainlit échouera et on
   brûlera un essai).

---

## 3. Configuration des variables d'environnement

Dans le service Railway → onglet **Variables** → cliquer
**« New Variable »** (ou « Raw Editor » pour coller en bloc).

Liste exhaustive — **9 variables**, identiques à `.env` local :

| Variable                   | Valeur                                  | Notes                                              |
| -------------------------- | --------------------------------------- | -------------------------------------------------- |
| `ANTHROPIC_API_KEY`        | `sk-ant-…` (clé prod)                   | Haiku 4.5 + Sonnet 4.6 sur la même clé             |
| `PAPPERS_API_KEY`          | clé Pappers (handshake MCP)             | URL MCP construite côté serveur                    |
| `ELEVENLABS_API_KEY`       | clé ElevenLabs                          | Inutilisée tant que `ENABLE_VOICE_MODE=false`      |
| `ELEVENLABS_VOICE_GAELLE`  | `tKaoyJLW05zqV0tIH9FD`                  | Config publique, pas un secret                     |
| `ELEVENLABS_VOICE_GUILLAUME` | `ohItIVrXTBI80RrUECOD`                | Config publique, pas un secret                     |
| `ELEVENLABS_MODEL_ID`      | `eleven_multilingual_v2`                | Config publique                                    |
| `ENABLE_VOICE_MODE`        | `false`                                 | À flipper `true` après merge S10 + gating §19.1 OK |
| `LOG_LEVEL`                | `INFO`                                  | Pour les logs JSON `structlog`                     |

**Optionnel — recommandé en prod** :

| Variable       | Valeur                        | Notes                                                     |
| -------------- | ----------------------------- | --------------------------------------------------------- |
| `STATS_TOKEN`  | token URL-safe ≥ 32 chars     | Auth-gate `/stats` (timing-safe `hmac.compare_digest`)    |

Pour générer le token :

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**S09.7 — variables additionnelles** (cache persistant + sidebar
conversations cross-session) :

| Variable                       | Valeur                                | Notes                                                   |
| ------------------------------ | ------------------------------------- | ------------------------------------------------------- |
| `MCP_CACHE_PERSIST_PATH`       | `/data/mcp_cache.json`                | Chemin cache disque MCP. Si absent → cache in-memory uniquement (perdu au redémarrage) |
| `CHAINLIT_DATA_LAYER_DB_PATH`  | `/data/cl_threads.db`                 | SQLite des conversations — alimente la sidebar threads. Si absent → sidebar désactivée |
| `CHAINLIT_AUTH_SECRET`         | JWT ≥ 64 chars URL-safe               | Requis pour `header_auth_callback` (sinon Chainlit n'invoque jamais `list_threads` et la sidebar reste vide silencieusement) |

Pour générer le `CHAINLIT_AUTH_SECRET` :

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Les 3 variables ci-dessus sont **liées au volume `/data`** configuré
en §3 bis ci-dessous. En dev local sans volume, on peut les omettre
(ou pointer sur `data/` du repo) — la sidebar reste désactivée mais
l'agent fonctionne.

> ⚠️ **Ne pas** définir `WALL_CLOCK_S_OVERRIDE` côté Railway. La prod
> EU-West tient le cap cahier 15 s sans override (pertinent uniquement
> en dev WSL haute latence — cf. review S05).

> ⚠️ Les valeurs des variables sont masquées par défaut dans le
> dashboard. Cliquer « Show » pour vérifier l'équivalence avec
> `.env` local. **Ne jamais** coller les clés dans une issue / PR /
> log Railway public.

---

## 3 bis. Volume Railway pour cache persistant + sidebar threads (S09.7)

> Cette étape n'est **pas optionnelle** si on veut la persistance
> cross-deploy du cache MCP (~17 entrées pré-warmées au boot, économie
> de crédits Pappers significative) et la sidebar des conversations
> cross-session façon ChatGPT/Claude (livrée S09.7).

### Création du volume

Via la console Railway :

1. Service → **Settings → Volumes** → **« Create Volume »**.
2. Nom : `genial-agent-volume` (ou autre, peu importe).
3. Mount path : **`/data`** (figé, c'est le préfixe attendu par les 2
   variables `MCP_CACHE_PERSIST_PATH` et `CHAINLIT_DATA_LAYER_DB_PATH`).
4. Taille : **500 Mo** suffisent largement (cache MCP ~1 Mo, SQLite
   threads ~10-20 Mo après 100 conversations).

Via l'API GraphQL (alternative scriptable, cf. §"Annexe — API Railway"
plus bas pour les IDs et le snippet `volumeCreate`).

### Permissions runtime (Railway monte les volumes en `root:root`)

Railway monte les volumes appartenant à `root:root` par défaut. Notre
runtime tourne en `agent` (uid 1000) — il ne peut pas écrire sur
`/data` sans intervention. Le hotfix S09.7 (`docker/entrypoint.sh`)
chown `/data` au boot **avant** le `setpriv` qui switch en agent :

```sh
# docker/entrypoint.sh (extrait)
if [ -d "/data" ]; then
    chown -R agent:agent /data 2>/dev/null || true
fi
exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
```

Si on retire l'entrypoint ou si on change l'uid runtime, il faut
mettre à jour ce script en cohérence (sinon `mcp_cache_persist_failed`
en boucle dans les logs).

### Bootstrap du cache MCP (bake → volume au 1er boot)

Au build, le Dockerfile copie un cache pré-warmé (`data/`) dans
`/app/data/`. Au runtime, `bootstrap_volume_from_bake()`
(`src/genial_agent/data_bootstrap.py`) copie ce contenu vers `/data`
**uniquement si le volume est vide** (idempotent — ne réécrase pas
les conversations utilisateur accumulées). Logs attendus au boot :

```
data_bootstrap_copy        action=copy_initial size=937889
mcp_cache_loaded           loaded=17 skipped_or_expired=0
```

`bootstrap_volume_from_bake()` est appelé **avant les imports
applicatifs** dans `app.py` (ligne 37, avant `from genial_agent
import mcp_pappers`) — l'ordre est critique, ne pas le casser sous
peine de cache lazy-load qui pointe sur un volume encore vide.

### Pré-warm du cache après refill crédits Pappers

Pour ré-alimenter le cache disque (entités golden LVMH/BNP/Carrefour/
Casino + recherche-dirigeants Arnault) après un refill mensuel
Pappers :

```bash
# En local, sur la machine dev
uv run python scripts/prewarm_persistent_cache.py
```

Le script lit `traces/*.jsonl` pour extraire les `(tool, args)`
connus puis ré-exécute uniquement les appels PAYG-compatibles. Le
fichier `data/mcp_cache.json` résultant est committé dans le repo
(< 1 Mo) → automatiquement copié au prochain build vers le bake →
disponible au runtime via `bootstrap_volume_from_bake`.

---

## 3 ter. Voice mode S10 — Eleven Agents (custom LLM SSE)

> **Optionnel** — n'activer ce chantier qu'après gating §19.1 (cahier
> des charges) respecté, MVP texte vert sur l'URL Railway prod.

### Création de l'Eleven Agent

1. ElevenLabs dashboard → [Agents](https://elevenlabs.io/agents) →
   « Create Agent ».
2. **Voice** : Gaëlle (`tKaoyJLW05zqV0tIH9FD`). Sélecteur Guillaume
   gérable plus tard côté dashboard si besoin.
3. **Language** : `fr`. Override aussi côté widget (`override-language="fr"`).
4. **LLM** : choisir « **Custom LLM** ».
   - URL : `https://genial-agent-production.up.railway.app/v1/chat/completions`
   - Model name : libre (suggéré `genial-agent-claude`).
   - Headers : ajouter `Authorization` avec value `Bearer ${ELEVEN_AGENT_SHARED_TOKEN}`
     (le `${...}` pointe sur le Workspace Secret du même nom — voir étape suivante).
5. **Conversation flow** :
   - Turn eagerness : **Patient** (laisse le temps de formuler une
     question complexe U3).
   - Soft timeout : `timeout_seconds=3.0`,
     `message="Un instant, je consulte les données…"`,
     `use_llm_generated_message=false`.
6. **Security tab** :
   - Authentication : `disabled` (agent public — la sécurité passe
     par le Bearer côté custom LLM, pas par le widget).
   - Allowlist domains :
     - `genial-agent-production.up.railway.app`
     - `localhost:8000`
     - `localhost:8765`
7. Sauvegarder. Récupérer l'`agent_xxxxxxxxxxxxxxxxxxxxx` depuis l'URL
   du dashboard.

### Création du Workspace Secret

1. ElevenLabs dashboard → **Workspace → Secrets** → « Add Secret ».
2. Name : `ELEVEN_AGENT_SHARED_TOKEN`.
3. Value : générer 32+ chars random :

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Garder cette valeur — elle ira aussi dans Railway et `.env` local.

### Variables Railway additionnelles (S10)

Dans Railway → Variables :

| Variable                       | Valeur                                       | Notes                                                 |
| ------------------------------ | -------------------------------------------- | ----------------------------------------------------- |
| `ENABLE_VOICE_MODE`            | `true` (après gating §19.1 OK ; sinon `false`) | Si `false`, l'endpoint `/v1/chat/completions` n'est même pas monté. |
| `ELEVEN_AGENT_ID`              | `agent_xxxxxxxxxxxxxxxxxxxxx`                | ID public visible côté widget JS — non-secret.       |
| `ELEVEN_AGENT_SHARED_TOKEN`    | (la valeur générée à l'étape précédente)     | Bearer comparé timing-safe côté `voice/security.py`. **Strictement secret**. |

> ⚠️ La valeur de `ELEVEN_AGENT_SHARED_TOKEN` doit être **strictement
> identique** dans : Workspace Secret ElevenLabs, Railway Variables,
> et `.env` local. Sinon le custom LLM endpoint renvoie 401 et le
> widget vocal restera silencieux côté navigateur.

### Smoke test post-merge

Après deploy avec `ENABLE_VOICE_MODE=true` :

```bash
# 1) Vérifier que /voice-meta.html est servi (et contient l'agent-id).
curl -s https://genial-agent-production.up.railway.app/voice-meta.html | grep "data-agent-id"

# 2) Vérifier que /v1/chat/completions exige l'auth (sans token → 401).
curl -i -X POST https://genial-agent-production.up.railway.app/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"ping"}],"stream":true}' | head -5
# Attendu : HTTP/1.1 401 Unauthorized

# 3) Avec le bon token + Eleven envoie ses chunks. Côté navigateur,
#    cliquer sur le bouton micro flottant et tester "Donne-moi la
#    fiche LVMH" en parlant.
```

Couper le voice mode à chaud si problème en prod : passer
`ENABLE_VOICE_MODE=false` dans Railway → redeploy automatique → le
chat texte reste 100 % fonctionnel (le widget JS skippe l'injection
silencieusement quand `/voice-meta.html` renvoie 404).

---

## 4. Vérification de la région

Le `railway.json` à la racine fixe la région via `multiRegionConfig` :

```json
"multiRegionConfig": {
  "europe-west4-drams3a": { "numReplicas": 1 }
}
```

Après le 1er deploy, vérifier dans **Settings → Region** : doit
afficher **« Europe West (Amsterdam) »**.

Si Railway montre une autre région (par défaut compte → `us-west2`) :

- Vérifier que `railway.json` est bien à la racine du repo et committé.
- Re-trigger un déploiement (clic « Redeploy »).
- Si le bug persiste : éditer manuellement `Settings → Region` →
  sélectionner **Europe West (Amsterdam)** → confirmer redeploy.

---

## 5. Premier déploiement

Railway déclenche un build automatiquement au push sur la branche
configurée (`claude/builder-evaluation-exercise-34Iyu`). Si on veut
forcer un build sans push :

- Onglet **Deployments** → bouton **« Deploy »** en haut à droite.

Suivre les logs en direct :

- **Build Logs** : sortie du `docker build` (uv sync, copy sources,
  install curl).
- **Deploy Logs** : sortie de l'app au démarrage (Chainlit boot,
  structlog JSON, ping MCP au healthcheck).

Le déploiement est marqué `SUCCESS` une fois que le `/health` répond
2xx (timeout configuré 60 s dans `railway.json`).

> Build typique : ~90-120 s (cache uv chaud), ~3-4 min à froid.

---

## 6. Récupération de l'URL publique

Dans le service Railway → **Settings → Networking → Generate Domain**.

Railway provisionne une URL `*.up.railway.app`. Pour le projet courant :

```
https://genial-agent-production.up.railway.app
```

(provisionnée 2026-04-25 via la mutation GraphQL `serviceDomainCreate`
en pointant le `targetPort: 8080` — port injecté par Railway au runtime,
expansé par notre shell-form CMD `${PORT:-8000}`. Cf. annexe « API
Railway » plus bas pour le snippet curl exact.)

**Noter cette URL** dans :

1. La section « Déploiement » de [`README.md`](../README.md).
2. L'`EVALUATION.md` (S09) — première section, lien cliquable.

> Custom domain (`genial-agent.lancelotoudin.fr`) : hors scope MVP,
> documenté en next step S09. `*.up.railway.app` suffit pour Fabien.

---

## 7. Smoke test post-déploiement

Bash idempotent à rejouer après chaque deploy :

```bash
# Remplacer par l'URL retournée à l'étape 6.
DOMAIN="genial-agent-production.up.railway.app"

# 1. Health endpoint — doit afficher status:"ok" + le sous-ensemble
#    de tools Pappers retenus par notre agent (cf. cahier §5.4 : on
#    filtre les tools utiles aux cas U1–U5 sur les 31 que Pappers
#    expose. Compte courant : 7 — peut bouger avec S04/S10).
curl -fsS "https://${DOMAIN}/health" | jq

# Sortie attendue :
# {
#   "status": "ok",
#   "mcp": {
#     "status": "ok",
#     "latency_ms": 250,
#     "tools_count": 7,
#     "error": null
#   },
#   "version": "0.1.0",
#   "uptime_s": 12
# }

# 2. UI Chainlit accessible sur la racine.
curl -fsI "https://${DOMAIN}/" | grep -E "HTTP|content-type"
# → HTTP/2 200
# → content-type: text/html; charset=utf-8

# 3. Stats compteurs — en prod Railway, STATS_TOKEN est OBLIGATOIRE
#    (review S08 §B2). Sans token, /stats répond 503
#    {"error":"stats_token_required_in_production"}. Avec token :
curl -fsS "https://${DOMAIN}/stats" -H "Authorization: Bearer $STATS_TOKEN" | jq
# → JSON avec uptime_s, total_turns (0 au boot), pappers_calls_today, etc.
```

Si `/health` répond `200` mais `body.status == "ko"` : MCP Pappers
indispo. C'est attendu (UptimeRobot alertera, cf. §8) — pas un bug
de déploiement.

---

## 8. Configuration UptimeRobot

[UptimeRobot](https://uptimerobot.com) — keyword monitor en mode
« does not exist » sur `/health`. Alerte dès que `"status":"ok"`
disparaît du body (panne MCP Pappers, downtime Railway, app crashée).

### Pas-à-pas

1. Login UptimeRobot → **« New Monitor »**.
2. Configurer le monitor :

   | Champ                | Valeur                                          |
   | -------------------- | ----------------------------------------------- |
   | Monitor Type         | **Keyword**                                     |
   | URL (or IP)          | `https://<DOMAIN>/health`                       |
   | Keyword Type         | **Keyword does not exist**                      |
   | Keyword              | `"status":"ok"` *(avec guillemets et `:`)*      |
   | HTTP Method          | `GET` *(défaut)*                                |
   | Monitoring Interval  | **5 minutes**                                   |
   | Alert Contacts       | email Lancelot                                  |
   | Friendly Name        | `genial-agent (prod)`                           |

3. **« Create Monitor »**.
4. Vérifier dans le dashboard : statut **« Up »** au bout de 1-2
   intervals (10 min max).
5. Test alert : depuis **My Settings → Alert Contacts → Test** →
   confirmer la réception de l'email de test.

### Pourquoi keyword `"status":"ok"` plutôt que juste `ok`

Le body `/health` contient potentiellement d'autres `"ok"` à l'avenir
(ex : un nom de tool). Le pattern `"status":"ok"` est ancré au champ
métier, pas à un mot isolé.

### Pourquoi mode « does not exist »

Notre `/health` retourne **toujours HTTP 200**, même si MCP est KO
(décision S07 : un flap MCP transient ne doit pas redéployer Railway).
Du coup un monitor HTTP basique 2xx ne capterait pas une panne MCP.
Le keyword scan du body si :

- `"status":"ko"` → keyword absent → alerte.
- HTTP 500/timeout Railway → keyword absent → alerte.
- HTTP 200 + body OK → keyword présent → no-op.

---

## 9. Troubleshooting

### `Application failed to respond`

- **Cause #1** : l'app binde sur `127.0.0.1` au lieu de `0.0.0.0`.
  → Vérifier que le `Dockerfile` CMD contient `--host 0.0.0.0`.
- **Cause #2** : variable `PORT` non lue côté app. Railway injecte
  `PORT` au runtime (typiquement `8080`). Notre `Dockerfile` utilise
  `${PORT:-8000}` en shell-form CMD pour expansion.
  → Vérifier `docker logs` du container Railway pour voir sur quel
  port Chainlit a effectivement bindé.
- **Cause #3** : crash au boot. Lire les **Deploy Logs** Railway —
  un `KeyError: 'ANTHROPIC_API_KEY'` indique une variable d'env
  manquante.

### Build qui timeout

- **Cause #1** : `uv.lock` non committé. Le builder fait
  `uv sync --frozen` qui exige le lock.
  → Vérifier `git ls-files | grep uv.lock`.
- **Cause #2** : layer cache invalidé (changement `pyproject.toml`).
  Le 1er build à froid prend ~3-4 min, tolérer.

### Healthcheck Railway échoue

- **Cause #1** : MCP Pappers KO au boot. Le `/health` répond 200 mais
  `body.status == "ko"` → Railway considère 200 comme OK
  (cf. décision S07). Donc pas un blocker côté Railway.
- **Cause #2** : Chainlit met > 60 s à démarrer. Très improbable —
  `healthcheckTimeout: 60` est confortable (boot mesuré ~5-8 s).
  → Vérifier `Deploy Logs` pour un blocage atypique.
- **Cause #3** : crash Chainlit. → Lire les logs.

### Région Amsterdam ignorée

- **Symptôme** : `Settings → Region` montre US East / autre.
- **Cause** : `railway.json` mal formé ou absent au moment du 1er
  build → Railway tombe sur la région par défaut du compte.
  → Re-vérifier que `railway.json` est à la racine, valide JSON
  (`jq . railway.json`), avec `multiRegionConfig` correct.
  → Forcer la région dans le dashboard puis re-deploy.

### Cold start malgré UptimeRobot

- **Symptôme** : 1ère requête de la journée prend > 5 s.
- **Cause #1** : plan Railway Trial (sleepApplication par défaut).
  Notre `railway.json` force `sleepApplication: false`, mais sur
  Trial Railway peut quand même endormir l'app — passer en plan
  Hobby (5 $/mois) si critique.
- **Cause #2** : UptimeRobot pause lors d'une coupure DNS. Vérifier
  la timeline du monitor — un trou de > 10 min indique un raté
  côté UptimeRobot, pas une panne app.

### `/stats` retourne 401

- **Cause** : `STATS_TOKEN` est défini côté Railway, le client doit
  envoyer `Authorization: Bearer <token>`. Comparaison timing-safe
  via `hmac.compare_digest` (S07 review B3).
  → Récupérer le token dans Railway Variables, ajouter le header :
  `curl -H "Authorization: Bearer <token>" https://<domain>/stats`.

---

## 10. Rollback

Railway garde l'historique des déploiements (jusqu'à 100 environ).

1. Onglet **Deployments**.
2. Cliquer le déploiement N-1 (avant le crash) → menu **« ⋮ »** →
   **« Redeploy »**.
3. Railway redéploie l'image précédente en < 30 s — plus rapide
   qu'un nouveau build.

> Alternative CLI :
>
> ```bash
> npm i -g @railway/cli
> railway login
> railway link  # sélectionne le projet genial-agent
> railway redeploy <deployment-id>
> ```

---

## Annexe — fallback CLI Railway

Si la console UI Railway plante, on peut déployer via CLI :

```bash
# Install
npm i -g @railway/cli@latest

# Auth (ouvre un browser pour OAuth)
railway login

# Lier le repo local au projet Railway
cd ~/projects/genial-agent
railway link

# Déclencher un build + deploy en uploadant le contexte courant
railway up
```

> Pas le mode normal — l'auto-deploy GitHub trace les builds dans
> l'historique commit-by-commit, ce qui est plus traceable. À utiliser
> en plan B uniquement.

---

## Annexe — API Railway (introspection programmatique)

Pour les Dev / Review Agents qui doivent auditer le déploiement sans
ouvrir le dashboard Railway, on peut introspecter via la **GraphQL API
publique** : `https://backboard.railway.com/graphql/v2`.

Authentification : `Authorization: Bearer <RAILWAY_API_TOKEN>` avec un
token **scope Account** (créer via [railway.com/account/tokens](https://railway.com/account/tokens),
dropdown Workspace = **« No workspace »** — un workspace token ne peut
pas accéder aux ressources hors de son workspace).

Stocker le token + IDs dans `.env` local (gitignoré). Les UUIDs Project /
Service / Environment ne sont pas des secrets en eux-mêmes (besoin du
token pour les exploiter), mais on évite de les committer publiquement
en defense-in-depth (review S08 §I2 — combinés à un token leak, ils
donnent l'attaquant une cible directe). Récupérer les IDs réels via la
console Railway (Settings → Service → Service ID) ou via l'introspection
GraphQL ci-dessous (`me { workspaces { teams { projects { ... } } } }`).

```bash
RAILWAY_API_TOKEN=<token-Account-scope>
RAILWAY_PROJECT_ID=<uuid-project>
RAILWAY_SERVICE_ID=<uuid-service>
RAILWAY_ENVIRONMENT_ID=<uuid-environment>
RAILWAY_PUBLIC_DOMAIN=genial-agent-production.up.railway.app
```

Snippets utiles :

```bash
# Whoami
curl -sS -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ me { name email } }"}'

# Statut du dernier déploiement
curl -sS -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"{ deployments(input: { projectId: \\\"$RAILWAY_PROJECT_ID\\\", serviceId: \\\"$RAILWAY_SERVICE_ID\\\", environmentId: \\\"$RAILWAY_ENVIRONMENT_ID\\\" }, first: 1) { edges { node { id status meta } } } }\"}"

# Logs runtime
curl -sS -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"query { deploymentLogs(deploymentId: \\\"<DEP_ID>\\\", limit: 200) { message timestamp severity } }\"}"

# Domaines
curl -sS -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"{ domains(serviceId: \\\"$RAILWAY_SERVICE_ID\\\", environmentId: \\\"$RAILWAY_ENVIRONMENT_ID\\\", projectId: \\\"$RAILWAY_PROJECT_ID\\\") { serviceDomains { domain targetPort } customDomains { domain } } }\"}"
```

> ⚠️ **Gotcha vars Railway** : les variables au scope **shared (project)**
> ne sont **pas** automatiquement héritées par les services. Il faut
> soit les recréer au scope service, soit poser des références
> `${{ shared.VAR_NAME }}` dans la définition service. Notre
> déploiement initial a planté avec `RuntimeError: PAPPERS_API_KEY not
> set` parce que les vars étaient au project-level uniquement.
> Correctif : `variableCollectionUpsert` mutation avec serviceId +
> values pointant `${{ shared.X }}`. Cf. déploiement S08 phase 2.

---

## Annexe — sticky sessions (next step si scaling)

Chainlit stream via WebSockets. En multi-replica + load balancer, un
client doit rester épinglé au replica qui détient sa session WebSocket
(sinon les frames atterrissent ailleurs et le stream casse).

MVP S08 : **1 replica EU-West**, pas de config sticky session.
Si on scale plus tard :

- Activer le header `X-Chainlit-Session-id` côté load balancer
  (Chainlit ≥ 2.10 le pose automatiquement).
- Configurer Railway sticky sessions ou passer derrière Cloudflare
  avec une règle de session persistence.

Pas dans le scope week-end.

---

## Annexe — paths filter auto-deploy (next step économie crédits)

L'auto-deploy GitHub branche `claude/builder-evaluation-exercise-34Iyu`
redéploie sur **chaque push**, y compris commits doc-only — chaque
redeploy ping `/health` qui consomme 1 crédit Pappers (~70-90 s build
+ healthcheck).

Sur 48 h de démo avec ~10 commits doc/polish (typique S09/S10), ça mange
~10 crédits sur les 100/jour budgétés. Pas critique mais évitable.

**Mitigation 2026** — Railway expose un filtre `paths` sur les
`repoTriggers` (cf. [docs.railway.com/deploy/deployments#path-filtering](https://docs.railway.com/deploy/deployments#path-filtering)).
À ajouter à la création du service via la mutation GraphQL
``serviceUpdate`` :

```graphql
mutation {
  serviceUpdate(id: "<service-id>", input: {
    repoTriggers: [{
      repository: "Bakajanai69/genial-agent",
      branch: "claude/builder-evaluation-exercise-34Iyu",
      paths: ["src/**", "Dockerfile", "railway.json", "pyproject.toml", "uv.lock", ".chainlit/config.toml", "public/**", "chainlit.md"]
    }]
  }) { id }
}
```

Effets : commits sur `docs/`, `tests/`, `*.md` racine ne déclenchent plus
de redeploy. Si on veut quand même redéployer pour tester un nouveau
commit doc, `railway up` reste disponible.

Décision week-end : pas urgent (l'overhead crédit reste sous contrôle
avec le cap journalier 100). À implémenter en S09 si on a 5 min.

---

## Annexe — rotation token Railway (review S08 §I1)

Trois tokens Account-scope ont été manipulés dans la conversation
Dev Agent S08 (visibles dans le diff `docs/stories/S08-deployment.md`
§F). Le token actif `79ca7d8c-…` reste dans `.env` local pour permettre
aux Dev / Review Agents S09 / S10 d'auditer le déploiement.

**Action utilisateur recommandée AVANT la démo** :

1. [railway.com/account/tokens](https://railway.com/account/tokens) →
   créer un nouveau token, dropdown Workspace = **« No workspace »**.
2. Mettre à jour `RAILWAY_API_TOKEN` dans `.env` local.
3. Révoquer les 3 anciens tokens dans la même page.
4. Vérifier qu'aucun script externe ne dépend de l'ancien token.

Pourquoi : un token Account a tous les droits (lire les vars secrets,
redéployer, supprimer le projet). Si le `.env` fuit (backup, sync,
screenshot), prise de contrôle complète.
