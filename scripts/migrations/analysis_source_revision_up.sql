-- Requires PostgreSQL 10+; run explicitly in one transaction, never automatically.
BEGIN;
-- This is a one-time chain. Re-running would replace the final capture
-- functions with revision-only versions and can silently lose coverage.
DO $$ BEGIN
 IF to_regclass(format('%I.analysis_source_revision', current_schema())) IS NOT NULL THEN
   RAISE EXCEPTION 'Source revision already exists; do not rerun migration chain';
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS analysis_source_revision (market text PRIMARY KEY CHECK (market IN ('shopee_my','shopee_sg')), revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0));
INSERT INTO analysis_source_revision (market, revision) VALUES ('shopee_my',0),('shopee_sg',0) ON CONFLICT (market) DO NOTHING;
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_orders_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_full_order_current_orders_insert ON full_order_current_orders;
CREATE TRIGGER ig_revision_full_order_current_orders_insert AFTER INSERT ON full_order_current_orders REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_full_order_current_orders_insert();
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_orders_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM old_rows UNION SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_full_order_current_orders_update ON full_order_current_orders;
CREATE TRIGGER ig_revision_full_order_current_orders_update AFTER UPDATE ON full_order_current_orders REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_full_order_current_orders_update();
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_orders_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM old_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_full_order_current_orders_delete ON full_order_current_orders;
CREATE TRIGGER ig_revision_full_order_current_orders_delete AFTER DELETE ON full_order_current_orders REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_full_order_current_orders_delete();
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_items_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_full_order_current_items_insert ON full_order_current_items;
CREATE TRIGGER ig_revision_full_order_current_items_insert AFTER INSERT ON full_order_current_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_full_order_current_items_insert();
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_items_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM old_rows UNION SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_full_order_current_items_update ON full_order_current_items;
CREATE TRIGGER ig_revision_full_order_current_items_update AFTER UPDATE ON full_order_current_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_full_order_current_items_update();
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_items_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market FROM old_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_full_order_current_items_delete ON full_order_current_items;
CREATE TRIGGER ig_revision_full_order_current_items_delete AFTER DELETE ON full_order_current_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_full_order_current_items_delete();
CREATE OR REPLACE FUNCTION ig_revision_statement_data_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_statement_data_insert ON statement_data;
CREATE TRIGGER ig_revision_statement_data_insert AFTER INSERT ON statement_data REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_statement_data_insert();
CREATE OR REPLACE FUNCTION ig_revision_statement_data_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM old_rows UNION SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_statement_data_update ON statement_data;
CREATE TRIGGER ig_revision_statement_data_update AFTER UPDATE ON statement_data REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_statement_data_update();
CREATE OR REPLACE FUNCTION ig_revision_statement_data_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM old_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_statement_data_delete ON statement_data;
CREATE TRIGGER ig_revision_statement_data_delete AFTER DELETE ON statement_data REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_statement_data_delete();
CREATE OR REPLACE FUNCTION ig_revision_statement_financial_components_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_statement_financial_components_insert ON statement_financial_components;
CREATE TRIGGER ig_revision_statement_financial_components_insert AFTER INSERT ON statement_financial_components REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_statement_financial_components_insert();
CREATE OR REPLACE FUNCTION ig_revision_statement_financial_components_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM old_rows UNION SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_statement_financial_components_update ON statement_financial_components;
CREATE TRIGGER ig_revision_statement_financial_components_update AFTER UPDATE ON statement_financial_components REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_statement_financial_components_update();
CREATE OR REPLACE FUNCTION ig_revision_statement_financial_components_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM old_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_statement_financial_components_delete ON statement_financial_components;
CREATE TRIGGER ig_revision_statement_financial_components_delete AFTER DELETE ON statement_financial_components REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_statement_financial_components_delete();
CREATE OR REPLACE FUNCTION ig_revision_order_adjustments_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_order_adjustments_insert ON order_adjustments;
CREATE TRIGGER ig_revision_order_adjustments_insert AFTER INSERT ON order_adjustments REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_order_adjustments_insert();
CREATE OR REPLACE FUNCTION ig_revision_order_adjustments_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM old_rows UNION SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM new_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_order_adjustments_update ON order_adjustments;
CREATE TRIGGER ig_revision_order_adjustments_update AFTER UPDATE ON order_adjustments REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_order_adjustments_update();
CREATE OR REPLACE FUNCTION ig_revision_order_adjustments_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 UPDATE analysis_source_revision r SET revision = r.revision + 1 FROM (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market FROM old_rows) changed WHERE r.market = changed.market;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_order_adjustments_delete ON order_adjustments;
CREATE TRIGGER ig_revision_order_adjustments_delete AFTER DELETE ON order_adjustments REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_order_adjustments_delete();
CREATE OR REPLACE FUNCTION ig_revision_truncate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 UPDATE analysis_source_revision SET revision = revision + 1;
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 RETURN NULL; END $$;
DROP TRIGGER IF EXISTS ig_revision_truncate ON full_order_current_orders;
CREATE TRIGGER ig_revision_truncate AFTER TRUNCATE ON full_order_current_orders FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_truncate();
DROP TRIGGER IF EXISTS ig_revision_truncate ON full_order_current_items;
CREATE TRIGGER ig_revision_truncate AFTER TRUNCATE ON full_order_current_items FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_truncate();
DROP TRIGGER IF EXISTS ig_revision_truncate ON statement_data;
CREATE TRIGGER ig_revision_truncate AFTER TRUNCATE ON statement_data FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_truncate();
DROP TRIGGER IF EXISTS ig_revision_truncate ON statement_financial_components;
CREATE TRIGGER ig_revision_truncate AFTER TRUNCATE ON statement_financial_components FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_truncate();
DROP TRIGGER IF EXISTS ig_revision_truncate ON order_adjustments;
CREATE TRIGGER ig_revision_truncate AFTER TRUNCATE ON order_adjustments FOR EACH STATEMENT EXECUTE FUNCTION ig_revision_truncate();
COMMIT;
