# bibi-base — Laufzeit-Image für containerisierte Jobs (PLAN-8 Slice C / DESIGN §7.7).
#
# Enthält was Job-Typen brauchen:
#   - bash + git + coreutils   → `job` (bash -c …) und Worktree-Schreiben
#   - node + claude-code CLI    → `claude` (claude -p …, Auth via ANTHROPIC_API_KEY)
#   - ripgrep                   → von claude-code intern genutzt
#   - python3 + pip + venv      → Python-Jobs und App-Container
#   - uv                        → schnelles Python-Package-Management im Container
#   - sudo                      → für spontanes Nachinstallieren (PLAN-24 Befund 5)
#
# Ownership (PLAN-24 Befund 5, "arbitrary UID"-Konvention): das Exec-Backend
# startet den Container mit `--user <host-uid>:0` — die tatsächliche UID
# variiert pro Host/Aufruf, die GID ist bewusst immer 0 ("root"-Gruppe), damit
# `/etc/passwd`-unabhängig eine feste Identität (HOME=/root) und `sudo` (an
# die Gruppe gebunden, nicht an einen Usernamen) funktionieren. Von /workspace
# (Bind-Mount) geschriebene Dateien gehören dadurch exakt dem Host-User, kein
# nachträgliches chown vor `git commit` nötig. Für den seltenen Fall, dass ein
# Job auf OS-Ebene nachinstallieren muss, steht passwortloses `sudo` bereit
# (Gegenvorschlag User, PLAN-24: Container standardmäßig unprivilegiert,
# Root nur auf Zuruf — statt dauerhaft als root laufen).
#
# Der Worktree wird zur Laufzeit als Volume nach /workspace gemountet (-w /workspace);
# stdout/stderr fängt der Host-Wrapper → output.jsonl. Kein bibi-Code im Image nötig
# (Exec-Backend hüllt nur das Child-argv in `docker run`).
#
# Bauen:  docker build -t bibi-base:dev -f bibi/docker/bibi-base/Dockerfile bibi/docker/bibi-base
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git ca-certificates ripgrep sudo \
        python3 python3-pip python3-venv python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# Passwortloses sudo für die "root"-Gruppe (GID 0) — an die Gruppe gebunden,
# nicht an einen Usernamen, weil die tatsächliche UID (--user <host-uid>:0)
# von Host zu Host variiert und im Image kein /etc/passwd-Eintrag dafür hat.
RUN echo '%root ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/bibi-root-group \
    && chmod 0440 /etc/sudoers.d/bibi-root-group

# uv — schnelles Python-Package-Management (single binary, kein pip/curl nötig).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# claude-code CLI (Binary: `claude`); Auth zur Laufzeit über ANTHROPIC_API_KEY.
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# Trust-Dialog für /workspace vorab akzeptieren + volle Permissions.
# /root bleibt HOME (per -e HOME=/root erzwungen, s. exec_backend.build_exec),
# unabhängig von der tatsächlichen --user-UID — deshalb hier gruppen-
# beschreibbar für GID 0 machen (die beliebige UID hat immer GID 0).
RUN mkdir -p /root/.claude && \
    printf '{"projects":{"/workspace":{"hasTrustDialogAccepted":true}}}\n' \
        > /root/.claude.json && \
    printf '{"permissions":{"allow":["Bash(*)","Read(*)","Write(*)","Edit(*)","WebFetch(*)","WebSearch(*)","mcp__*"],"deny":[]}}\n' \
        > /root/.claude/settings.json && \
    chmod -R g+rwX /root

# Entrypoint: eine beliebige --user-UID hat oft keinen /etc/passwd-Eintrag —
# sudo lehnt das per NSS-Check ab, obwohl die Sudoers-Regel gruppenbasiert
# ist. Skript ergänzt den Eintrag zur Laufzeit (Standardfix für arbitrary-UID-
# Images), /etc/passwd dafür GID-0-beschreibbar.
COPY entrypoint.sh /entrypoint.sh
# /etc/shadow gehört auf Debian root:shadow (nicht GID 0) — erst umgruppieren,
# dann gruppen-beschreibbar machen, sonst greift GID 0 nicht.
RUN chmod +x /entrypoint.sh \
    && chmod g+rw /etc/passwd \
    && chgrp 0 /etc/shadow && chmod g+rw /etc/shadow
ENTRYPOINT ["/entrypoint.sh"]

WORKDIR /workspace
