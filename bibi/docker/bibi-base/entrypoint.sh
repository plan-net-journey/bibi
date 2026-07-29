#!/bin/bash
# PLAN-24 Befund 5: "arbitrary UID"-Konvention — eine per `--user <host-uid>:0`
# gestartete UID hat meist keinen /etc/passwd-Eintrag; sudo (und andere
# NSS-lesende Tools) lehnen das ab ("you do not exist in the passwd
# database"), obwohl die Sudoers-Regel gruppenbasiert ist (%root NOPASSWD).
# Standardfix für arbitrary-UID-Images (analog Red Hat UBI/OpenShift): einen
# passenden Eintrag zur Laufzeit ergänzen. /etc/passwd ist dafür GID-0-
# beschreibbar (s. Dockerfile).
set -e
if ! whoami >/dev/null 2>&1; then
    echo "bibi:x:$(id -u):0:bibi container user:/root:/bin/bash" >> /etc/passwd
    # sudo ruft PAM-Account-Prüfung (pam_unix) IMMER auf, auch mit NOPASSWD —
    # ohne shadow-Eintrag gilt der User als "locked" ("account validation
    # failure"). `*` = kein Passwort gesetzt, aber Account gültig (nicht `!`,
    # das heißt "gesperrt"); leere Aging-Felder = keine Ablauf-Beschränkung.
    echo "bibi:*:19000:0:99999:7:::" >> /etc/shadow
fi
exec "$@"
