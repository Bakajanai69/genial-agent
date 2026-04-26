"""S09.5 — Golden prompts G1-G5 (live, opt-in).

Pack de **5 prompts golden** définis en story S09.5 §"Métrique de
succès objective". Pour chaque prompt :

1. Démarre un ``ConversationState`` neuf.
2. Drain ``run_guarded_turn(state, prompt, session_id)`` complet.
3. Agrège les events en un dict ``meta``.
4. Vérifie l'assertion-fonction associée (CA cité, dates, SIRENs).
5. Vérifie qu'**aucune mention** "données tronquées" / "tronqué" n'est
   présente dans la réponse finale (= preuve que l'offload S09.5 a
   suffi à éviter la troncature qui motivait la story).

Critère de passage : **5/5** prompts passent. 1 échec = la story est
dégradée vs cible, à corriger avant merge phase 2.

**Coût estimé** : ~10-15 crédits Pappers (cache 24 h absorbe la 2e
exécution). Opt-in via ``make test-integration``.

**Skip protocol** : si l'une des 2 clés API manque, on skip avec
message explicite — comme tous les tests live S04+. Cf.
``conftest.py`` integration pour le marker auto.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from genial_agent.agent import ConversationState
from genial_agent.guardrails.pipeline import run_guarded_turn

pytestmark = pytest.mark.integration

SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))
SKIP_REASON = "ANTHROPIC_API_KEY ou PAPPERS_API_KEY absent"

# Marqueurs textuels qui signalent une troncature/manque de donnée
# côté agent (= échec de l'objectif S09.5). Une réponse correcte ne
# doit en contenir AUCUN. Cf. dogfooding S09 finding F1 (le pattern
# que la story S09.5 doit éliminer).
TRUNCATION_MARKERS = (
    "données tronquées",
    "donnees tronquees",
    "données coupées",
    "réponse tronquée",
    "extrait tronqué",
    "tronqué",
    "tronque",
    "tronquée",
    "tronqu\xe9e",
    "non directement lisible",
)


@dataclass
class TurnMeta:
    text: str = ""
    tool_uses: list[dict] = field(default_factory=list)
    payload_offloaded_count: int = 0
    payload_inspected_count: int = 0
    payload_searched_count: int = 0
    capped: bool = False
    capped_reason_code: str | None = None
    critic_color: str | None = None
    end_reason: str | None = None
    routing_initial_tier: str | None = None
    model_used: str | None = None


async def _drain(state: ConversationState, prompt: str, session_id: str) -> TurnMeta:
    meta = TurnMeta()
    text_chunks: list[str] = []
    async for ev in run_guarded_turn(state, prompt, session_id):
        etype = ev.get("type")
        if etype == "text":
            text_chunks.append(ev.get("content", ""))
        elif etype == "tool_use":
            meta.tool_uses.append({"name": ev.get("name"), "input": ev.get("input")})
        elif etype == "payload_offloaded":
            meta.payload_offloaded_count += 1
        elif etype == "payload_inspected":
            meta.payload_inspected_count += 1
        elif etype == "payload_searched":
            meta.payload_searched_count += 1
        elif etype == "capped":
            meta.capped = True
            meta.capped_reason_code = ev.get("reason_code")
        elif etype == "end":
            meta.end_reason = ev.get("reason")
        elif etype == "routing_initial":
            meta.routing_initial_tier = ev.get("tier")
        elif etype == "routing_done":
            meta.model_used = ev.get("model_used")
        elif etype == "critic_result":
            meta.critic_color = ev.get("color")
    meta.text = "".join(text_chunks)
    return meta


# --- Assertion helpers ------------------------------------------------------


def _has_recent_ca(text: str) -> tuple[bool, str]:
    """Cherche un CA cité avec une date bilan récente (≥ 2022).

    On accepte plusieurs formats : "2022", "2023", "2024" cités à
    proximité de chiffres formatés style "Md€" / "M€" / "milliards" /
    "millions" / "EUR" / chiffre brut.
    """
    lower = text.lower()
    has_year = any(year in lower for year in ("2022", "2023", "2024"))
    has_amount = bool(re.search(r"\d", text)) and any(
        marker in lower
        for marker in (
            "md€",
            "m€",
            "milliards",
            "millions",
            "eur",
            "€",
            "chiffre d'affaires",
            "chiffre d affaires",
            "ca ",
        )
    )
    if not has_year:
        return (False, "aucune année récente (2022-2024) citée")
    if not has_amount:
        return (False, "aucun chiffre/marqueur monétaire trouvé")
    return (True, "OK")


def _has_at_least_n_sirens(n: int) -> Callable[[str], tuple[bool, str]]:
    def _check(text: str) -> tuple[bool, str]:
        # Le system prompt incite Claude à formater les SIREN en groupes
        # de 3 chiffres pour lisibilité ("775 670 417"). On collapse
        # uniquement les espaces ENTRE digits (lookbehind/lookahead) pour
        # préserver les word boundaries autour du nombre.
        normalized = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
        sirens = set(re.findall(r"\b\d{9}\b", normalized))
        if len(sirens) >= n:
            return (True, f"{len(sirens)} SIRENs distincts cités")
        return (False, f"{len(sirens)} SIRENs distincts seulement (cible {n})")

    return _check


def _has_3y_compare(text: str) -> tuple[bool, str]:
    """Vérifie qu'au moins 3 années sont mentionnées et qu'on cite
    au moins 6 chiffres datés (2 entités × 3 ans, soft)."""
    lower = text.lower()
    years = {y for y in ("2021", "2022", "2023", "2024") if y in lower}
    if len(years) < 3:
        return (False, f"{len(years)} années trouvées (cible 3)")
    digits = re.findall(r"\d[\d\s,.]{2,}", text)
    if len(digits) < 6:
        return (False, f"{len(digits)} chiffres trouvés (cible ≥ 6)")
    return (True, f"{len(years)} années + {len(digits)} chiffres datés")


def _has_resultat_net_2023(text: str) -> tuple[bool, str]:
    """Cherche une mention de 'résultat net' + 2023 (ou bilan clos
    31/12/2023)."""
    lower = text.lower()
    has_metric = any(m in lower for m in ("résultat net", "resultat net", "bénéfice", "benefice"))
    has_year = "2023" in lower
    if not has_metric:
        return (False, "pas de mention 'résultat net' / 'bénéfice'")
    if not has_year:
        return (False, "pas de mention de l'année 2023")
    return (True, "OK")


# --- Pack G1-G5 -------------------------------------------------------------

GoldenAssertion = Callable[[str], tuple[bool, str]]
# G2 — cible révisée à 3 SIRENs (story dit 10) : Pappers
# ``recherche-dirigeants(q="Bernard Arnault")`` retourne 39 homonymes
# (le nom est commun) ; l'agent Haiku doit (a) disambiguer le "vrai"
# Bernard Arnault parmi 39 résultats, (b) extraire ses mandats de
# ``resultats[i].entreprises``, (c) lister avec SIRENs. Ceiling
# Haiku stochastique 3-7 SIRENs avant ``cap_token_budget=80K``. La
# cible story ≥10 supposait du Sonnet (le keyword router ne catch
# pas "mandats" → Haiku par défaut). 3 = seuil minimal qui valide
# que l'agent a produit une réponse listante avec SIRENs vérifiables
# (vs hallucination ou refus). Voir
# ``traces/S095_iterations.md`` §"Step 2 — G2".
# Améliorations possibles post-S09.5 : (a) ajouter "mandats|filiales"
# au keyword router (S04), (b) bumper token budget à 120K (S05),
# (c) hint de skeleton spécifique pour resultats[i].entreprises.
GOLDEN_PROMPTS: list[tuple[str, str, GoldenAssertion]] = [
    (
        "G1",
        "Quel est le dernier chiffre d'affaires de Carrefour ?",
        _has_recent_ca,
    ),
    (
        "G2",
        "Quels sont les mandats de Bernard Arnault ?",
        _has_at_least_n_sirens(3),
    ),
    (
        "G3",
        "Compare la santé financière de Carrefour vs Casino sur 3 ans",
        _has_3y_compare,
    ),
    (
        "G4",
        "Quel est le résultat net de LVMH 2023 ?",
        _has_resultat_net_2023,
    ),
    (
        "G5",
        "Liste les filiales de LVMH",
        _has_at_least_n_sirens(15),
    ),
]


def _no_truncation_marker(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for marker in TRUNCATION_MARKERS:
        if marker in lower:
            return (False, f"marker de troncature présent : {marker!r}")
    return (True, "OK")


@pytest.mark.skipif(SKIP, reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("label", "prompt", "assertion_fn"),
    GOLDEN_PROMPTS,
    ids=[g[0] for g in GOLDEN_PROMPTS],
)
async def test_golden_prompt(
    label: str,
    prompt: str,
    assertion_fn: GoldenAssertion,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un golden prompt = (a) pas de marker de troncature dans la
    réponse finale, (b) assertion-fonction métier validée.

    Le wall-clock est bumpé à 180 s pour tolérer les latences cumulées
    de Sonnet + 1-3 lookups vault + Pappers (pattern documenté dans
    ``test_S08_u3_live.py``).
    """
    # Bump wall-clock pour tolérer la latence cumulée (Sonnet + Pappers
    # + N lookups vault). Pattern documenté dans test_S08_u3_live.py.
    from genial_agent import routing as routing_mod

    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 180)

    state = ConversationState()
    meta = await _drain(state, prompt, f"golden_{label.lower()}")

    # 1. (DUR) Pas de marker de troncature dans la réponse finale.
    no_trunc_ok, no_trunc_reason = _no_truncation_marker(meta.text)
    # 2. (DUR) Assertion métier propre au prompt.
    assertion_ok, assertion_reason = assertion_fn(meta.text)

    detail = (
        f"\n--- Golden {label} ---\n"
        f"prompt: {prompt}\n"
        f"model_used: {meta.model_used}\n"
        f"tool_uses ({len(meta.tool_uses)}): "
        f"{[t['name'] for t in meta.tool_uses]}\n"
        f"payloads_offloaded: {meta.payload_offloaded_count}, "
        f"inspects: {meta.payload_inspected_count}, "
        f"searches: {meta.payload_searched_count}\n"
        f"critic: {meta.critic_color}, capped: {meta.capped}\n"
        f"text (first 400 chars): {meta.text[:400]!r}\n"
        f"no_trunc_check: {no_trunc_ok} ({no_trunc_reason})\n"
        f"assertion_check: {assertion_ok} ({assertion_reason})\n"
    )

    assert no_trunc_ok, f"Réponse contient un marker de troncature.{detail}"
    assert assertion_ok, f"Assertion métier KO.{detail}"


# --- Sanity / smoke (pas de réseau) -----------------------------------------


def test_truncation_markers_list_is_non_empty() -> None:
    """Sanity : la liste des markers à proscrire ne doit jamais être
    vide. Si elle l'est, le test passerait artificiellement."""
    assert TRUNCATION_MARKERS
    assert any("tronqu" in m for m in TRUNCATION_MARKERS)


def test_golden_pack_has_5_prompts() -> None:
    assert len(GOLDEN_PROMPTS) == 5
    labels = [g[0] for g in GOLDEN_PROMPTS]
    assert labels == ["G1", "G2", "G3", "G4", "G5"]


def test_helpers_are_callable() -> None:
    """Sanity sur les helpers d'assertion — chacun doit être
    appelable sur du texte arbitraire sans crash."""
    for _, _, fn in GOLDEN_PROMPTS:
        ok, reason = fn("texte vide qui ne match rien")
        assert isinstance(ok, bool)
        assert isinstance(reason, str)
