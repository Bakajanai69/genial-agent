"""Validation déterministe de la réponse finale (cahier §14.3 C5, §R11, §R14).

Vérifie trois familles de problèmes :

1. **SIREN consistency** : tout SIREN Luhn-valide cité dans la réponse
   doit être présent dans les tool_results (``allowed_sirens``). Les
   orphelins remontent un flag ``hallucination_orphan_sirens`` et
   déclenchent un disclaimer visible via ``degrade``.
2. **Horodatage bilan** : toute séquence monétaire ou mention ``CA /
   chiffre d'affaires / résultat net / effectif`` doit être suivie,
   dans les 200 caractères qui suivent, d'une référence ``bilan ...
   YYYY``, ``clos ... YYYY`` ou ``exercice ... YYYY``. Sinon flag
   ``hallucination_missing_bilan_date`` + disclaimer.
3. **Advisory language** : pas de formulation prescriptive financière
   (cahier §R14). Reframing silencieux par ``degrade`` (sub regex →
   ``[reformulation neutre]``).

Politique R11 figée en phase 1 : disclaimer + event
``hallucination_detected`` — **pas** de retry LLM auto en MVP.
L'utilisateur reformule si besoin, le retry auto est documenté comme
next step (S09).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from genial_agent.guardrails.sirens import extract_sirens

# ---------------------------------------------------------------------------
# Disclaimer texts (single source of truth — testés par référence)
# ---------------------------------------------------------------------------

DISCLAIMER_ADVISORY = (
    "⚠ Cet agent fournit des informations factuelles sourcées (Pappers) "
    "et **pas** de conseil financier ou de recommandation d'investissement. "
    "Les formulations prescriptives détectées dans la réponse sont à considérer "
    "comme telles."
)

# ---------------------------------------------------------------------------
# Reason codes (cohérent S04 pattern)
# ---------------------------------------------------------------------------

REASON_CODE_HALLUCINATION_ORPHAN_SIRENS = "hallucination_orphan_sirens"
REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE = "hallucination_missing_bilan_date"
REASON_CODE_ADVISORY_LANGUAGE = "advisory_language"


# ---------------------------------------------------------------------------
# Advisory patterns — gardent les accents (texte brut, pas normalisé)
# ---------------------------------------------------------------------------

# Les disclaimers parlent explicitement des formes accentuées ET non
# accentuées (« à acheter » vs « a acheter »). On matche les deux sans
# passer par ``normalize_fr`` pour que ``degrade`` puisse sub le texte
# original (la sub sur un texte normalisé casserait les accents utiles
# dans la réponse agent).
ADVISORY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(je te|je vous|nous)\s+(conseille|recommande|suggere|suggère)", re.IGNORECASE),
    re.compile(r"\btu devrais\s+(investir|acheter|vendre|eviter|éviter)", re.IGNORECASE),
    re.compile(r"\bvous devriez\s+(investir|acheter|vendre|eviter|éviter)", re.IGNORECASE),
    re.compile(r"\b(bon|mauvais)\s+(placement|investissement)\b", re.IGNORECASE),
    re.compile(r"\b(à|a)\s+(acheter|vendre|éviter|eviter)\b", re.IGNORECASE),
    re.compile(r"\b(valeur|titre|action)\s+(à|a)\s+(acheter|vendre|suivre)\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Chiffres financiers non horodatés
# ---------------------------------------------------------------------------

# Détecte :
# - montants monétaires (``94 Md€``, ``2,3 millions``, ``5k€``) — un seul
#   chiffre suffit côté montant car l'unité (``€``, ``millions``…) lève
#   l'ambiguïté ;
# - libellés ``CA``, ``chiffre d'affaires``, ``résultat net``, ``effectif``
#   suivis d'**au moins 2 chiffres** (ex : ``CA: 94``, ``effectif 120``).
#   On exige 2+ chiffres pour éviter les faux positifs sur des phrases
#   sans contexte chiffré majeur (``effectif: 1 employé``, ``CA des
#   années 1980``) qui déclenchaient à tort le disclaimer
#   ``missing_bilan_date``.
MONEY_RE = re.compile(
    r"\b(?:\d[\d\s.,]*\s?(?:€|md€|m€|k€|milliards?|millions?)"
    r"|(?:CA|chiffre\s+d['’]affaires|résultat\s+net|resultat\s+net|effectif)"
    r"\s*[:=]?\s*\d{2,})",
    re.IGNORECASE,
)

# Contexte d'horodatage : ``bilan ... 2023``, ``clos ... 2023``,
# ``exercice ... 2023``. Fenêtre de 60 chars entre le mot-clef et
# l'année.
BILAN_CONTEXT_RE = re.compile(
    r"bilan[^.]{0,60}\d{4}|clos[^.]{0,60}\d{4}|exercice[^.]{0,60}\d{4}",
    re.IGNORECASE,
)


def _has_orphan_money_without_bilan(text: str) -> bool:
    """True si au moins un chiffre financier n'a pas de mention de bilan
    dans les 200 caractères qui suivent sa position."""
    for m in MONEY_RE.finditer(text):
        window = text[m.start() : m.start() + 200]
        if not BILAN_CONTEXT_RE.search(window):
            return True
    return False


# ---------------------------------------------------------------------------
# Schémas Pydantic (parsable par S06 / S07 pour agrégation stats)
# ---------------------------------------------------------------------------


class OutputValidationResult(BaseModel):
    """Résultat structuré de ``validate_response``.

    Attributes:
        valid: ``True`` ssi ``issues == []``.
        issues: liste de reason_codes (enum stable). Les consumers
            matchent sur ``REASON_CODE_*`` via ``==``, pas substring.
        sirens_in_text: SIREN Luhn-valides cités dans la réponse
            (sorted).
        sirens_in_tool_results: SIREN Luhn-valides présents dans les
            tool_results (``allowed_sirens`` passé à
            ``validate_response``) (sorted).
        orphan_sirens: ``sirens_in_text - sirens_in_tool_results``
            (sorted).
    """

    valid: bool
    issues: list[str] = Field(default_factory=list)
    sirens_in_text: list[str] = Field(default_factory=list)
    sirens_in_tool_results: list[str] = Field(default_factory=list)
    orphan_sirens: list[str] = Field(default_factory=list)


def validate_response(
    text: str,
    allowed_sirens: set[str],
) -> OutputValidationResult:
    """Valide la réponse finale de l'agent.

    Args:
        text: réponse finale concaténée (tous les ``text`` events du
            turn, ou ``_stringify`` du dernier assistant message).
        allowed_sirens: SIREN Luhn-valides extraits des tool_results de
            ce turn. Le pipeline les construit via
            ``extract_sirens(content_preview)``.

    Returns:
        ``OutputValidationResult`` structuré.
    """
    sirens_in_text = extract_sirens(text, luhn_only=True)
    issues: list[str] = []

    orphan_sirens = sirens_in_text - allowed_sirens
    if orphan_sirens:
        issues.append(REASON_CODE_HALLUCINATION_ORPHAN_SIRENS)

    for pattern in ADVISORY_PATTERNS:
        if pattern.search(text):
            issues.append(REASON_CODE_ADVISORY_LANGUAGE)
            break  # un seul flag suffit — l'application de ``degrade`` est idempotente

    if _has_orphan_money_without_bilan(text):
        issues.append(REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE)

    return OutputValidationResult(
        valid=(len(issues) == 0),
        issues=issues,
        sirens_in_text=sorted(sirens_in_text),
        sirens_in_tool_results=sorted(allowed_sirens),
        orphan_sirens=sorted(orphan_sirens),
    )


# ---------------------------------------------------------------------------
# Dégradation (cahier §R11, §R14, §16.3)
# ---------------------------------------------------------------------------


def degrade(result: OutputValidationResult, text: str) -> tuple[str, bool]:
    """Applique la dégradation sur la réponse selon les issues détectées.

    Politique (cf. S05 phase 1 elicitation, ajustée par la review S05) :

    - ``orphan_sirens`` → disclaimer visible + ``needs_llm_retry=True``
      (exploité post-MVP ; MVP ignore cette valeur).
    - ``missing_bilan_date`` → disclaimer « dates manquantes, vérifier
      sur Pappers ».
    - ``advisory_language`` → disclaimer ajouté **en pied** (cf.
      ``DISCLAIMER_ADVISORY``). Le sub-regex initial transformait
      ``"Je te conseille d'investir"`` en
      ``"[reformulation neutre] d'investir"``, grammaticalement cassé
      et trompeur côté UX. Un disclaimer explicite est plus clair pour
      l'utilisateur ET conserve l'intégrité de la réponse originale,
      ce qui permet à un humain de juger de la qualité de l'agent.

    Returns:
        ``(text_dégradé, needs_llm_retry)`` — ``needs_llm_retry`` est
        True uniquement sur orphan SIREN (cf. R11).
    """
    needs_retry = bool(result.orphan_sirens)
    disclaimers: list[str] = []

    if result.orphan_sirens:
        disclaimers.append(
            f"⚠ SIREN cités non retrouvés dans les sources Pappers : "
            f"{', '.join(result.orphan_sirens)}. À vérifier directement "
            f"sur pappers.fr avant usage."
        )
    if REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE in result.issues:
        disclaimers.append(
            "⚠ Certains chiffres ne sont pas horodatés (date de bilan "
            "manquante). Vérifier sur Pappers pour le contexte exact."
        )
    if REASON_CODE_ADVISORY_LANGUAGE in result.issues:
        disclaimers.append(DISCLAIMER_ADVISORY)
    if disclaimers:
        text = text + "\n\n" + "\n".join(disclaimers)
    return text, needs_retry
