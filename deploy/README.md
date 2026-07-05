# Deploying steak.omnidat.cc (SAML) — OMNIDAT / omniDECT

The SAML-authenticated portal is this Python/aiohttp app (upstream steakweb).
Unlike the zero-infra edge worker (`shadydect:web/steak/`), it needs three things:
a Postgres DB, a SAML IdP, and a node to run on.

## 1. Postgres — DONE (shares OMNIDAT's database)
steak uses **OMNIDAT's shared Postgres** (`fryos_production` on hetzner-master),
in its **own `steak` schema** so it never touches omnidat/fryos tables. The schema
is already applied:
```bash
DSN=$(~/.forgegraph/bin/fg db url --app fryos)   # the shared DSN
psql "$DSN" -f schema.postgres.sql               # creates schema steak + registered_extensions
```
In `config.json`: set `dbconnstr` to that DSN and `db_schema` to `steak`
(the app sets `search_path=steak`, so upstream's unqualified queries resolve
there). No `forge db create` — the DB is shared.

## 2. SAML IdP  (the piece that needs the IdP admin)
This app is a SAML **Service Provider**. Register it in the OMNIDAT Authentik
(or reuse identity.shady.tel) with:
- **SP entityId:** `https://steak.omnidat.cc/saml/service-provider-metadata`
- **ACS URL:** `https://steak.omnidat.cc/saml/acs` (HTTP-POST binding)
- **NameID:** unspecified; sign messages + assertions
- Emit a group claim so `check_auth_isadmin` can see `Extension Admins`.

Copy the IdP metadata URL into `config.json` (`idp_metadata`). No app code
changes — python3-saml handles the XML signature validation (which is why SAML
lives here and not in the Worker).

## 3. Node + TLS + DNS
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.json.omnidat.example config.json   # fill secrets
sudo cp deploy/steak-omnidat.service /etc/systemd/system/ && sudo systemctl enable --now steak-omnidat
```
Terminate TLS for `steak.omnidat.cc` at a reverse proxy in front (Caddy/nginx),
then point the `steak.omnidat.cc` DNS record at the node.

> **Cutover note:** `steak.omnidat.cc` currently serves the edge worker
> (Cloudflare custom domain, signed-cookie sessions). Moving the hostname here
> swaps that record for the node. Keep the worker as staging, or retire it.

## 4. Wire the node
Set on the shadydect/omniDECT daemon so `*77` provisions against this portal:
```
SHADYDECT_STEAK_BASE_URL=https://steak.omnidat.cc
SHADYDECT_STEAK_API_TOKEN=<same as config.json api_token>
```
`/api/provision`, `/api/extension/{extn}`, `/api/registry` are token-authed and
identical to the edge worker, so the daemon (`daemon/steak.py`) works unchanged.
