CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION
  WHEN undefined_file OR feature_not_supported THEN
    RAISE NOTICE 'TimescaleDB extension is not available in this local image; continuing with PostgreSQL + pgvector.';
END
$$;
