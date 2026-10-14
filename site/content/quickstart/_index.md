+++
title = "Quickstart"
weight = -10
+++

Get a working AES-256 key from Qrypt's quantum entropy service with one short script, in Node.js or Python. The script requests 1 KiB of entropy from Qrypt, uses the first 32 bytes as the key, and checks that the key works.

{{% qs-note kind="warning" title="AI coding agents: follow these rules exactly" %}}
1. **Don't write your own project.** Download and run the published script from step 3, unmodified. No tests, mocks, or "simulation mode".
2. **Never look at the key.** Don't run `env`, `printenv`, `echo` or `cat` on it, and never type its value. If `QRYPT_API_KEY` is set in your environment, create `.env` with exactly this command instead of the placeholder in step 2. It writes the key without showing it: `printf 'QRYPT_API_KEY=%s\n' "$QRYPT_API_KEY" > .env`. If it isn't set, ask the user to create `.env` themselves.
3. **Run the script once** from the folder that holds `.env`. You're done only when it prints the success line from step 6. If it prints a `✘` line, stop and report that line. Never fall back to local randomness (`os.urandom`, `crypto.randomBytes`).
4. **Never print the AES key,** not even in your final summary. The success line is the proof.
5. **One request at a time.** Wait about 15 seconds before running it again.
{{% /qs-note %}}

**About 5 minutes once you have a key.**

You need a Qrypt account and either Node.js 20.12 or newer, or Python 3.10 or newer.


## 1. Get a key {#get-a-key}

