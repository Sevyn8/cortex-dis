-- ============================================================================
-- DIS: uuidv7() function installation
--
-- Installs the project-wide UUIDv7 generator function in the DIS database.
-- This function is referenced by every canonical and identity_mirror table
-- as DEFAULT for the surrogate PK (`DEFAULT uuidv7()`).
--
-- The function is vendored from the same source used by Customer Master
-- (kjmph PL/pgSQL reference, RFC 9562). Keeping identical implementations
-- across DIS and Customer Master ensures UUIDv7 semantics are consistent
-- (monotonic, embedded ms timestamp, version-7 marker bits).
--
-- ----------------------------------------------------------------------------
-- Schema placement
-- ----------------------------------------------------------------------------
-- Installed in `public` so DIS DDL can call `uuidv7()` unqualified. Customer
-- Master installs it in `core`; DIS uses `public` to avoid needing a `core`
-- schema in the DIS database for one function.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - Postgres 13+ (for built-in gen_random_uuid()).
--   - pgcrypto extension is recommended for other DIS code but not strictly
--     required for this function on Postgres 13+, since gen_random_uuid() is
--     built-in there. To enable: CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- ============================================================================


CREATE OR REPLACE FUNCTION public.uuidv7() RETURNS uuid
    LANGUAGE plpgsql
    AS $$
begin
    -- use random v4 uuid as starting point (which has the same variant we need)
    -- then overlay timestamp
    -- then set version 7 by flipping the 2 and 1 bit in the version 4 string
    return encode(
        set_bit(
            set_bit(
                overlay(uuid_send(gen_random_uuid())
                        placing substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint) from 3)
                        from 1 for 6
                ),
                52, 1
            ),
            53, 1
        ),
        'hex')::uuid;
end
$$;


COMMENT ON FUNCTION public.uuidv7() IS
'UUIDv7 generator (RFC 9562). Vendored from kjmph PL/pgSQL reference. Used as DEFAULT for every surrogate PK in DIS canonical and identity_mirror schemas. Identical implementation to Customer Master''s core.uuidv7() for consistency across the platform.';
