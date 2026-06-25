"""Scheduling-Domäne: Datenmodell + reine Zustandsmaschine (DESIGN §5, PLAN-3).

Bewusst frei von DB und HTTP — ``models`` definiert die Typen, ``lifecycle`` die
Übergänge. Beide sind isoliert beweisbar (wie der ``PushDebouncer`` der Phase 2),
bevor sie an Persistenz oder Prozesse gekoppelt werden (PLAN-3 §3.0).
"""
