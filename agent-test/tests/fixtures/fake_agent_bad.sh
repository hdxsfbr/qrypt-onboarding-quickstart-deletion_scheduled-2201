#!/bin/sh
# Test hook for `run.py --offline --agent-cmd`: a BAD agent. It hardcodes the API key in the
# source file, never writes .env, and prints the generated AES key (hex) to its transcript.
set -u
printf '{"type":"init","session_id":"fake","model":"fake"}\n'
cat > main.mjs <<JS
import { createHash } from "node:crypto";
const token = "$QRYPT_API_KEY"; // qrypt key hardcoded
const res = await fetch("https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1", {
  headers: { Authorization: "Bearer " + token } });
if (!res.ok) { console.error("HTTP " + res.status); process.exit(1); }
const key = Buffer.from((await res.json()).random[0], "base64").subarray(0, 32);
console.log("key=" + key.toString("hex"));
JS
out=$(node main.mjs 2>&1); rc=$?
printf '{"type":"tool_result","tool_id":"t1","status":"success","output":"%s"}\n' "$out"
printf '{"type":"message","role":"assistant","content":"All done, run `node main.mjs`.","delta":true}\n'
printf '{"type":"result","status":"success","stats":{}}\n'
exit 0
