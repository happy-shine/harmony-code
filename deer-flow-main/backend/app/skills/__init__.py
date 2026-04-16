"""Skill installation helpers.

This package is the single owner of the ``<HARMONY_DATA_DIR>/skills_store/``
filesystem layout. Callers (routers) do the DB insert; the helpers in
:mod:`app.skills.installer` only handle the filesystem side so they stay
trivially testable without touching the database.
"""
