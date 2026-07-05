# steakweb — OMNIDAT / omniDECT fork

A fork of [Shadytel/steakweb](https://github.com/Shadytel/steakweb) — the
"Shadytel Telephony Enrollment & Activation Kit" — adapted for **OMNIDAT** and its
**omniDECT** cordless service at **steak.omnidat.cc**.

## What changed vs upstream
- **`config.json.omnidat.example`** — deployment config for `steak.omnidat.cc`.
  OMNIDAT federates identity via OmniAuth Passkey / ForgeGraph OIDC (or an OMNIDAT
  SAML IdP), not `identity.shady.tel`.
- **`prov_to_dect` route** (`steakweb.py`) — provisions an extension onto the
  **DECT switch (20)**, binding it to a cordless handset by **IPUI**. Parallel to
  upstream `prov_to_sip` (switch 11). The endpoint is the shadydect FP
  (`dect/fp/registry.py`).
- **`schema.omnidat.sql`** — adds `ipui` + `handset_id` columns the DECT flow
  needs; safe to apply to an existing steakweb Postgres.

## Two runtimes, one product
This upstream **Python / aiohttp / SAML / Postgres** app is the canonical
full-infra deployment (identity + activation-server socket + Postgres).

There is also an **edge reimplementation** that is **already live at
steak.omnidat.cc** — a Cloudflare Worker + D1 that models the same
`registered_extensions` table and routes, deployable without Postgres/SAML. It
lives in the shadydect repo at `web/steak/`. Use whichever fits the deployment:
- **edge worker** — zero-infra, live now, signed-cookie sessions;
- **this app** — when you want the real IdP federation + shared OMNIDAT Postgres
  + the activation-server socket that resyncs switches.

## Deploy (this app)
1. `pip install -r requirements.txt`
2. `cp config.json.omnidat.example config.json` and fill secrets (Postgres DSN,
   cookiekey, IdP metadata, activation socket).
3. Apply `schema.omnidat.sql` to the OMNIDAT Postgres.
4. Run behind a reverse proxy terminating TLS for `steak.omnidat.cc` (OMNIDAT node
   via `forge` / systemd, per the omnidat infra).
