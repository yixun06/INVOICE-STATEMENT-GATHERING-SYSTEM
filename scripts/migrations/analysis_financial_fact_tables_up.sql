-- Fact storage only. Does not establish a baseline or enable the reader.
-- Apply only after target/schema verification and during an approved cutover.
-- Set search_path to the verified application schema before running.
BEGIN;
CREATE TABLE analysis_order_financial_fact (
    market text NOT NULL, order_id text NOT NULL, order_created_date date NOT NULL,
    settlement_state text NOT NULL, payout_completed_date date, released_amount numeric,
    order_component_count bigint NOT NULL, charges_amount numeric NOT NULL,
    credits_amount numeric NOT NULL, refund_amount numeric NOT NULL,
    non_product_amount numeric NOT NULL, PRIMARY KEY (market, order_id)
);
CREATE TABLE analysis_financial_fact_revision (
    market text PRIMARY KEY, source_revision bigint NOT NULL
);
COMMIT;
