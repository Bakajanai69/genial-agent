# SXX — Titre court

> **Statut** : ⬜ à faire · 🟡 en cours · ✅ approuvée
> **Durée estimée** : X h
> **Parallélisable avec** : Syy

---

## 📍 Contexte

Lien explicite vers les sources de vérité que l'agent doit lire avant de
commencer.

- `docs/cahier-des-charges.md` §X.Y — description
- `docs/pappers-mcp.md` §Z (si pertinent)
- Stories précédentes : Sxx, Syy

Résumé de ce que la story livre et pourquoi elle existe.

---

## 🔒 Prérequis

- [ ] Stories terminées : Sxx, Syy
- [ ] Fichiers / dossiers existants attendus
- [ ] Variables d'env attendues dans `.env`

## 🔑 Inputs utilisateur requis AVANT de démarrer

- [ ] Clé / compte / décision à fournir (user action)

---

## 🎯 Scope

### Dans le scope

- Bullet précis.
- Autre bullet précis.

### Hors scope (explicite)

- Bullet précis.
- Autre bullet précis.

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Vérifier la version actuelle de la librairie X (WebSearch).
- [ ] Confirmer la signature actuelle de l'endpoint Y.
- [ ] Lire la doc officielle Z pour valider tel pattern.

### Points à résoudre

- [ ] Ambiguïté / trou à combler.

### Mise à jour du fichier story

Le fichier story est mis à jour pour refléter les infos collectées :
versions pinnées, snippets à jour, gotchas documentés.

### Commit final phase 1

`story(Sxx): refine — <résumé recherches>`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `path/to/file.ext` — but : …

### APIs à utiliser (endpoints réels)

- `https://…` — à appeler réellement dans les tests d'intégration.

### Gotchas documentés

- Piège 1 : …
- Piège 2 : …

### Tests à produire

#### Unitaires (`tests/unit/test_Sxx_*.py`)

- `test_<fonction>_<cas>()` — …

#### Intégration (`tests/integration/test_Sxx_*.py`)

- `test_<integration>()` — requiert `<VAR_ENV>` sinon skip.

### Commandes de vérification

```bash
make lint
make test
```

### Commit final phase 2

`feat(Sxx): <résumé implémentation>`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique à cette story

- [ ] Point spécifique 1.
- [ ] Point spécifique 2.

### Check-list générique (tous stories)

- [ ] Pas de secret commité (`gitleaks detect` clean).
- [ ] `ruff check` et `ruff format --check` clean.
- [ ] `make test` vert avec couverture raisonnable.
- [ ] Tests d'intégration contre API réelle présents ou explicitement skip.
- [ ] Error handling : timeouts + retry + mapping codes erreur.
- [ ] Pas de TODO / FIXME / print() oubliés.
- [ ] Pas de dépendance inutile ajoutée.

### Commit final phase 3

- Si OK : `review(Sxx): approved`
- Si rework : `docs/stories/reviews/Sxx-rework.md` créé + commit
  `review(Sxx): rework — <raisons>`

---

## ✅ Critères d'acceptation globaux

Cochables indépendamment, testables par une commande ou un comportement
observable.

- [ ] Critère 1 testable.
- [ ] Critère 2 testable.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée, tests verts, `gitleaks` clean.
- [ ] Phase 3 approuvée.
- [ ] Ligne de la story mise à jour dans `docs/stories/README.md`.
- [ ] Push sur `claude/builder-evaluation-exercise-34Iyu` effectué.
