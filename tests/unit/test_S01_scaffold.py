"""Smoke tests du scaffold S01. Vérifie que le packaging est correct."""

from __future__ import annotations


def test_package_importable() -> None:
    import genial_agent

    assert genial_agent.__name__ == "genial_agent"


def test_package_has_version() -> None:
    """La version doit être exposée depuis __init__.py pour le /health (S07)."""
    from genial_agent import __version__

    assert isinstance(__version__, str)
    assert __version__  # non vide
