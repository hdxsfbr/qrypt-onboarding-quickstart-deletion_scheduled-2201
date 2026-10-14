# Qrypt onboarding quickstart

Node.js and Python quickstart snippets that generate an AES-256 key from Qrypt quantum entropy.

## Grader quick-start

1. Get an Entropy-as-a-Service key at [portal.qrypt.com](https://portal.qrypt.com).
2. Copy the example environment file and put your key in it:

   ```sh
   cp .env.example .env
   # edit .env: QRYPT_API_KEY=<your key>
   ```

3. Run a snippet.

   **Node.js** (20.12 or later). Node reads `.env` from the current directory, so run it from the folder that contains `.env`:

   ```sh
   node quickstart/node/quickstart.mjs
   ```

   **Python** (3.10 or later):

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r quickstart/python/requirements.txt
   python quickstart/python/quickstart.py
   ```

   python-dotenv finds `.env` by searching upward from the script's folder, so keep `.env` in the repo root or next to the script.

Expected output, for either language:

```
✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK).
```

## Running the tests locally

The tests run both snippets against a local mock of the Qrypt API. They need no key and make no network calls.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Node.js 20.12 or later must be on your `PATH` for the Node tests. Use `pytest -m python` or `pytest -m node` to run one language.

### Same checks as CI, before every push

`scripts/ci_checks.sh` is the single definition of the CI checks: every GitLab job calls it. Enable the pre-push hook once per clone, and a push that would fail CI fails locally first (about 10 seconds; the secret scan needs `gitleaks` or Docker):

```sh
git config core.hooksPath .githooks
```

Run it by hand with `scripts/ci_checks.sh local`, or run `scripts/ci_checks.sh matrix` to repeat the CI test matrix (Python 3.10/3.13, Node 20.12/22/24) in the exact Docker images. Skip the hook for one push with `git push --no-verify`.

## Repository layout

```
quickstart/node/quickstart.mjs         Node.js snippet
quickstart/python/quickstart.py        Python snippet
quickstart/python/requirements.txt     Python dependencies, exact pins
mock/server.py                         mock of the Qrypt entropy API
tests/                                 pytest suites for the snippets and the mock
scripts/                               helper scripts (loc.py counts snippet lines)
docs/                                  project documents
.gitlab-ci.yml                         CI: lint, tests, secrets scan, Pages
.env.example                           template for your API key
```

## Deviations from the brief

- The repository is hosted on Gauntlet's GitLab (labs.gauntletai.com) rather than GitHub, and CI is GitLab CI.
