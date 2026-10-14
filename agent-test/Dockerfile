# syntax=docker/dockerfile:1
# Agent image for the agent-readiness harness (docs/TDD.md section 6, rule A2).
# Debian bookworm with Node 22, Python 3.11 + pip + venv, git, curl, and the pinned Gemini CLI.
# Nothing Qrypt-specific is installed. The entrypoint trusts the run's proxy CA, then drops
# root before running the agent.
#
# Base: node:22.23.2-bookworm, pinned by index digest (image created 2026-09-18). Build:
#   docker build -t qrypt-agent-test:gemini-0.62.0 agent-test/
FROM node:22.23.2-bookworm@sha256:dd5847a04b0deee391fa145f1f4c6d214196668b6bcc7988ebed67249f226844

ARG GEMINI_CLI_VERSION=0.62.0

# python3 is 3.11.2 on bookworm. python-is-python3 gives `python` as well as `python3`.
# util-linux provides setpriv, used by the entrypoint to drop root.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git python3 python3-pip python3-venv python-is-python3 util-linux \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
 && npm cache clean --force \
 && gemini --version

# An unprivileged home for whatever uid the harness passes in (QH_UID/QH_GID), and the empty
# workdir. The CLI's auto-update and usage statistics are off so a run does not depend on them.
RUN mkdir -p /work /home/agent/.gemini \
 && useradd --no-create-home --uid 1001 --home-dir /home/agent --shell /bin/bash agent
# Built-in subagents (codebase_investigator, browser_agent, ...) ignore -m and call hard-coded
# preview models that have no free-tier quota; with them on, every run ended in Gemini 429s.
# Disabling them keeps every model call on the pinned model; the main agent keeps its web-fetch,
# file and shell tools.
COPY <<'JSON' /home/agent/.gemini/settings.json
{"general": {"enableAutoUpdate": false, "enableAutoUpdateNotification": false},
 "privacy": {"usageStatisticsEnabled": false},
 "experimental": {"enableAgents": false}}
JSON
RUN chmod -R a+rwX /work /home/agent

# Trust the mitmproxy CA mounted at /ca (if any), then become QH_UID:QH_GID and exec the command.
COPY --chmod=0755 <<'SH' /usr/local/bin/entry.sh
#!/bin/sh
set -eu
CA=/ca/mitmproxy-ca-cert.pem
if [ -f "$CA" ]; then
  cp "$CA" /usr/local/share/ca-certificates/mitmproxy-ca.crt
  update-ca-certificates >/dev/null 2>&1
fi
export HOME=/home/agent
exec setpriv --reuid="${QH_UID:-1001}" --regid="${QH_GID:-1001}" --clear-groups "$@"
SH

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entry.sh"]
