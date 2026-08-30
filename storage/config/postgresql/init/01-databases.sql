-- Runs only on first initialisation of an empty PGDATA.
-- Langfuse's DATABASE_URL points at a dedicated database, which POSTGRES_DB
-- (=postgres) does not create; without this a fresh boot fails to migrate.
CREATE DATABASE langfuse OWNER admin;
