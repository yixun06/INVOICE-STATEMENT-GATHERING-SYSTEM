-- Experimental change capture. Apply only after a verified fact baseline.
-- Capture is transactional with the source write. Publication is deliberately
-- not enabled by this migration: revision coverage must first be proven.
BEGIN;
CREATE TABLE analysis_financial_fact_pending (
    market text NOT NULL CHECK (market IN ('shopee_my', 'shopee_sg')),
    order_id text NOT NULL,
    change_kind text NOT NULL CHECK (change_kind IN ('order', 'adjustment', 'unresolved')),
    source_transaction bigint NOT NULL,
    PRIMARY KEY (market, order_id, change_kind, source_transaction)
);

CREATE FUNCTION ig_fact_capture_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE entry record; entry_market text; entry_order text; entry_kind text;
BEGIN
    FOR entry IN SELECT * FROM jsonb_array_elements(
        CASE TG_OP WHEN 'INSERT' THEN jsonb_build_array(to_jsonb(NEW))
                   WHEN 'DELETE' THEN jsonb_build_array(to_jsonb(OLD))
                   ELSE jsonb_build_array(to_jsonb(OLD), to_jsonb(NEW)) END)
    LOOP
        IF TG_TABLE_NAME IN ('full_order_current_orders', 'full_order_current_items') THEN
            entry_market := entry.value->>'market';
        ELSE
            entry_market := CASE entry.value->>'platform'
                WHEN 'Shopee' THEN 'shopee_my' WHEN 'Shopee SG' THEN 'shopee_sg' END;
        END IF;
        IF entry_market NOT IN ('shopee_my', 'shopee_sg') OR entry_market IS NULL THEN
            RAISE EXCEPTION 'Cannot capture fact change for unknown market';
        END IF;
        IF TG_TABLE_NAME = 'order_adjustments' THEN
            entry_kind := 'adjustment'; entry_order := '';
        ELSE
            entry_order := btrim(coalesce(entry.value->>'order_id', ''));
            entry_kind := CASE WHEN entry_order = '' THEN 'unresolved' ELSE 'order' END;
        END IF;
        INSERT INTO analysis_financial_fact_pending (market, order_id, change_kind, source_transaction)
        VALUES (entry_market, entry_order, entry_kind, txid_current()) ON CONFLICT DO NOTHING;
    END LOOP;
    RETURN NULL;
END $$;

CREATE FUNCTION ig_fact_capture_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO analysis_financial_fact_pending (market, order_id, change_kind, source_transaction)
    VALUES ('shopee_my', '', 'unresolved', txid_current()),
           ('shopee_sg', '', 'unresolved', txid_current()) ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['full_order_current_orders','full_order_current_items',
        'statement_data','statement_financial_components','order_adjustments'] LOOP
        EXECUTE format('CREATE TRIGGER ig_fact_capture_row AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ig_fact_capture_row()', table_name);
        EXECUTE format('CREATE TRIGGER ig_fact_capture_truncate AFTER TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION ig_fact_capture_truncate()', table_name);
    END LOOP;
END $$;
COMMIT;
