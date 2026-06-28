# bibi-base — Laufzeit-Image für containerisierte Jobs (PLAN-8 Slice C / DESIGN §7.7).
#
# Enthält genau, was Job-Typen brauchen:
#   - bash + git + coreutils   → `job` (bash -c …) und Worktree-Schreiben
#   - node + claude-code CLI    → `claude` (claude -p …, Auth via ANTHROPIC_API_KEY)
#   - ripgrep                   → von claude-code intern genutzt
#
# Der Worktree wird zur Laufzeit als Volume nach /workspace gemountet (-w /workspace);
# stdout/stderr fängt der Host-Wrapper → output.jsonl. Kein bibi-Code im Image nötig
# (Exec-Backend hüllt nur das Child-argv in `docker run`).
#
# Bauen:  docker build -t bibi-base:dev -f docker/bibi-base/Dockerfile .
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git ca-certificates ripgrep \
    && rm -rf /var/lib/apt/lists/*

# claude-code CLI (Binary: `claude`); Auth zur Laufzeit über ANTHROPIC_API_KEY.
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# Non-root User: Claude Code läuft besser ohne root-Warnung; UID 1000 passt
# zu den meisten Host-Usern sodass gemountete Worktrees schreibbar bleiben.
RUN useradd -m -u 1000 -s /bin/bash bibi

# Trust-Dialog für /workspace vorab akzeptieren + volle Permissions (kein interaktives
# Nachfragen im Container). settings.json aus dem Worktree wird sonst ignoriert weil
# das Workspace-Trust fehlt.
RUN mkdir -p /home/bibi/.claude && \
    printf '{"projects":{"/workspace":{"hasTrustDialogAccepted":true}}}\n' \
        > /home/bibi/.claude.json && \
    printf '{"permissions":{"allow":["Bash(*)","Read(*)","Write(*)","Edit(*)","WebFetch(*)","WebSearch(*)","mcp__*"],"deny":[]}}\n' \
        > /home/bibi/.claude/settings.json && \
    chown -R bibi:bibi /home/bibi/.claude /home/bibi/.claude.json

USER bibi
WORKDIR /workspace
