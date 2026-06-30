# bibi-base — Laufzeit-Image für containerisierte Jobs (PLAN-8 Slice C / DESIGN §7.7).
#
# Enthält was Job-Typen brauchen:
#   - bash + git + coreutils   → `job` (bash -c …) und Worktree-Schreiben
#   - node + claude-code CLI    → `claude` (claude -p …, Auth via ANTHROPIC_API_KEY)
#   - ripgrep                   → von claude-code intern genutzt
#   - python3 + pip + venv      → Python-Jobs und App-Container
#   - uv                        → schnelles Python-Package-Management im Container
#
# Container laufen als root → Jobs können apt/pip/uv zur Laufzeit nachinstallieren.
#
# Der Worktree wird zur Laufzeit als Volume nach /workspace gemountet (-w /workspace);
# stdout/stderr fängt der Host-Wrapper → output.jsonl. Kein bibi-Code im Image nötig
# (Exec-Backend hüllt nur das Child-argv in `docker run`).
#
# Bauen:  docker build -t bibi-base:dev -f docker/bibi-base/Dockerfile .
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git ca-certificates ripgrep \
        python3 python3-pip python3-venv python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# uv — schnelles Python-Package-Management (single binary, kein pip/curl nötig).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# claude-code CLI (Binary: `claude`); Auth zur Laufzeit über ANTHROPIC_API_KEY.
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# Trust-Dialog für /workspace vorab akzeptieren + volle Permissions.
# Root-Home /root/.claude — Container laufen als root (Jobs können nachinstallieren).
RUN mkdir -p /root/.claude && \
    printf '{"projects":{"/workspace":{"hasTrustDialogAccepted":true}}}\n' \
        > /root/.claude.json && \
    printf '{"permissions":{"allow":["Bash(*)","Read(*)","Write(*)","Edit(*)","WebFetch(*)","WebSearch(*)","mcp__*"],"deny":[]}}\n' \
        > /root/.claude/settings.json

WORKDIR /workspace
