# Dogfooding S09 — session live

> **Squelette posé par le Dev Agent S09 phase 2 (2026-04-25).**
> Ce fichier est à compléter en live — le Dev Agent ouvre l'URL Railway,
> joue les scénarios D0 → D12, remplit le tableau, prend les screenshots,
> puis valide la décision finale ("démo prête à enregistrer" ou
> "bloquée par <bug>"). Le Review Agent (phase 3) co-signe en pied de
> document après avoir rejoué a minima D2 / D7 / D9 / D10.

**Date** : 2026-04-DD HH:MM (Europe/Paris)
**Dev Agent** : Claude Code CLI (modèle …)
**URL testée** : <https://genial-agent-production.up.railway.app>
**Pré-check `bash scripts/smoke_S09.sh`** : ✅ exit 0 / ❌ <message>

## Tableau scénarios D0 → D12

| #     | Verdict | Latence  | Notes                                                                         |
| ----- | ------- | -------- | ----------------------------------------------------------------------------- |
| D0    | ⬜       | n/a      | Logo Genial visible header + hero (top-left + above starters), pas de "C" Chainlit. |
| D0bis | ⬜       | n/a      | Logo lisible sur thèmes dark **et** light.                                    |
| D1    | ⬜       | …        | 4 starters ⚡⚡🧠🧠 + footer RGPD + lien GitHub présent.                       |
| D2    | ⬜       | …        | Starter ⚡ Fiche LVMH : badge `⚡ Haiku`, SIREN cliquable, bannière entité, critic ✓. |
| D3    | ⬜       | …        | Suivi "Et son CA ?" : multi-turn résolu, CA + date de bilan.                  |
| D4    | ⬜       | …        | Suivi "Et ses dirigeants ?" : liste des dirigeants LVMH.                      |
| D5    | ⬜       | …        | Test Pappers BNP : dirigeants + rôles + SIREN cliquable.                      |
| D6    | ⬜       | …        | Test Pappers Carrefour : CA + date de bilan, advisory disclaimer absent.      |
| D7    | ⬜       | …        | Starter 🧠 Compare : 4+ steps tool, badge `🧠 Sonnet`, tableau comparatif.    |
| D8    | ⬜       | …        | Refus scope Apple Inc : pas d'appel Pappers, refus poli FR-only.              |
| D9    | ⬜       | …        | Jailbreak system prompt : bandeau garde-fou C1, pas d'appel LLM.              |
| D10   | ⬜       | …        | 3 onglets concurrents : isolation par session OK.                             |
| D11   | ⬜       | …        | Idempotence < 60 s : 2ᵉ envoi servi du cache.                                 |
| D12   | ⬜       | n/a      | Fallback MCP KO local : bandeau rouge, agent répond "sans accès".             |

## Observations transverses

- Streaming : ⬜
- Steps orphelines : ⬜ (aucune ?)
- Linkify SIREN : ⬜ (X/Y cliquables)
- Bannière entité : ⬜
- Footer RGPD : ⬜
- Console JS : ⬜
- /stats cohérent : ⬜
- Critic cohérent : ⬜

## Bugs / écarts trouvés

- **B1** … (ID + scénario + description + sévérité)
- **B2** …
- **B3** …

## Décision

- [ ] Démo prête à enregistrer (Loom).
- [ ] Démo bloquée par : <listing des fix obligatoires>.

---

## Re-test review agent (phase 3)

> Section ajoutée par le Review Agent — confirme les verdicts du Dev
> Agent sur a minima D2 / D7 / D9 / D10.

**Date** : 2026-04-DD HH:MM (Europe/Paris)
**Review Agent** : Claude Code CLI (modèle …)

| # | Verdict re-test | Notes |
|---|---|---|
| D2  | ⬜ | … |
| D7  | ⬜ | … |
| D9  | ⬜ | … |
| D10 | ⬜ | … |

**Co-signature** : ✅ verdicts du Dev Agent confirmés / ❌ écart à
re-traiter (cf. ``docs/stories/reviews/S09-rework.md``).
