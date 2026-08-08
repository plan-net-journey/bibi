"""Die Browser-Ebene als **Paket** — und das ist kein Formalismus.

Ohne diese Datei legt pytest ``tests/browser/`` selbst in den ``sys.path``, und
dann gibt es zwei Module namens ``conftest``: das hiesige und
``tests/conftest.py``. Welches ein ``from conftest import FAR_FUTURE_TS`` trifft,
entscheidet die Sammelreihenfolge — beim ersten Lauf traf es das falsche, und
vier Testdateien der Kernsuite fielen mit einem ``ImportError`` aus, der nach
einem Fehler in ihnen aussah.

Mit ``__init__.py`` heißt dieses Verzeichnis ``browser``, der ``sys.path``
bekommt ``tests/`` statt ``tests/browser/``, und die beiden Konfigurationen
heißen ``conftest`` und ``browser.conftest``.
"""
