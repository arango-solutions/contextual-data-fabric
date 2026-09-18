-- One-time Snowflake setup for the Federation Forge's live mode (ADR-0006 D-3;
-- deploy/forge/README.md, "Live mode"). Run once as ACCOUNTADMIN, after
-- setup.sql. Names match the CDF_FORGE_SNOWFLAKE_* defaults in the Forge.
--
-- The Forge DEPLOYS as CDF_FORGE (one schema per generated system, in its own
-- database, so TELEMETRY is never touched) and the fabric still QUERIES as
-- CDF_RO (least privilege, CC-7): the future grants below let the read-only
-- role see every schema the Forge creates without a per-run GRANT.
--
-- Replace <YOUR_USER> with the login the Forge runs under (the .env user; in
-- CI the SNOWFLAKE_USER secret).

USE ROLE ACCOUNTADMIN;

-- Own database: dropping it removes every Forge artifact at once.
CREATE DATABASE IF NOT EXISTS CDF_FORGE
  COMMENT = 'Federation Forge live mode: one schema per generated system; disposable';

-- Deployer role: may create schemas here and nowhere else; runs on the same
-- XS warehouse under the same CC-11 statement cap and resource monitor.
CREATE ROLE IF NOT EXISTS CDF_FORGE;
GRANT USAGE ON WAREHOUSE CDF_WH TO ROLE CDF_FORGE;
GRANT USAGE, CREATE SCHEMA ON DATABASE CDF_FORGE TO ROLE CDF_FORGE;

-- The query path stays read-only and sees what the Forge deploys.
GRANT USAGE ON DATABASE CDF_FORGE TO ROLE CDF_RO;
GRANT USAGE ON FUTURE SCHEMAS IN DATABASE CDF_FORGE TO ROLE CDF_RO;
GRANT SELECT ON FUTURE TABLES IN DATABASE CDF_FORGE TO ROLE CDF_RO;
GRANT USAGE ON ALL SCHEMAS IN DATABASE CDF_FORGE TO ROLE CDF_RO;
GRANT SELECT ON ALL TABLES IN DATABASE CDF_FORGE TO ROLE CDF_RO;

-- Let the Forge's login assume the deployer role.
GRANT ROLE CDF_FORGE TO USER <YOUR_USER>;

-- Sanity: USE ROLE CDF_FORGE; CREATE SCHEMA CDF_FORGE.FORGE_SMOKE; DROP SCHEMA CDF_FORGE.FORGE_SMOKE;
-- Teardown: USE ROLE ACCOUNTADMIN; DROP DATABASE CDF_FORGE; DROP ROLE CDF_FORGE;
