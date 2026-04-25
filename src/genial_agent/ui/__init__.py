"""Helpers UI Chainlit pour S06.

Modules :

- ``starters`` : déclaration des 4 starters (cf. cahier §16.1).
- ``post_process`` : SIREN linkify (regex + Markdown) + badge modèle.
- ``entity_tracker`` : extraction et formatage de la bannière "Entité
  active" (cf. cahier §16.2).
- ``events`` : dispatcher event → callback UI (réduit l'éparpillement
  de ``if/elif`` dans ``app.on_message``).

Aucune logique métier ici (validation, scoring, agent). Tout passe par
``run_guarded_turn`` côté S05.
"""
