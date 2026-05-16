-- ===========================================================================
-- init_db.sql — Master initialization script (first install)
-- Atlas RA Seismic ETL Pipeline
-- ===========================================================================
-- Run this script ONCE to create and fully initialize the database.
-- In production environments, use Flyway or Alembic for incremental
-- migrations instead of running this file directly.
--
-- Usage (run from project root):
--   mysql -h 127.0.0.1 -u root -patlas2025 < sql/init_db.sql
--
-- Or run each migration file individually in order:
--   mysql -h 127.0.0.1 -u root -patlas2025 seismic_db < sql/migrations/V1__init_schema.sql
--   mysql -h 127.0.0.1 -u root -patlas2025 seismic_db < sql/migrations/V2__add_quarantine_table.sql
-- ===========================================================================

CREATE DATABASE IF NOT EXISTS seismic_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE seismic_db;

-- Run migrations in version order
-- NOTE: SOURCE is a MySQL client command. If you are running this
-- through a script or application driver, execute each migration
-- file individually using the commands above.
SOURCE sql/migrations/V1__init_schema.sql;
SOURCE sql/migrations/V2__add_quarantine_table.sql;

SELECT 'Database seismic_db initialized successfully.' AS status;
