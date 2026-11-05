# Financial Fact migration chain

These scripts are one-time migrations. Before starting, verify the target database,
the application schema and existing objects. Keep all source writers paused
throughout the entire chain: individual scripts commit independently, and the
intermediate revision-only and row-capture states are not a certified cutover.
Set `search_path` explicitly to the verified application schema (and `pg_catalog`)
in the same session that runs the scripts. Production currently expects `public`.

Apply in this order:

1. `analysis_source_revision_up.sql`
2. `analysis_financial_fact_pending_up.sql`
3. `analysis_financial_fact_pending_revision_up.sql`
4. `analysis_financial_fact_tables_up.sql`

Preflight must reject an existing or partially installed chain for operator review.
The first script explicitly refuses an existing source-revision table; the other
scripts contain one-time CREATE/ALTER operations and fail on existing objects.
Never rerun the first script to repair a partial installation. Stop writers and
investigate the exact schema state and backup before deciding recovery.

The chain creates empty Fact tables. It does not validate historical source data,
establish a Fact baseline, authorize Fact features, or certify pending history.
