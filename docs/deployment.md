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
| `ELEVENLABS_API_KEY`       | clé ElevenLabs                          | Inutilisée tant que `ENABLE_VOICE_BRIEF=false`     |
| `ELEVENLABS_VOICE_GAELLE`  | `tKaoyJLW05zqV0tIH9FD`                  | Config publique, pas un secret                     |
| `ELEVENLABS_VOICE_GUILLAUME` | `ohItIVrXTBI80RrUECOD`                | Config publique, pas un secret                     |
| `ELEVENLABS_MODEL_ID`      | `eleven_multilingual_v2`                | Config publique                                    |
| `ENABLE_VOICE_BRIEF`       | `false`                                 | À flipper `true` après merge S10 + gating §19.1 OK |
| `LOG_LEVEL`                | `INFO`                                  | Pour les logs JSON `structlog`                     |

**Optionnel — recommandé en prod** :

| Variable       | Valeur                        | Notes                                                     |
| -------------- | ----------------------------- | --------------------------------------------------------- |
| `STATS_TOKEN`  | token URL-safe ≥ 32 chars     | Auth-gate `/stats` (timing-safe `hmac.compare_digest`)    |

Pour générer le token :

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> ⚠️ **Ne pas** définir `WALL_CLOCK_S_OVERRIDE` côté Railway. La prod
> EU-West tient le cap cahier 15 s sans override (pertinent uniquement
> en dev WSL haute latence — cf. review S05).

> ⚠️ Les valeurs des variables sont masquées par défaut dans le
> dashboard. Cliquer « Show » pour vérifier l'équivalence avec
> `.env` local. **Ne jamais** coller les clés dans une issue / PR /
> log Railway public.

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

Railway provisionne une URL `*.up.railway.app`, par exemple :

```
https://genial-agent-production.up.railway.app
```

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

# 1. Health endpoint — doit afficher status:"ok" + 31 tools Pappers.
curl -fsS "https://${DOMAIN}/health" | jq

# Sortie attendue :
# {
#   "status": "ok",
#   "mcp": {
#     "status": "ok",
#     "latency_ms": 250,
#     "tools_count": 31,
#     "error": null
#   },
#   "version": "0.1.0",
#   "uptime_s": 12
# }

# 2. UI Chainlit accessible sur la racine.
curl -fsI "https://${DOMAIN}/" | grep -E "HTTP|content-type"
# → HTTP/2 200
# → content-type: text/html; charset=utf-8

# 3. Stats compteurs (si STATS_TOKEN configuré : ajouter
#    -H "Authorization: Bearer <token>")
curl -fsS "https://${DOMAIN}/stats" | jq
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
