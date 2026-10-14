To: sean@qrypt.com
Subject: Developer quickstart project: 5 questions before we write the snippets

Hi Sean,

I'm working on the Developer Quickstart & Onboarding Funnel project. Before writing code I checked the brief against your docs and the live API, and found a few discrepancies. Your answers decide how the quickstart is built, so I wanted to ask up front.

1. **Which entropy API should the quickstart teach?** The brief and docs.qrypt.com describe `GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size={KiB}` with a Bearer token. The portal's sample code, shown right after creating a key, uses `POST https://eaas-aws.qrypt.com/api/v1/entropy` with `X-API-Key` and byte-sized blocks. Both accept the same key. The second one matches the On-Prem Appliance's OpenAPI spec (its `/api/v1/capabilities` endpoint responds too), but no docs page connects that spec to a cloud host or `X-API-Key` auth. Is `eaas-aws` the future and `api-eus` legacy? (`eaas-aws` also returned 429 after about 3 requests in 2 seconds. What is its rate limit?) My default is `api-eus`, as the brief specifies.

2. **What happens with an expired token?** The docs say 401 for "invalid or expired". Live, a wrong token returns 403 ("Forbidden or entropy quota reached"), and 401 only appears when the header is missing. Does an expired token return 401 or 403? Is there any way for a developer to tell "bad token" apart from "quota used up"?

3. **What do 429 and 503 look like?** Do they send `Retry-After`, and what is the body format? We won't trigger them deliberately against production, so our test mocks would otherwise be guesses.

4. **Can we use the raw bytes directly as a key?** Is it acceptable to use 32 raw bytes from the service directly as an AES-256 key, as the brief says? Or do you recommend passing them through a KDF first? The "Key Entropy Size" page budgets 40 bytes of entropy per 32-byte key.

5. **Sign-up link and quota.** The brief links `portal.qrypt.com/register`, which returns a 404. Should developers use `/trial` instead, and does a trial account include Entropy-as-a-Service? Also, what is the monthly allotment on my assessment key? I'm keeping total usage under the brief's 10 MB.

Thanks,
Andre Batista
