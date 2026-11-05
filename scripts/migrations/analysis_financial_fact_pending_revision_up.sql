-- Revision-bound change capture; requires the existing M3 POC capture migration.
BEGIN;
ALTER TABLE analysis_financial_fact_pending ADD COLUMN source_revision bigint;
ALTER TABLE analysis_financial_fact_pending DROP CONSTRAINT analysis_financial_fact_pending_pkey;
CREATE UNIQUE INDEX ig_fact_pending_revision_identity ON analysis_financial_fact_pending
    (market, source_revision, change_kind, order_id) WHERE source_revision IS NOT NULL;
-- Rows created before this migration retain NULL revision and are unverified.
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_orders_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_orders_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows UNION SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_orders_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_items_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_items_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows UNION SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_full_order_current_items_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE WHEN market IN ('shopee_my','shopee_sg') THEN market END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_statement_data_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, CASE WHEN record_type='ADJUSTMENT' THEN '' ELSE btrim(coalesce(order_id,'')) END AS order_id, CASE WHEN record_type='ADJUSTMENT' THEN 'adjustment' WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_statement_data_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, CASE WHEN record_type='ADJUSTMENT' THEN '' ELSE btrim(coalesce(order_id,'')) END AS order_id, CASE WHEN record_type='ADJUSTMENT' THEN 'adjustment' WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows UNION SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, CASE WHEN record_type='ADJUSTMENT' THEN '' ELSE btrim(coalesce(order_id,'')) END AS order_id, CASE WHEN record_type='ADJUSTMENT' THEN 'adjustment' WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_statement_data_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, CASE WHEN record_type='ADJUSTMENT' THEN '' ELSE btrim(coalesce(order_id,'')) END AS order_id, CASE WHEN record_type='ADJUSTMENT' THEN 'adjustment' WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_statement_financial_components_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_statement_financial_components_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows UNION SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_statement_financial_components_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, btrim(coalesce(order_id,'')) AS order_id, CASE WHEN btrim(coalesce(order_id,''))='' THEN 'unresolved' ELSE 'order' END AS change_kind FROM old_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_order_adjustments_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, ''::text AS order_id, 'adjustment'::text AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_order_adjustments_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, ''::text AS order_id, 'adjustment'::text AS change_kind FROM old_rows UNION SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, ''::text AS order_id, 'adjustment'::text AS change_kind FROM new_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_order_adjustments_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 WITH changed AS (SELECT CASE platform WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END AS market, ''::text AS order_id, 'adjustment'::text AS change_kind FROM old_rows),
 advanced AS (UPDATE analysis_source_revision r SET revision=r.revision+1
   WHERE r.market IN (SELECT market FROM changed WHERE market IS NOT NULL)
   RETURNING r.market,r.revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT DISTINCT advanced.market,changed.order_id,changed.change_kind,txid_current(),advanced.revision
 FROM advanced JOIN changed ON changed.market=advanced.market
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION ig_revision_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 WITH advanced AS (UPDATE analysis_source_revision SET revision=revision+1 RETURNING market,revision)
 INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision)
 SELECT market,'','unresolved',txid_current(),revision FROM advanced;
 IF (SELECT count(*) FROM analysis_source_revision) <> 2 THEN RAISE EXCEPTION 'Analysis revision metadata is incomplete'; END IF;
 RETURN NULL;
END $$;
DROP TRIGGER ig_fact_capture_row ON full_order_current_orders;
DROP TRIGGER ig_fact_capture_truncate ON full_order_current_orders;
DROP TRIGGER ig_fact_capture_row ON full_order_current_items;
DROP TRIGGER ig_fact_capture_truncate ON full_order_current_items;
DROP TRIGGER ig_fact_capture_row ON statement_data;
DROP TRIGGER ig_fact_capture_truncate ON statement_data;
DROP TRIGGER ig_fact_capture_row ON statement_financial_components;
DROP TRIGGER ig_fact_capture_truncate ON statement_financial_components;
DROP TRIGGER ig_fact_capture_row ON order_adjustments;
DROP TRIGGER ig_fact_capture_truncate ON order_adjustments;
-- A consumer must call this against the same locked, consistent snapshot it
-- intends to certify. NULL revisions are historical and cannot prove coverage.
CREATE FUNCTION ig_fact_assert_capture_coverage(p_market text, p_from bigint, p_through bigint)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE current_revision bigint;
BEGIN
 IF p_market NOT IN ('shopee_my','shopee_sg') OR p_from IS NULL OR p_through IS NULL
    OR p_from < 0 OR p_from > p_through THEN
   RAISE EXCEPTION 'Invalid fact capture coverage range';
 END IF;
 SELECT revision INTO current_revision FROM analysis_source_revision WHERE market=p_market;
 IF current_revision IS DISTINCT FROM p_through THEN
   RAISE EXCEPTION 'Fact capture source revision changed';
 END IF;
 IF EXISTS (SELECT 1 FROM analysis_financial_fact_pending WHERE market=p_market AND source_revision IS NULL) THEN
   RAISE EXCEPTION 'Unverified historical fact capture';
 END IF;
 IF EXISTS (SELECT 1 FROM generate_series(p_from+1,p_through) AS required(revision)
            WHERE NOT EXISTS (SELECT 1 FROM analysis_financial_fact_pending p
                WHERE p.market=p_market AND p.source_revision=required.revision)) THEN
   RAISE EXCEPTION 'Fact capture revision gap';
 END IF;
 IF EXISTS (SELECT 1 FROM analysis_financial_fact_pending WHERE market=p_market
            AND source_revision > p_from AND source_revision <= p_through AND change_kind='unresolved') THEN
   RAISE EXCEPTION 'Unresolved fact capture';
 END IF;
END $$;
COMMIT;
