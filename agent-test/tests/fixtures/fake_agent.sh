#!/bin/sh
# Test hook for `run.py --offline --agent-cmd`: stands in for Gemini CLI. Behaves like a good
# agent: checks it has no direct network route, writes .env from the container environment,
# writes a small Node program that calls the Qrypt entropy endpoint (through the proxy, which
# in --offline mode serves the repo mock), runs it, and ends with the command to run it.
# Emits Gemini-style stream-json events on stdout.
set -u
say() { printf '{"type":"message","role":"assistant","content":"%s","delta":true}\n' "$1"; }
use() { printf '{"type":"tool_use","tool_name":"run_shell_command","tool_id":"t1","parameters":{"command":"%s"}}\n' "$1"; }
res() { printf '{"type":"tool_result","tool_id":"t1","status":"success","output":"%s"}\n' "$1"; }

printf '{"type":"init","session_id":"fake","model":"fake"}\n'
say "Checking the network path."
use "curl --noproxy '*' https://example.com"
if curl --noproxy '*' -sS -m 6 -o /dev/null https://example.com 2>/dev/null; then
  res "DIRECT EGRESS WORKS (harness isolation broken)"; echo "isolation broken" >&2
else
  res "no direct egress (expected)"
fi

( umask 077; printf 'QRYPT_API_KEY=%s\n' "$QRYPT_API_KEY" > .env )
cat > index.mjs <<'JS'
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
process.loadEnvFile();
const token = process.env.QRYPT_API_KEY?.trim();
if (!token) { console.error("QRYPT_API_KEY is not set"); process.exit(1); }
const res = await fetch("https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1", {
  headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
if (!res.ok) { console.error(`Qrypt returned HTTP ${res.status}`); process.exit(1); }
const key = Buffer.from((await res.json()).random[0], "base64").subarray(0, 32);
const iv = randomBytes(12), msg = "hello";
const c = createCipheriv("aes-256-gcm", key, iv);
const ct = Buffer.concat([c.update(msg, "utf8"), c.final()]);
const d = createDecipheriv("aes-256-gcm", key, iv).setAuthTag(c.getAuthTag());
if (Buffer.concat([d.update(ct), d.final()]).toString() !== msg) process.exit(1);
console.log("Generated a 256-bit AES key from Qrypt entropy (round trip OK).");
JS
use "node index.mjs"
out=$(node index.mjs 2>&1) ; rc=$?
res "exit $rc: $out"
say "Done. Run it with: node index.mjs"
printf '{"type":"result","status":"success","stats":{}}\n'
exit $rc
