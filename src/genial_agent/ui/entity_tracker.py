"""Extraction de l'entité active pour la bannière multi-turn (cahier §16.2).

Sources d'entité scannées dans l'ordre :

1. ``content_preview`` JSON parseable d'un ``tool_result`` — confirmation
   que Pappers a réellement résolu l'entité.
2. Inputs des ``tool_use`` (``company_name``, ``denomination``, ``siren``)
   — capturés au plus tôt, dispo même si Pappers timeout sur les
   tool_results suivants.
3. SIREN brut dans n'importe quel ``content_preview`` text + un nom
   provenant des inputs ``tool_use``.

Heuristique : on retourne la **dernière** entité résolue (parcours
``reversed``) → après chaînage U3 sur Carrefour puis Casino, on garde
Casino — l'utilisateur posera son follow-up dessus le plus souvent.

Cette logique est triviale au regard du contrat S03 (``content_preview``
tronqué à 200 chars) — pour les tools Pappers utilisés en MVP
(``sirenisateur``, ``recherche-entreprises``, ``recherche-dirigeants``,
``cartographie-entreprise``…), le SIREN figure en tête du payload JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from genial_agent.guardrails.sirens import valid_siren

SIREN_RE = re.compile(r"\b(\d{9})\b")
# Champs Pappers connus pour porter le nom et le SIREN d'une entité.
NAME_KEYS: tuple[str, ...] = (
    "denomination",
    "nom_entreprise",
    "denomination_usuelle",
    "name",
    "company_name",
)
SIREN_KEYS: tuple[str, ...] = ("siren", "siren_formatted")


@dataclass(frozen=True)
class ActiveEntity:
    """Entité active à afficher dans la bannière multi-turn (§16.2)."""

    name: str
    siren: str


@dataclass
class TurnTracker:
    """Accumule les inputs et previews collectés pendant un turn pour
    reconstruire l'entité active en fin de turn.

    Instance créée en début de ``on_message``, jetée en fin. **Pas
    stockée en cl.user_session** — la bannière elle-même l'est.

    On utilise ``field(default_factory=list)`` pour éviter le piège du
    mutable default partagé entre instances.
    """

    tool_use_inputs: list[dict] = field(default_factory=list)
    tool_result_previews: list[str] = field(default_factory=list)

    def record_tool_use(self, event_input: object) -> None:
        """Append l'input d'un event ``tool_use`` (silencieusement no-op
        si ce n'est pas un dict — ``input`` est typé ``dict`` côté S03,
        mais on tolère les events futurs malformés)."""
        if isinstance(event_input, dict):
            self.tool_use_inputs.append(event_input)

    def record_tool_result(self, content_preview: object) -> None:
        """Append le ``content_preview`` d'un event ``tool_result``
        (no-op si ce n'est pas une str)."""
        if isinstance(content_preview, str):
            self.tool_result_previews.append(content_preview)


def _scan_dict_for_entity(d: dict) -> ActiveEntity | None:
    """Cherche un couple (name, siren) dans un dict (input ou JSON parsé).

    Le SIREN doit être un string 9-chiffres exactement (espaces tolérés
    et strippés). On accepte tout name string non-vide trouvé dans la
    liste ``NAME_KEYS`` par ordre de priorité.
    """
    name: str | None = None
    siren: str | None = None
    for key in NAME_KEYS:
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    for key in SIREN_KEYS:
        v = d.get(key)
        if isinstance(v, str):
            cleaned = v.replace(" ", "")
            # S09.7 hotfix : on impose la clef Luhn même quand le SIREN
            # vient d'un champ explicite ``siren`` du payload — défense
            # en profondeur contre un payload exotique ou un futur tool
            # qui exposerait un identifiant interne sous le même nom.
            if SIREN_RE.fullmatch(cleaned) and valid_siren(cleaned):
                siren = cleaned
                break
    if name and siren:
        return ActiveEntity(name=name, siren=siren)
    return None


def extract_active_entity(tracker: TurnTracker) -> ActiveEntity | None:
    """Retourne la dernière entité résolue du turn, ou ``None``.

    Parcours :

    1. ``tool_result_previews`` en ordre inverse, on tente ``json.loads``
       et on cherche un couple (name, siren).
    2. ``tool_use_inputs`` en ordre inverse — si l'input contient un
       SIREN c'est gagné, sinon on garde le 1er nom trouvé en mémoire.
    3. Si on a un nom orphelin, on cherche un SIREN brut dans n'importe
       quel preview text (toujours en ordre inverse) pour le compléter.

    None si aucun couple (name, siren) reconstruisable → la bannière
    n'est pas mise à jour ce tour-ci.
    """
    # Priorité 1 : content_previews JSON parseables.
    for preview in reversed(tracker.tool_result_previews):
        try:
            data = json.loads(preview)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            ent = _scan_dict_for_entity(data)
            if ent:
                return ent

    # Priorité 2 : inputs de tool_use (souvent ``{"company_name": "LVMH"}``).
    last_name: str | None = None
    for tu_input in reversed(tracker.tool_use_inputs):
        ent = _scan_dict_for_entity(tu_input)
        if ent:
            return ent
        if last_name is None and isinstance(tu_input, dict):
            for key in NAME_KEYS:
                v = tu_input.get(key)
                if isinstance(v, str) and v.strip():
                    last_name = v.strip()
                    break

    # Priorité 3 : SIREN brut dans n'importe quel preview text + nom orphan.
    # S09.7 hotfix : check Luhn obligatoire — sinon on capture des
    # entiers 9-chiffres collés dans un JSON (ex: ``"resultat":-453301347``
    # → 453301347 matche \\b\\d{9}\\b mais n'est pas un SIREN). Bug
    # observé live U3 (bannière "SIREN 453301347" qui était en fait
    # le résultat net Carrefour Hyper 2023).
    if last_name:
        for preview in reversed(tracker.tool_result_previews):
            for candidate in SIREN_RE.findall(preview):
                if valid_siren(candidate):
                    return ActiveEntity(name=last_name, siren=candidate)

    return None


def format_banner(entity: ActiveEntity | None) -> str | None:
    """Markdown de la bannière. ``None`` si pas d'entité résolue."""
    if entity is None:
        return None
    return (
        f"📌 **Entité active** : {entity.name} "
        f"(SIREN [{entity.siren}](https://www.pappers.fr/entreprise/{entity.siren}))"
    )
