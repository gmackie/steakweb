-- OMNIDAT / omniDECT additions to the steakweb `registered_extensions` table.
-- Upstream steakweb (Shadytel) tracks SIP/copper extensions; omniDECT also binds
-- an extension to a cordless handset, so add the DECT identity columns that
-- prov_to_dect() writes. Safe to run against an existing steakweb database.

ALTER TABLE registered_extensions ADD COLUMN IF NOT EXISTS ipui       TEXT;    -- DECT International Portable User Identity (hex)
ALTER TABLE registered_extensions ADD COLUMN IF NOT EXISTS handset_id INTEGER; -- module handset slot (dect/fp/registry.py)

CREATE UNIQUE INDEX IF NOT EXISTS idx_regext_ipui
  ON registered_extensions (ipui) WHERE ipui IS NOT NULL;

-- switch convention: NULL unprovisioned | 11 SIP (upstream) | 20 omniDECT DECT base
