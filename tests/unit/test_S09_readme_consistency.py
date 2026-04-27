"""Garde anti-régression sur la doc S09.

Pas de réseau, pas de clé API. Vérifie 4 invariants :

1. ``README.md`` mentionne l'URL Railway publique exacte.
2. ``EVALUATION.md`` cite les 5 scénarios attendus.
3. ``docs/stories/README.md`` ligne S09 → ``✅`` ou encore "à faire".
4. ``docs/stories/README.md`` ligne S08 → ``✅`` (pas 🟡).
5. ``docs/adversarial-run.md`` (s'il existe) mentionne les 10 cases
   T1-T10.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_readme_mentions_railway_url() -> None:
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "genial-agent-production.up.railway.app" in content


def test_evaluation_md_5_scenarios() -> None:
    content = (ROOT / "EVALUATION.md").read_text(encoding="utf-8")
    for needle in ("Le bateau", "Le chaînage", "multi-turn", "scope", "jailbreak"):
        assert needle.lower() in content.lower(), f"manquant : {needle!r}"


def test_stories_readme_s09_present() -> None:
    content = (ROOT / "docs" / "stories" / "README.md").read_text(encoding="utf-8")
    assert "S09" in content
    # Soit la story est encore "à faire" (avant merge phase 3), soit
    # approved après merge — au minimum un de ces marqueurs.
    assert "✅" in content or "à faire" in content


def test_stories_readme_s08_completed() -> None:
    """S08 doit être ✅ approved — l'agent dépend de S08 pour le déploiement."""
    content = (ROOT / "docs" / "stories" / "README.md").read_text(encoding="utf-8")
    # ``startswith`` plutôt que ``"| S08 |" in line`` : la cellule
    # « parallèle avec » d'autres lignes (ex : S07) peut contenir
    # ``S08`` et matcher trop large.
    s08_line = next(
        (line for line in content.splitlines() if line.startswith("| S08 |")),
        None,
    )
    assert s08_line is not None, "ligne S08 absente du tableau de stories"
    assert "✅" in s08_line, f"S08 non marqué ✅ dans : {s08_line!r}"


@pytest.mark.skipif(
    not (ROOT / "docs" / "adversarial-run.md").exists(),
    reason="adversarial-run.md généré par test_S09_adversarial.py (live)",
)
def test_adversarial_run_md_lists_t1_t10() -> None:
    content = (ROOT / "docs" / "adversarial-run.md").read_text(encoding="utf-8")
    for tn in (f"T{n}" for n in range(1, 11)):
        assert tn in content, f"case {tn} absent du rapport"
