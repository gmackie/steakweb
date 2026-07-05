-- steak.omnidat.cc — Postgres schema for the SAML portal, in its OWN schema so it
-- shares OMNIDAT's database (fryos_production) without touching omnidat/fryos
-- tables. Apply with: psql "$DSN" -f schema.postgres.sql
--
-- Column set = upstream steakweb (extn/name/userid/auth_code/publish/switch/
-- provisioned) + OMNIDAT DECT binding (ipui/handset_id). switch: NULL
-- unprovisioned | 11 SIP (upstream) | 20 omniDECT DECT base.

CREATE SCHEMA IF NOT EXISTS steak;

CREATE TABLE IF NOT EXISTS steak.registered_extensions (
  extn        INTEGER PRIMARY KEY,
  name        TEXT        NOT NULL DEFAULT '',
  userid      TEXT        NOT NULL,          -- SAML NameID (see note below)
  auth_code   TEXT,
  publish     BOOLEAN     NOT NULL DEFAULT false,
  switch      INTEGER,
  provisioned BOOLEAN     NOT NULL DEFAULT false,
  ipui        TEXT,                          -- DECT International Portable User Identity (hex)
  handset_id  INTEGER,                       -- module handset slot (dect/fp/registry.py)
  created     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_regext_userid  ON steak.registered_extensions (userid);
CREATE INDEX IF NOT EXISTS idx_regext_publish ON steak.registered_extensions (publish);
CREATE UNIQUE INDEX IF NOT EXISTS idx_regext_ipui
  ON steak.registered_extensions (ipui) WHERE ipui IS NOT NULL;

-- NOTE: upstream steakweb casts session['uid'] to int in its queries (its
-- Authentik emits a numeric NameID). userid is TEXT here for SAML generality; if
-- the OMNIDAT IdP emits a numeric NameID this still holds it fine. Set the app's
-- search_path to `steak` (config / connection) so unqualified `registered_extensions`
-- resolves here.