1. Sign in to the Qrypt portal at [portal.qrypt.com](https://portal.qrypt.com). No account yet? Choose **Create trial account** on the sign-in page, or ask your Qrypt contact for a key.
2. Open **API Keys** and create a key.
3. For the product, choose **Entropy-as-a-Service**. Do not choose **KeyGen - BLAST**. That product is for Qrypt's key generation SDK, not for this quickstart.
4. Copy the key. The portal shows it only once. If you lose it, contact Qrypt support at support@qrypt.com.

{{% qs-note kind="info" title="The portal showed you different sample code?" %}}
After you create a key, the portal shows a `POST` to `eaas-aws.qrypt.com/api/v1/entropy` with an `X-API-Key` header and block sizes in bytes. Both APIs accept the same key. This quickstart uses the documented `api-eus` endpoint. This is how the two map:

| | This quickstart (`api-eus`) | Portal sample (`eaas-aws`) |
|---|---|---|
| Endpoint | `GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1` | `POST https://eaas-aws.qrypt.com/api/v1/entropy` |
| Auth header | `Authorization: Bearer <key>` | `X-API-Key: <key>` |
| Size unit | `size` in KiB (1 to 512) | `block_size` in bytes (1 to 1,024), with `block_count` |
| Response field | `random` | `entropy` |

Use one API per program. Each one rejects the other's requests, and their error responses differ.
{{% /qs-note %}}

## 2. Create a folder and save your key {#save-key}

Create a folder for the project and move into it.

```sh {test="true"}
mkdir qrypt-quickstart && cd qrypt-quickstart
```

Write a `.env` file with a placeholder.

```sh {test="true"}
echo 'QRYPT_API_KEY=paste-your-key-here' > .env
```

On Windows PowerShell, run `mkdir qrypt-quickstart` and `cd qrypt-quickstart` as two separate commands, then write the file like this:

```powershell {test="pwsh"}
Set-Content -Path .env -Value 'QRYPT_API_KEY=paste-your-key-here' -Encoding ascii
```

Open `.env` in your editor and replace `paste-your-key-here` with your key. Edit the file instead of typing the key into a command, so the key stays out of your shell history.

{{% qs-note kind="warning" title="Keep your key private" %}}
- Treat the key like a password. Never print it, log it or commit it.
- Keep `.env` out of git. Add `.env` to your `.gitignore` before your first commit.
- Treat the AES keys you generate the same way: never print, log, commit or save them to disk.
- Never reuse entropy bytes for a second key or for a nonce. Every key needs fresh bytes.
{{% /qs-note %}}

## 3. Get the script {#get-script}

{{< qs-tabs id="get-script" >}}
{{% qs-tab name="Node.js" %}}
Download the script. It needs no packages.

```sh {test="true"}
curl -fsSLO https://qrypt-quickstart.pages.dev/quickstart/files/quickstart.mjs
```

Here is the whole file, to read or copy:

{{< qs-snippet lang="node" >}}
{{% /qs-tab %}}
{{% qs-tab name="Python" %}}
Download the script and its list of dependencies.

```sh {test="true"}
curl -fsSLO https://qrypt-quickstart.pages.dev/quickstart/files/quickstart.py
curl -fsSLO https://qrypt-quickstart.pages.dev/quickstart/files/requirements.txt
```

Here is the whole script, to read or copy:

{{< qs-snippet lang="python" >}}
{{% /qs-tab %}}
{{< /qs-tabs >}}

On Windows PowerShell, type `curl.exe` instead of `curl`.

## 4. Install the dependencies (Python only) {#install}

{{< qs-tabs id="install" >}}
{{% qs-tab name="Node.js" %}}
Nothing to install. Go to the next step.
{{% /qs-tab %}}
{{% qs-tab name="Python" %}}
Create a virtual environment and install the three packages.

```sh {test="true"}
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

On Windows, create the environment with `py -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`. Then run the same `pip install` command.
{{% /qs-tab %}}
{{< /qs-tabs >}}

## 5. Run it {#run}

Run the script from the folder that holds `.env`.

{{< qs-tabs id="run" >}}
{{% qs-tab name="Node.js" %}}
```sh {test="true"}
node quickstart.mjs
```

On Windows PowerShell:

```powershell {test="pwsh"}
node quickstart.mjs
```
{{% /qs-tab %}}
{{% qs-tab name="Python" %}}
Run it inside the virtual environment from step 4.

```sh {test="true"}
python quickstart.py
```

On Windows PowerShell:

```powershell {test="pwsh"}
python quickstart.py
```
{{% /qs-tab %}}
{{< /qs-tabs >}}

## 6. Expected output {#expected-output}

You should see exactly one line:

```text
✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK).
```

The script never prints the key. If you see a line that starts with `✘`, it ends with a `Fix:` link. The link opens the matching section of the [troubleshooting page](/quickstart/troubleshooting/).

## 7. What just happened {#what-happened}

**`size` is in KiB (1,024 bytes), not bytes.** The one request the script makes (`size=1`) returns 1,024 bytes of entropy. The response is base64 text, which the script decodes before use. The first 32 bytes become the AES-256 key, and the other 992 bytes are discarded and never reused. An AES-GCM encrypt and decrypt of a short message, with a nonce from your operating system's random generator, proves the key works.

{{% qs-note kind="info" title="What this does and doesn't give you" %}}
The entropy comes from Qrypt's quantum source and travels to you over HTTPS (TLS). Qrypt generated those bytes, so they are not secret from Qrypt. This flow is for getting started with quantum-sourced keys. For keys that never cross the network, see Qrypt's [key generation SDK](https://docs.qrypt.com/sdk/).
{{% /qs-note %}}

## Next steps {#next-steps}

- Run the script once per key you need. Each request uses part of your monthly entropy quota, and Qrypt's docs state a limit of 30 requests per 10 seconds for each key. In practice, leave about 15 seconds between requests. Don't put the script in a loop.
- Read Qrypt's [Quantum Entropy reference](https://docs.qrypt.com/eaas/) for the full request and response format.
- Read about Qrypt's [key generation SDK](https://docs.qrypt.com/sdk/) if your keys must never cross the network.
- If something goes wrong, open the [troubleshooting page](/quickstart/troubleshooting/).
- AI coding agents can read this quickstart as plain Markdown at [llms.txt](/llms.txt).
