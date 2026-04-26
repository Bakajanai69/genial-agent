"""Payload Vault — offload générique des tool results MCP volumineux.

Vault scoped session (jamais sur disque, jamais cross-session). Stocke
les payloads bruts > ``OFFLOAD_THRESHOLD_CHARS`` et expose un index
compact au LLM, qui peut ensuite ré-interroger via ``payload_inspect`` /
``payload_search``.

Aucune logique métier spécifique à un MCP : le module ne sait que
naviguer du JSON arbitraire (dict / list / scalaire). Il marche pour
tout serveur MCP qui retourne du JSON volumineux. Garantie maintenue
par grep dans la review S09.5 (cf. story §"Check-list spécifique").

Inspiration :

- Filesystem offload Deep Agents (LangChain) — sans la dep LangChain.
- Memory Tool Anthropic (``memory_20250818``) — sans la convention
  ``/memories/`` ni la persistence cross-session.

Cf. ``docs/stories/S09.5-mcp-payload-handling.md`` §"Approche J retenue".

Sécurité (review S09.5 phase 3) :

- ``_walk`` ne touche **jamais** au filesystem ni aux attributs Python
  d'un objet : c'est un walk déterministe sur ``dict``/``list`` parsés
  par ``json.loads``. Les segments de path qui ressemblent à
  ``__class__`` sont traités comme des clés de dict ordinaires. Si
  elles n'existent pas (ce qui est le cas sur du JSON normal), on
  retourne ``{"_error": ...}``.
- ``search`` compile un ``re.Pattern`` sans ``re.X``/``re.U``
  potentiellement permissifs et **borne** explicitement le nombre de
  matches via ``max_matches``. La protection contre le ReDoS catastrophe
  est best-effort : on borne aussi la taille de la chaîne fouillée et
  on tronque la regex utilisateur à ``MAX_REGEX_PATTERN_CHARS``.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

# --- Tunables (cf. story §"Données factuelles") -----------------------------

# Au-dessous de ce seuil, le payload passe direct (pas d'offload).
# Calibré sur le tableau §"Données factuelles" de la story S09.5 :
# un payload moyen (~19 K) doit être offloadé, un petit (~4 K) passe
# direct. 12 K = mid-point.
OFFLOAD_THRESHOLD_CHARS = 12_000

# Budget hard de l'index renvoyé au LLM (squelette + previews). Si
# l'index dépasse ce budget, on tronque les previews et on simplifie
# le squelette. Cf. test ``test_build_index_under_4kb``.
INDEX_BUDGET_CHARS = 4_000

# Cap par défaut sur ``payload_inspect.max_chars`` (l'agent peut le
# baisser, mais pas le monter au-delà de ``LOOKUP_MAX_CHARS_HARD``).
LOOKUP_MAX_CHARS_DEFAULT = 8_000
LOOKUP_MAX_CHARS_HARD = 12_000

PREVIEW_HEAD_CHARS = 400
PREVIEW_TAIL_CHARS = 400
SKELETON_MAX_DEPTH = 3
SKELETON_MAX_KEYS_PER_LEVEL = 30

# S09.7 A4 — quand ``_walk_simple`` retourne ``_error key missing``, on
# liste les clés disponibles du nœud parent pour aider le LLM à se
# corriger. Cap à 10 pour éviter qu'un dict à 100 clés explose la
# fenêtre de contexte.
AVAILABLE_KEYS_LIMIT = 10

# S09.7 B1 — marqueur lisible apposé en suffixe d'une clé string
# numérique pure dans le squelette (``"2023" → "2023↹"``). Distingue
# string-key vs index pour le LLM. ``↹`` (U+21B9) est neutre (pas de
# balise XML, pas d'instruction). Cf. story §"Architecture phase 1 — Axe
# 2 B1".
DIGIT_STRING_KEY_MARKER = "↹"

# S09.7 B3 — sample d'item type dans les arrays de dicts. On expose les
# vrais champs du 1er item au lieu d'un simple ``<dict>[N items]``,
# pour que le LLM choisisse le bon path du premier coup.
SKELETON_SAMPLE_KEYS_LIMIT = 8

# Limite sur la taille de la regex que l'agent peut envoyer. Évite des
# patterns absurdement longs (prompt injection indirecte ou bug LLM).
MAX_REGEX_PATTERN_CHARS = 200
SEARCH_DEFAULT_CONTEXT_CHARS = 200
SEARCH_DEFAULT_MAX_MATCHES = 10
SEARCH_HARD_MAX_MATCHES = 30

# Review S09.7 — cap dur sur le nombre de matches retournés par
# ``_walk_with_jsonpath_ng``. Filet de sécurité contre les wildcards
# pathologiques (``$..*`` sur un payload Carrefour 706 K peut renvoyer
# plusieurs dizaines de milliers de nœuds avant que le cap chars
# ``LOOKUP_MAX_CHARS_HARD`` ne tronque le ``json.dumps`` final). 1 000
# est très large vs ce qu'on observe en prod (G2 = 39 homonymes, G5 =
# 16 entreprises) — ne tronque rien en pratique, borne uniquement les
# cas dégénérés.
JSONPATH_MAX_MATCHES = 1_000


# --- Vault ------------------------------------------------------------------


@dataclass
class PayloadVault:
    """Vault session-scoped. Rangé sur ``ConversationState``.

    Pas cross-session : chaque session Chainlit a son propre vault, et
    le vault meurt avec elle. Cap implicite mémoire = durée de la
    session. Pas de TTL.
    """

    _store: dict[str, str] = field(default_factory=dict)

    def store(self, raw: str) -> str:
        """Range un payload brut, retourne un ``payload_id`` court."""
        pid = f"p_{secrets.token_hex(4)}"
        self._store[pid] = raw
        return pid

    def get(self, pid: str) -> str | None:
        """Lookup par ``payload_id``. ``None`` si inconnu / expiré."""
        return self._store.get(pid)

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


# --- Index ------------------------------------------------------------------


def build_index(raw: str, payload_id: str) -> dict[str, Any]:
    """Construit un index générique du payload.

    Pas de logique métier : on profile uniquement la **structure** JSON
    (squelette à profondeur N, tailles d'arrays, previews head/tail).
    Sortie compacte (< ``INDEX_BUDGET_CHARS``), prête à être injectée
    dans un ``tool_result`` Anthropic à la place du payload brut.

    S09.7 enrichissements :

    - skeleton avec sample d'item dans les arrays de dicts (B3) +
      annotation ``↹`` sur les clés numériques pures de dict (B1).
    - hint d'introspection itérative : sample → wildcard → search (E1+E3).
    """
    parsed = _safe_parse_json(raw)
    index: dict[str, Any] = {
        "_payload_id": payload_id,
        "_size_chars": len(raw),
        "_skeleton": _skeleton(parsed, depth=0),
        "_array_sizes": _collect_array_sizes(parsed),
        "_preview_head": raw[:PREVIEW_HEAD_CHARS],
        "_preview_tail": raw[-PREVIEW_TAIL_CHARS:] if len(raw) > PREVIEW_HEAD_CHARS else "",
        "_inspect_hint": (
            "Use payload_inspect(payload_id, json_path) to read a specific "
            "subtree, or payload_search(payload_id, pattern) to grep the raw "
            "JSON. Path syntax: '$.foo.bar[42].baz', wildcard "
            "'$.arr[*].field' (returns array of all matches), recursive "
            "descent '$..key', negative index '$.arr[-1]' (last item). "
            "A numeric segment on a dict is a string-key (e.g. '$.2023[0]' "
            "on date-keyed payloads, marked with '"
            + DIGIT_STRING_KEY_MARKER
            + "' in the skeleton); on a list it's an integer index. "
            "Recommended pattern for an array of objects: (1) read the "
            "field names from the sample shown in the skeleton, (2) "
            "extract everything in one call via wildcard "
            "'$.arr[*].chosen_field' instead of N inspects by index. "
            "(3) If no field has the expected name, payload_search with a "
            'regex on raw JSON keys (e.g. \'"key_a"|"key_b"\') to find '
            "the actual key name used by the upstream API."
        ),
    }
    encoded = json.dumps(index, ensure_ascii=False)
    if len(encoded) <= INDEX_BUDGET_CHARS:
        return index
    return _shrink_index(index)


def _shrink_index(index: dict[str, Any]) -> dict[str, Any]:
    """Réduit l'index si la version pleine dépasse ``INDEX_BUDGET_CHARS``.

    Étapes : (1) raccourcit les previews à 200 chars, (2) tronque le
    squelette à profondeur 2, (3) garde au plus 20 array sizes triées
    par taille décroissante. À utiliser uniquement quand le payload
    contient un squelette dense (rare : la majorité des payloads JSON
    rentrent dans 4 K avec la profondeur 3 standard).
    """
    shrunk = dict(index)
    shrunk["_preview_head"] = shrunk["_preview_head"][:200]
    shrunk["_preview_tail"] = shrunk["_preview_tail"][:200]
    shrunk["_skeleton"] = _truncate_skeleton(shrunk["_skeleton"], max_depth=2)
    sizes = shrunk.get("_array_sizes", {})
    if isinstance(sizes, dict) and len(sizes) > 20:
        kept = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:20]
        shrunk["_array_sizes"] = dict(kept)
        shrunk["_array_sizes_truncated"] = True
    return shrunk


def _truncate_skeleton(node: Any, max_depth: int, depth: int = 0) -> Any:
    if depth >= max_depth:
        return _summarize(node)
    if isinstance(node, dict):
        return {k: _truncate_skeleton(v, max_depth, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return node  # déjà résumé par ``_skeleton``
    return node


# --- Inspect & search -------------------------------------------------------


def inspect(raw_json: str, json_path: str, max_chars: int = LOOKUP_MAX_CHARS_DEFAULT) -> str:
    """Walk déterministe sur le JSON. Retourne la valeur encodée JSON.

    Tronqué à ``max_chars`` avec marker explicite si nécessaire. La
    valeur est **verbatim** (pas de reformulation), garantie par
    ``json.dumps`` sur le ``json.loads`` du payload.

    S09.7 amélioration 3 : si le path final accède à un index ``[N]``
    d'une list et qu'il y a > 0 items restants après cet index, on
    ajoute un footer signalétique `_meta: N more siblings` + hint
    `use [*] for all`. Aide l'agent à savoir qu'il n'a vu qu'un seul
    item d'une liste plus longue (anti-pattern G2 où l'agent ne va
    pas chercher plus loin que ``resultats[0]`` et ``resultats[2]``).
    """
    capped = max(0, min(int(max_chars), LOOKUP_MAX_CHARS_HARD))
    parsed = _safe_parse_json(raw_json)
    value = _walk(parsed, json_path)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)

    # Footer signalétique : remaining siblings sur index terminal.
    footer = _remaining_siblings_footer(parsed, json_path)
    if footer:
        encoded = encoded + footer

    if len(encoded) > capped:
        return encoded[:capped] + "\n…[lookup truncated]"
    return encoded


def _remaining_siblings_footer(parsed: Any, json_path: str) -> str:
    """Calcule le footer ``_meta: N more siblings`` quand le path
    final accède à un index ``[N]`` ou un segment numérique sur une
    list, et qu'il y a plus d'items après.

    Retourne `""` si pas applicable (path non-terminal-index, parent
    introuvable, hors range). Best-effort : silencieusement vide en
    cas de path complexe (wildcard, recursive descent) — déjà couvert
    par l'amélioration 2.
    """
    if not isinstance(json_path, str):
        return ""
    # Skip les paths à wildcard (couverts par amélioration 2).
    if any(tok in json_path for tok in _JSONPATH_WILDCARD_TOKENS):
        return ""
    cleaned = json_path.lstrip("$").lstrip(".")
    if not cleaned:
        return ""
    segments = [m.group(1) or m.group(2) for m in _PATH_SEGMENT.finditer(cleaned)]
    if not segments:
        return ""
    last = segments[-1]
    last_clean = last.rstrip(DIGIT_STRING_KEY_MARKER) if isinstance(last, str) else last
    if not isinstance(last_clean, str) or not last_clean.lstrip("-").isdigit():
        return ""

    # Re-walk au parent (path moins le dernier segment) pour mesurer
    # la taille de la liste source.
    parent_segments = segments[:-1]
    parent_path = "$"
    if parent_segments:
        # Reconstruire un path canonique. Les indices numériques
        # peuvent être encodés ``[N]`` ou ``.N`` indifféremment côté
        # walker — on choisit ``.N`` pour la simplicité.
        parent_path = "$." + ".".join(str(s) for s in parent_segments)
    parent = _walk_simple(parsed, parent_path)
    if not isinstance(parent, list):
        return ""
    try:
        idx = int(last_clean)
    except ValueError:
        return ""
    if idx < 0:
        idx += len(parent)
    if idx < 0 or idx >= len(parent):
        return ""
    remaining = len(parent) - idx - 1
    if remaining <= 0:
        return ""
    parent_path_clean = parent_path.lstrip("$").lstrip(".") or "<root>"
    return (
        f"\n…[_meta: {remaining} more siblings at this index "
        f"(use '$.{parent_path_clean}[*]' for all items, or "
        f"'$.{parent_path_clean}[N]' with N up to {len(parent) - 1})]"
    )


def search(
    raw_json: str,
    pattern: str,
    max_matches: int = SEARCH_DEFAULT_MAX_MATCHES,
    context_chars: int = SEARCH_DEFAULT_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    """Regex search avec contexte. Retourne ``[{match, position, context}]``.

    La regex est compilée avec ``IGNORECASE | MULTILINE`` (sans
    ``re.X``/``re.U`` pour rester strict). ``max_matches`` est
    explicitement borné par ``SEARCH_HARD_MAX_MATCHES`` pour limiter
    l'overhead côté LLM. Pattern tronqué à ``MAX_REGEX_PATTERN_CHARS``.
    """
    pattern = (pattern or "")[:MAX_REGEX_PATTERN_CHARS]
    capped_max = max(1, min(int(max_matches), SEARCH_HARD_MAX_MATCHES))
    try:
        rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        return [{"error": f"invalid regex: {exc}"}]

    out: list[dict[str, Any]] = []
    half = max(0, int(context_chars)) // 2
    for m in rx.finditer(raw_json):
        if len(out) >= capped_max:
            break
        start = max(0, m.start() - half)
        end = min(len(raw_json), m.end() + half)
        out.append(
            {
                "match": m.group(0),
                "position": m.start(),
                "context": raw_json[start:end],
            }
        )
    return out


# --- Helpers privés ---------------------------------------------------------


def _safe_parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"_raw": raw[:1000], "_note": "payload non-JSON"}


def _is_digit_string_key(k: Any) -> bool:
    """``True`` si ``k`` est une string qui ressemble à un nombre pur
    (clé string numérique d'un dict — ex: ``"2023"``, ``"-1"``, ``"0"``).

    On accepte le signe ``-`` en tête (pour rester cohérent avec la
    syntaxe d'index négatif côté list), bien que ce soit rare en
    pratique côté JSON.
    """
    return isinstance(k, str) and k.lstrip("-").isdigit()


def _skeleton(node: Any, depth: int) -> Any:
    """Squelette structurel borné en profondeur.

    Modifié S09.7 :

    - **B1** : préfixe la clé string numérique pure du dict par
      ``↹`` (U+21B9) pour signaler "string-key, pas index" au LLM.
      Couvre le piège G4 ``$.2023[0]`` qui était mal interprété.
    - **B3** : pour un array de dicts, expose un sample du 1er item
      (clés visibles, valeurs résumées) au lieu du simple
      ``<dict>[N items]``. Le LLM choisit le bon path du premier coup
      sans avoir à inspect par index pour découvrir les champs.
    """
    if depth >= SKELETON_MAX_DEPTH:
        return _summarize(node)
    if isinstance(node, dict):
        items = list(node.items())[:SKELETON_MAX_KEYS_PER_LEVEL]
        out: dict[Any, Any] = {}
        for k, v in items:
            display_k = f"{k}{DIGIT_STRING_KEY_MARKER}" if _is_digit_string_key(k) else k
            out[display_k] = _skeleton(v, depth + 1)
        return out
    if isinstance(node, list):
        if not node:
            return []
        first = node[0]
        if isinstance(first, dict):
            sample_keys = list(first.keys())[:SKELETON_SAMPLE_KEYS_LIMIT]
            sample = {k: _summarize(first[k]) for k in sample_keys}
            if len(node) > 1:
                return [sample, f"…{len(node) - 1} more dict items"]
            return [sample]
        if isinstance(first, list):
            if len(node) > 1:
                return [f"<nested list[{len(first)}]>", f"…{len(node) - 1} more lists"]
            return [f"<nested list[{len(first)}]>"]
        # Array de scalaires (str / int / etc.) : on garde la sémantique
        # historique « <type>[N items] » qui suffit largement.
        return [f"<{type(first).__name__}>[{len(node)} items]"]
    return _summarize(node)


def _summarize(node: Any) -> str:
    if isinstance(node, str):
        return f"<str:{len(node)}>"
    if isinstance(node, bool):
        return "<bool>"
    if isinstance(node, int):
        return "<int>"
    if isinstance(node, float):
        return "<float>"
    if node is None:
        return "<NoneType>"
    if isinstance(node, dict):
        return f"<dict:{len(node)} keys>"
    if isinstance(node, list):
        return f"<list:{len(node)}>"
    return f"<{type(node).__name__}>"


def _collect_array_sizes(
    node: Any,
    prefix: str = "$",
    out: dict[str, int] | None = None,
) -> dict[str, int]:
    if out is None:
        out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            _collect_array_sizes(v, f"{prefix}.{k}", out)
    elif isinstance(node, list):
        out[prefix] = len(node)
        if node and isinstance(node[0], dict | list):
            _collect_array_sizes(node[0], f"{prefix}[*]", out)
    return out


# Path tokenizer : reconnaît ``key`` (alphanum + _ + -) et ``[N]``,
# ``[-1]`` (index entiers signés). Le preprocessing strip ``$`` et
# ``.`` initiaux pour accepter à la fois ``$.foo[0]`` et ``foo[0]``.
_PATH_SEGMENT = re.compile(r"([^.\[\]]+)|\[(-?\d+)\]")

# S09.7 A2 — tokens qui déclenchent la délégation à ``jsonpath-ng``
# pour les expressions wildcards / recursive descent / filtres. Tout
# path qui en contient un sort du walker custom.
_JSONPATH_WILDCARD_TOKENS = ("[*]", "..", "[?", ".*")


def _walk(node: Any, path: str) -> Any:
    """Walk avec délégation conditionnelle à ``jsonpath-ng``.

    - Path **sans** wildcard (le cas le plus fréquent côté LLM) →
      ``_walk_simple`` (M1 livré S09.5) avec désambiguïsation
      dict-vs-list par contexte.
    - Path **avec** ``[*]`` / ``..`` / ``[?`` / ``.*`` → délégué à
      ``jsonpath-ng`` (S09.7 Axe 1 A2). Permet à l'agent d'extraire en
      1 call ce qu'il faisait en N (ex : ``$.results[*].id`` sur un
      retour upstream qui contient un array d'objets).

    Sécurité : la lib jsonpath-ng vendore ``ply`` (parser) sans pickle
    depuis 1.8.0 (CVE-2025-56005 patchée). Aucun accès filesystem ni
    aux attributs Python d'un objet. ``parse()`` peut lever sur path
    invalide → on renvoie ``_error`` pour que le LLM se corrige.
    """
    if not isinstance(path, str):
        return {"_error": f"json_path must be a string, got {type(path).__name__}"}
    if any(tok in path for tok in _JSONPATH_WILDCARD_TOKENS):
        return _walk_with_jsonpath_ng(node, path)
    return _walk_simple(node, path)


def _walk_simple(node: Any, path: str) -> Any:
    """Walk ``$.foo.bar[0].baz`` ou ``foo.bar[0].baz`` ou ``arr[-1]``.

    Désambiguïsation **par contexte** : un segment numérique pur
    (``"2023"``, ``"0"``) est interprété comme

    - **clé string** si le nœud courant est un ``dict`` (utile quand
      l'upstream renvoie un mapping à clés numériques type
      ``{"2023": [...]}``),
    - **index entier** si le nœud courant est une ``list``.

    Cela évite le piège où ``$.2023[0]`` était rejeté à tort sur un
    dict avec une clé string numérique. Les tests
    ``test_walk_digit_key_in_dict`` et ``test_walk_index_on_list_*``
    figent les deux comportements.

    Sécurité : on ne touche **jamais** aux attributs Python (``__class__``,
    ``__dict__``, etc.). Tout segment est traité soit comme une clé de
    dict ordinaire, soit comme un index entier de list. Si la clé est
    introuvable ou l'index hors range → retour ``{"_error": "..."}``
    pour que le LLM puisse réajuster.

    S09.7 A4 : sur ``key missing``, on liste les clés disponibles du
    nœud parent (jusqu'à ``AVAILABLE_KEYS_LIMIT``) — le LLM peut
    corriger sans relancer un inspect à l'aveugle.
    """
    cleaned = path.lstrip("$").lstrip(".")
    if not cleaned:
        return node
    segments = [m.group(1) or m.group(2) for m in _PATH_SEGMENT.finditer(cleaned)]
    if not segments:
        return {"_error": f"empty path after parsing '{path}'"}

    cur: Any = node
    for seg in segments:
        # S09.7 B1 : si le LLM a recopié l'annotation ``↹`` du skeleton,
        # on la strip avant de matcher la clé du dict. Tolérance pour
        # éviter de pénaliser un agent qui aurait copié-collé.
        seg_clean = seg.rstrip(DIGIT_STRING_KEY_MARKER) if isinstance(seg, str) else seg
        if isinstance(cur, dict):
            # Dict : segment toujours interprété comme clé string, même
            # numérique pur. Couvre les mappings à clés numériques type
            # ``{"2023": [...]}``.
            if seg_clean in cur:
                cur = cur[seg_clean]
            else:
                return {
                    "_error": f"key '{seg_clean}' missing at '{path}'",
                    "_available_keys": [str(k) for k in list(cur.keys())[:AVAILABLE_KEYS_LIMIT]],
                }
        elif isinstance(cur, list):
            # List : segment doit être un index entier (signé pour -1).
            if not seg_clean.lstrip("-").isdigit():
                return {
                    "_error": (
                        f"list expects integer index, got '{seg_clean}' at '{path}' "
                        f"(list has {len(cur)} items)"
                    )
                }
            try:
                idx = int(seg_clean)
            except ValueError:
                return {"_error": f"invalid index '{seg_clean}' at '{path}'"}
            if idx < 0:
                idx += len(cur)
            if idx < 0 or idx >= len(cur):
                return {"_error": f"index {seg_clean} out of range at '{path}'"}
            cur = cur[idx]
        else:
            # Scalaire (str/int/None/...) : impossible de naviguer plus loin.
            return {
                "_error": (
                    f"cannot navigate into {type(cur).__name__} at '{path}' "
                    f"(remaining segment '{seg_clean}')"
                )
            }
    return cur


def _walk_with_jsonpath_ng(node: Any, path: str) -> Any:
    """Délègue à ``jsonpath-ng.ext`` pour les paths à wildcards.

    Retourne :

    - la valeur unique si ``len(matches) == 1`` (plus ergonomique pour le
      LLM — pas de confusion list-vs-scalar),
    - **enveloppe dict** ``{"_extracted_values": [...], "_count": N,
      "_other_fields_available": [...]}`` quand le wildcard renvoie un
      array de **scalaires** depuis un parent dict (S09.7 amélioration 2).
      Encourage l'agent à voir les autres champs disponibles sur chaque
      item — règle l'anti-pattern où l'agent extrait un seul champ via
      ``$.items[*].field_unique`` et perd les champs frères.
    - la liste de valeurs si ``len(matches) >= 2`` et qu'au moins un
      match est un dict/list (l'agent a déjà la struct sous les yeux,
      pas besoin d'enveloppe).
    - ``{"_error": ...}`` si parse error / 0 match.

    Lazy import : on évite +50 ms de startup pour les sessions qui
    n'utilisent jamais de wildcard.
    """
    from jsonpath_ng.ext import parse  # lazy import

    try:
        expr = parse(path)
    except Exception as exc:  # noqa: BLE001 — parse error LLM, on remonte propre
        return {"_error": f"invalid jsonpath '{path}': {exc}"}
    found = expr.find(node)
    if not found:
        return {"_error": f"no match for '{path}' in payload"}
    # Review S09.7 — cap dur RAM sur les wildcards pathologiques
    # (``$..*`` sur un payload 700 K peut exploser). On borne avant
    # d'extraire les ``.value`` pour ne pas matérialiser N copies.
    truncated_count: int | None = None
    if len(found) > JSONPATH_MAX_MATCHES:
        truncated_count = len(found) - JSONPATH_MAX_MATCHES
        found = found[:JSONPATH_MAX_MATCHES]
    values = [m.value for m in found]
    if len(values) == 1:
        return values[0]

    # S09.7 amélioration 2 : si tous les matches sont des scalaires
    # (string/int/etc.) ET qu'ils proviennent d'un parent dict, on
    # enveloppe avec les **autres clés disponibles** sur ce parent. Le
    # LLM voit qu'il existe des champs frères et peut re-query sans le
    # ``.field`` terminal pour récupérer les items complets.
    if all(not isinstance(v, dict | list) for v in values):
        sample_keys = _extract_sibling_keys(found)
        if sample_keys:
            envelope: dict[str, Any] = {
                "_extracted_values": values,
                "_count": len(values),
                "_other_fields_available": sample_keys,
                "_hint": (
                    f"Extracted {len(values)} scalar values from '{path}'. "
                    f"Each item also has these fields: {sample_keys}. "
                    f"Re-query without the trailing '.field' to get full "
                    f"items, or chain another wildcard for a different field."
                ),
            }
            if truncated_count is not None:
                envelope["_truncated_more"] = truncated_count
            return envelope
    if truncated_count is not None:
        # Liste tronquée — on appose un marker textuel en queue pour
        # signaler au LLM (et garder le retour "list of matches" pour
        # ne pas casser les consommateurs existants qui attendent une
        # liste).
        return values + [
            f"…[_truncated: {truncated_count} more matches over JSONPATH_MAX_MATCHES={JSONPATH_MAX_MATCHES}]"
        ]
    return values


def _extract_sibling_keys(found: list[Any]) -> list[str]:
    """Trouve les clés du parent dict du 1er match jsonpath-ng.

    Utilisé par l'amélioration 2 pour signaler à l'agent les autres
    champs disponibles quand il extrait un seul scalaire via wildcard.
    Best-effort : si le parent n'est pas accessible (root match,
    structure exotique), retourne ``[]`` et le retour reste un array
    simple sans enveloppe.
    """
    try:
        first_ctx = found[0].context  # DatumInContext parent
        if first_ctx is None:
            return []
        parent_value = first_ctx.value
        if not isinstance(parent_value, dict):
            return []
        return [str(k) for k in list(parent_value.keys())[:SKELETON_SAMPLE_KEYS_LIMIT]]
    except (AttributeError, IndexError):
        return []
