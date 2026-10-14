// Qrypt quickstart: generate an AES-256 key from Qrypt quantum entropy.
// Requires Node.js 20.12+ and no packages. Put QRYPT_API_KEY in .env, then run from that
// folder: node quickstart.mjs
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

const HELP = "https://qrypt-quickstart.pages.dev/quickstart/troubleshooting/";
const HINTS = { 401: "key expired or malformed; create a new one at portal.qrypt.com",
  403: "key rejected, or monthly entropy quota used up",
  429: "rate limit reached; wait about 15 seconds, then run once" };
const fail = (msg, id) => { console.error(`✘ ${msg}\n  Fix: ${HELP}#${id}`); process.exit(1); };

if (!process.loadEnvFile) fail(`Node.js 20.12+ required (found ${process.version}).`, "runtime");
try { process.loadEnvFile(); } catch { /* no .env file: the next check explains */ }
const token = process.env.QRYPT_API_KEY?.trim();
if (!token) fail("Set QRYPT_API_KEY=<key> in .env. Get a key at portal.qrypt.com.", "no-key");

const url = process.env.QRYPT_API_URL ?? "https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1";
const headers = { Accept: "application/json", Authorization: `Bearer ${token}`,
  "User-Agent": "qrypt-quickstart-node/1.0" };
const res = await fetch(url, { headers, redirect: "manual" })
  .catch((e) => fail(`Could not reach Qrypt (${e.cause?.code ?? e.cause?.message ?? e.name}).`,
    "network"));
const hint = HINTS[res.status] ?? (res.status >= 500 ? "try again shortly" : "unexpected response");
if (!res.ok) fail(`Qrypt returned HTTP ${res.status}: ${hint}.`, res.status);

// The first 32 bytes (256 bits) of the 1 KiB block become the key. Never reuse the rest.
const aesKey = Buffer.from((await res.json()).random[0], "base64").subarray(0, 32);
const iv = randomBytes(12), message = "Hello from Qrypt quantum entropy";
const cipher = createCipheriv("aes-256-gcm", aesKey, iv);
const ciphertext = Buffer.concat([cipher.update(message, "utf8"), cipher.final()]);
const decipher = createDecipheriv("aes-256-gcm", aesKey, iv).setAuthTag(cipher.getAuthTag());
const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString();
if (plaintext !== message) fail("AES-GCM round trip failed.", "round-trip");
console.log("✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK).");
