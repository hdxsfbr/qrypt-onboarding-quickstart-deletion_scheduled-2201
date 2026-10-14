+++
title = "Troubleshooting"
+++

Every error the quickstart script prints ends with a `Fix:` link to a section on this page. Each section follows the same pattern: what you see, the likely cause, and the fix.

| If you see | Go to |
|---|---|
| `QRYPT_API_KEY is not set` | [No key](#no-key) |
| `Node.js 20.12+ required` | [Runtime version](#runtime) |
| `Could not reach Qrypt` | [Network](#network) |
| `HTTP 400` | [400](#400) |
| `HTTP 401` | [401](#401) |
| `HTTP 403` | [403](#403), and [403 or 429](#403-vs-429) |
| `HTTP 429` | [429](#429), and [403 or 429](#403-vs-429) |
| `HTTP 500` or `HTTP 503` | [500](#500), [503](#503) |
| `HTTP 530` | [530](#530) |
| `HTTP 302` | [302](#302) |
| `AES-GCM round trip failed` | [Round trip](#round-trip) |
| A stack trace instead of a `✘` line | [Stack trace](#malformed) |
| Any other message | [Other](#other) |

## QRYPT_API_KEY is not set {#no-key}

**You see:** `✘ QRYPT_API_KEY is not set. Add QRYPT_API_KEY=<your key> to .env.`

**Likely cause:** The script found no key. The usual reasons:
- You ran it from a folder that has no `.env` file. See [.env missing, or wrong folder](#env-file).
- The file is not named exactly `.env`. Some editors on Windows save it as `.env.txt`.
- The line is empty, for example `QRYPT_API_KEY=` with nothing after the equals sign.

**Fix:** Go to the folder that holds the script. List the files including hidden ones with `ls -a` (macOS and Linux) or `dir -Force` (PowerShell). Check that `.env` is there and has one line, `QRYPT_API_KEY=` followed by your key. Then run the script again.

## Node.js 20.12+ required {#runtime}

**You see:** `✘ Node.js 20.12+ required (found v18.19.0).` The version in the message is the one you are running.

**Likely cause:** The Node.js script loads `.env` with a built-in feature that older Node.js versions don't have.

**Fix:** Run `node --version`. Install Node.js 20.12 or newer, then run the script again. Or use the Python version. See [Wrong Node.js or Python version](#versions).

## Could not reach Qrypt {#network}

**You see:** `✘ Could not reach Qrypt (ENOTFOUND).` The word in brackets is the detail. In Node.js it is an error code such as `ENOTFOUND`, `ECONNREFUSED` or `UND_ERR_CONNECT_TIMEOUT`. In Python it is an exception name such as `ConnectionError`, `ConnectTimeout` or `SSLError`.

**Likely cause:** The request never got a response from Qrypt. The usual reasons:
- No internet connection, or a DNS problem.
- A VPN or firewall that blocks `api-eus.qrypt.com`.
- A corporate proxy, or a network that inspects TLS. Certificate errors such as `SELF_SIGNED_CERT_IN_CHAIN` (Node.js) or `SSLError` (Python) point here.
- A timeout. The Python script waits 15 seconds.

**Fix:** First check that you can reach Qrypt at all. This command sends no key, and a response of `401` means the request reached Qrypt:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' 'https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1'
```

If that fails too, fix your network, VPN or firewall. If it prints `401` but the script still fails, you are probably behind a proxy or TLS inspection. See [Corporate proxy or TLS inspection](#proxy).

## HTTP 400 {#400}

**You see:** `✘ Qrypt returned HTTP 400: unexpected response.`

**Likely cause:** Qrypt rejected the request as malformed. Qrypt's docs say to check the URL format and the parameters. The usual parameter problem is `size`. It must be a whole number from 1 to 512, and the live API answers `{"error":"'size' must be an integer between 1 and 512"}`. The quickstart script asks for `size=1`, so a 400 means the URL in the script was changed.

**Fix:** Download the script again and run it without edits. If you wrote your own code, set `size` to a whole number from 1 to 512. See [`size` is KiB, not bytes](#size-kib).

## HTTP 401 {#401}

**You see:** `✘ Qrypt returned HTTP 401: Authorization header missing or malformed.`

**Likely cause:** The live API sends a 401 when the `Authorization` header is missing or malformed. Its body is `{"error":"Missing or malformed Authorization header (expected 'Bearer <token>')"}`. Observed on 2026-10-13, this includes an empty key. Qrypt's FAQ also says an expired access token returns 401. We have not tested an expired key.

A wrong key does not return 401. It returns 403. See [HTTP 403](#403).

**Fix:**
1. Download the script again and run it without edits.
2. Check the key in `.env`. It must be on one line, `QRYPT_API_KEY=` followed by the key.
3. If you use Python, check for a [`~/.netrc` entry](#netrc).
4. If the key may have expired, create a new Entropy-as-a-Service key in the portal and try that.

## HTTP 403: key rejected, or quota used up {#403}

**You see:** `✘ Qrypt returned HTTP 403: key rejected, or monthly entropy quota used up.`

**What the live API does:** Qrypt's docs say a 401 means an invalid or expired token and a 403 means the entropy limit was reached. The live API differs for a wrong key. On 2026-10-13, a well-formed but wrong key got **403** with `{"error":"Forbidden or entropy quota reached"}`, not 401. A 401 came back only when the `Authorization` header was missing or malformed. So a 403 means one of two things: the key is wrong, or the quota is used up.

**Likely cause:**
- The key is wrong: a typo, a cut-off paste, or a key from the wrong product. The key must be for **Entropy-as-a-Service**.
- Your account used up its monthly entropy quota.
- An older `QRYPT_API_KEY` is set in your shell. A variable that is already set takes priority over the `.env` file.
- Less often, Cloudflare blocked the request before it reached Qrypt's API. The body then has a `title` of "Error 1010: Access denied" instead of Qrypt's `{"error": …}`. You can only see it in your own code. See [Cloudflare error 1010](#cloudflare-1010).

**How to tell a bad key from a used-up quota:**
1. Did this key ever work? If it never did, suspect the key. If it worked and then stopped, suspect the quota.
2. Ask Qrypt support (support@qrypt.com) whether your account has reached its monthly quota.
3. Create a new Entropy-as-a-Service key and run the script once. If the new key also gets a 403, the quota is the likely cause.

**Fix:**
- Wrong key: copy the key again. The portal shows a key only once, so if you didn't save it, create a new one. Check whether an old key is set in your shell. In `sh`, `[ -n "$QRYPT_API_KEY" ] && echo set` prints `set` if it is. Remove it with `unset QRYPT_API_KEY`. In PowerShell, use `Remove-Item Env:QRYPT_API_KEY`.
- Quota used up: Qrypt's FAQ says the quota resets monthly, on the day of the month you opened the account. To get a higher quota, contact Qrypt support at support@qrypt.com.

Don't re-run the script in a loop while you work this out. See [Don't loop requests](#rate-limit).

## HTTP 429: too many requests {#429}

**You see:** `✘ Qrypt returned HTTP 429: over 30 requests in 10 seconds; wait, then run once.`

**Likely cause:** Qrypt allows 30 requests per 10 seconds for each access token. Something sent more. It is often a loop, a retry in your own code, or several scripts and tools sharing one key.

**Fix:** Stop whatever is sending requests. Wait at least 10 seconds. Then run the script once. See [Don't loop requests](#rate-limit) and [403 or 429](#403-vs-429).

## HTTP 500: internal server error {#500}

**You see:** `✘ Qrypt returned HTTP 500: try again shortly.`

**Likely cause:** Qrypt's service hit an internal error. Your request and your key are probably fine.

**Fix:** Wait a minute and run the script once more. If the 500 continues, contact Qrypt support at support@qrypt.com and tell them the time it happened.

## HTTP 503: not enough supply {#503}

**You see:** `✘ Qrypt returned HTTP 503: try again shortly.`

**Likely cause:** Qrypt's docs call this "not enough supply". Its supply of entropy is temporarily too low to fill the request.

**Fix:** Wait and run the script once more. Don't loop. If it continues for a long time, contact Qrypt support at support@qrypt.com.

## HTTP 530 or Cloudflare error 1033 {#530}

**You see:** `✘ Qrypt returned HTTP 530: try again shortly.` The response body names Cloudflare error 1033 ("Cloudflare Tunnel error").

**Likely cause:** A mistyped path. On 2026-10-14, a request to `https://api-eus.qrypt.com/api/v1/entropy` returned 530, not 404. That is the path from the portal's sample code, which belongs to the other host. The script says "try again shortly" for every 5xx, but waiting will not fix a wrong path.

**Fix:** Use exactly `https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1`. Download the script again if you edited the URL. See [The portal showed you different sample code?](/quickstart/#get-a-key)

## HTTP 302: redirect {#302}

**You see:** `✘ Qrypt returned HTTP 302: unexpected response.` The same applies to other 3xx codes.

**Likely cause:** Qrypt's documented responses include no redirects. A redirect usually means something else answered: a captive portal on public Wi-Fi, a proxy or filter, or an edited URL. The script does not follow redirects.

**Fix:** Download the script again and run it without edits. Try another network. If you are on a company network, see [Corporate proxy or TLS inspection](#proxy).

## AES-GCM round trip failed {#round-trip}

**You see:** `✘ AES-GCM round trip failed.`

**Likely cause:** The script encrypts a short message, decrypts it, and compares the result to the original. This message means the decrypted text was different. The unedited script should not do this. It usually means the script was edited, for example if the encrypt and decrypt steps used different keys or nonces.

**Fix:** Download the script again and run it without edits. If you still see this message, run it once more. If it persists, report it to Qrypt support with the Node.js or Python version you use.

## Any other message {#other}

**You see:** `✘ Qrypt returned HTTP <number>: unexpected response.`, or a `Fix:` link that ends in a number with no section on this page, such as `#404`.

**Likely cause:** Qrypt documents these status codes: 200, 400, 401, 403, 429, 500 and 503. Any other code usually comes from something between you and Qrypt, or from an edited URL. For other 5xx codes the script says "try again shortly".

**Fix:**
1. Download the script again and run it without edits.
2. Try another network.
3. If the code is a 5xx, wait a minute and run the script once more.
4. If it persists, send Qrypt support the status code, the time, and the script you used. Never include your key.

## Cloudflare error 1010 with Python urllib {#cloudflare-1010}

**You see:** HTTP 403 with a Cloudflare error body whose `title` is "Error 1010: Access denied", instead of Qrypt's `{"error": …}`. It looks like a bad key, but it happens before the key is checked.

**Likely cause:** Cloudflare, in front of Qrypt, blocks Python's built-in `urllib` client. Its default `User-Agent` is `Python-urllib/3.x`. Checked on 2026-10-14 against both `api-eus.qrypt.com` and `eaas-aws.qrypt.com`. Requests from `requests`, `httpx`, `aiohttp`, Node.js `fetch`, curl and Go were not blocked. Neither were requests with a custom `User-Agent`. The quickstart script uses `requests` and sets its own `User-Agent`, so it is not affected.

**Fix:** If you write your own Python code, use `requests`. Or set your own `User-Agent` header on the `urllib` request, for example `my-app/1.0`.

## 403 or 429: which one is it? {#403-vs-429}

Both mean "not now", but they have different causes and different fixes.

| | HTTP 403 | HTTP 429 |
|---|---|---|
| The script says | `key rejected, or monthly entropy quota used up` | `over 30 requests in 10 seconds; wait, then run once` |
| What it means | The key is wrong, or the monthly quota is used up | The key sent more than 30 requests in 10 seconds |
| How long it lasts | Until you use a correct key, or until the quota resets on your account's monthly anniversary day | About 10 seconds |
| Does waiting a few seconds help? | No | Yes |
| What to do | Check the key and the quota. See [HTTP 403](#403) | Stop any loop, wait 10 seconds, run once. See [HTTP 429](#429) |

## `size` is KiB, not bytes {#size-kib}

**You see:** A request returns far more data than you wanted, uses up your quota quickly, or fails with a 400.

**Likely cause:** The `size` parameter of the `api-eus` API counts KiB (1,024 bytes), not bytes. `size=1` returns 1,024 bytes. `size=32` returns 32 KiB, which is 32,768 bytes. The allowed range is 1 to 512, and the default is 1. A value outside that range returns a 400.

**Fix:** For a 256-bit key, use `size=1` and take the first 32 bytes. The portal's sample code uses the other API, where sizes are in bytes. See [The portal showed you different sample code?](/quickstart/#get-a-key)

## The response is base64 {#base64}

**You see:** Your cipher library rejects the key length, or your key looks like letters and digits ending in `=`.

**Likely cause:** The `random` field is a list of base64 strings. Each string decodes to 1,024 bytes. One block, as text, is 1,368 characters long. If you use that text as the key, the key has the wrong length and wrong content.

**Fix:** Decode the string with standard base64 first. Do not use the URL-safe variant. Take the first 32 bytes of the decoded bytes as the AES-256 key.

## Don't loop requests {#rate-limit}

**You see:** A 429, or a 403 after a while.

**Likely cause:** Qrypt allows 30 requests per 10 seconds for each access token. Everything that uses the same key shares that limit. Every request also uses part of your monthly entropy quota. A loop, a retry in your own code, or a coding tool that keeps re-running the script can hit both limits quickly.

**Fix:** Run the script once. If it fails, read the message and use the matching section on this page before you run it again. Don't add automatic retries.

## .env missing, or wrong folder {#env-file}

**You see:** `QRYPT_API_KEY is not set`, even though you saved the key.

**Likely cause:** The two scripts look for `.env` in different places.
- **Node.js** reads `.env` from the folder you run the command in.
- **Python** searches for `.env` starting in the script's folder, then in each parent folder. A `.env` in a parent folder can be used by mistake if yours is missing.

**Fix:** Run the script from the folder that holds `.env`. The file name must be exactly `.env`. A variable that is already set in your shell takes priority over the file.

## Wrong Node.js or Python version {#versions}

**You see:** `Node.js 20.12+ required`, or Python errors as soon as the script starts.

**Likely cause:** The Node.js script needs Node.js 20.12 or newer. The Python script needs Python 3.10 or newer.

**Fix:** Check your versions with `node --version` and `python --version`. On macOS and Linux the Python command may be `python3`. On Windows it may be `py`. Install a current version from [nodejs.org](https://nodejs.org) or [python.org](https://www.python.org). Then, for Python, create a new virtual environment and run `pip install -r requirements.txt` again.

## Corporate proxy or TLS inspection {#proxy}

**You see:** `Could not reach Qrypt` with a certificate error, or with a timeout when direct internet access is blocked.

**Likely cause:** Your network sends traffic through a proxy. It may also inspect encrypted traffic with its own certificate authority (CA), which your tools don't trust by default.

**Fix:** Ask your IT team for the proxy address and the CA certificate file in PEM format. Then set these variables in the terminal where you run the script.

For a proxy, set `HTTPS_PROXY`. Python's `requests` reads it. Node.js's built-in `fetch` ignores it unless you also set `NODE_USE_ENV_PROXY=1`. Older Node.js versions may not support that setting. In that case, use the Python version.

```sh
export HTTPS_PROXY=http://proxy.example.com:8080
```

For a custom CA with Node.js:

```sh
export NODE_EXTRA_CA_CERTS=/path/ca.pem
```

For a custom CA with Python:

```sh
export REQUESTS_CA_BUNDLE=/path/ca.pem
```

In PowerShell, set a variable with `$env:NAME = "value"`, for example `$env:NODE_EXTRA_CA_CERTS = "C:\path\ca.pem"`.

## A ~/.netrc entry overrides the key {#netrc}

**You see:** A 401 or 403 from Python even though the key is right.

**Likely cause:** The Python script uses `requests`. If your `~/.netrc` file has an entry for `api-eus.qrypt.com`, `requests` can use it in place of the `Authorization` header the script sets.

**Fix:** Remove the `api-eus.qrypt.com` entry from `~/.netrc`, or give it a different `machine` name. Then run the script again.

## A stack trace instead of a message {#malformed}

**You see:** A long error with file names and line numbers, and no `✘` line. Typical endings are a JSON parse error, `KeyError: 'random'`, or a `TypeError` about `undefined`. A connection that closes in the middle of the response can produce one too.

**Likely cause:** The response was not the JSON the script expects, or it was cut off. The script does not handle these cases, so it shows the error as is. This is rare.

**Fix:** Wait a minute and run the script once more. If it happens again, report it to Qrypt support at support@qrypt.com with the last lines of the error and the time. Check first that the text you send does not contain your key.
