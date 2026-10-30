# InvoiceGather Project Status

### Shopee Analysis final minor polish — ready for PO review (2026-10-30)

- Tightened native spacing between the filters and shared module content; the
  segmented control keeps its collapsed accessible label and market session keys.
- Product Owner approved scoped CSS: the shared native control uses connected
  segments, a pale green selection, neutral surfaces and visible keyboard focus.
- Focused MY/SG module and filter checks: 60 passed; the final accepted-scope
  regression suite passed 246 tests with 32 explicit skips. A fresh fixture
  server supports browser checks; authenticated live-data acceptance remains
  with PO.
- Local checkpoint `2a7d00d` (`feat: finalize app surface and analysis
  dashboard ux`) is **COMMITTED / NOT PUSHED**. The prior clean restart of
  localhost:8503 was blocked by execution policy. No business data or schema
  write occurred.

Recent execution: 2026-10-30; app surface and shared Analysis UX checkpoint;
implemented; scoped CSS and accepted workflow/regression updates; 246 passed,
32 skipped, browser fixture, compile and diff checks; Git `2a7d00d` committed,
not pushed; UAT2 writes 0; schema delta 0; decision required none; next PO
live-browser review.

## Current Baseline

- Date: 2026-10-14
- Branch: `feature/uat2-invoice-persistence-integration`
- Product/code baseline before Platform Selector Refinement V1: `cd14652f24e93df184a5f277121cd51d6020f027`; **COMMITTED / PUSHED**, with local HEAD, upstream, and remote verified equal before this work.
- Hygiene checkpoints: `4eccc62` (`test: align Data Import batch state regression`) and `d4dd00c` (`chore: ignore generated report outputs`) are committed in this execution; they are pushed with this documentation checkpoint.
- Remote deployment: no later checkpoint is recorded as **REMOTE DEPLOYMENT VERIFIED**.
- Worktree: Platform Selector Refinement V1 feature checkpoint `996817ec30de5895c093fdaa3c91fc08f74bf695` (`feat: refine platform selection and routing`) and documentation checkpoint `b64f7a3a23e1ecd266a3dd8600a3f3e4919494a6` (`docs: record platform selector checkpoint`) are **IMPLEMENTED / TESTED / REVIEWED / COMMITTED / PUSHED**. Local, upstream, and remote were verified equal after the normal push; no deployment verification is recorded.

InvoiceGather is a reconciliation-and-evidence system. Full Order Current and
committed Statement are the active operational authorities; legacy Invoice/PDF
data remains read-only comparison/audit evidence.

## Current System State

### PYTHON_RUNTIME_DEPENDENCIES - VERIFIED / UNCHECKPOINTED

- `requirements.txt` remains the sole dependency manifest and now explicitly
  declares the direct runtime dependencies `altair`, `google-auth`,
  `SQLAlchemy>=2,<3`, and `psycopg2-binary>=2.9,<3`. This preserves the
  approved PostgreSQL dialect `postgresql+psycopg2`; no psycopg3 migration,
  schema, repository, or business-data change occurred. Local launch uses the
  project interpreter: `.\\.venv\\Scripts\\python.exe -m streamlit run app.py`.

### POSTGRESQL_MIGRATION_PLAYBOOK - DOCUMENTED / NO CODE OR BUSINESS-DATA WRITE

- The reusable migration playbook is now recorded in
  `.agents/skills/invoicegather-scope/SKILL.md`. It separates storage parity
  from reconstruction parity, requires canonicalization and overlap replay,
  prohibits dual authority and hidden legacy fallback, and requires consumer,
  dependency, schema, transaction, concurrency, and runtime-smoke checks.
- The Statement incident index records confirmed MY examples: the Google
  Sheets cell-capacity blocker, rejected dual-write, parity/readiness gap,
  missing runtime/dependency declarations, `.venv` reproducibility, ORM/schema
  drift, legacy consumers, concurrency protection, and 76 false
  `EXACT_DUPLICATE` to `CONFLICT` classifications from fixed-scale Numeric
  reconstruction. These remain historical examples, not universal rules.
- Application/runtime code, schema, UAT2/Google Sheets data, and PostgreSQL
  business data were not changed.

### POSTGRES_MY_STATEMENT_POSTGRESQL - CLOSED / LOCAL CHECKPOINTED / NOT PUSHED

- MY `Statement_Data`, `Statement_Financial_Components`, `Statement_Summary`,
  and `Order_Adjustments` use the local Docker PostgreSQL repository through
  the existing Data Import/financial-read/adjustment-read contracts. The
  PostgreSQL selection is explicit, fails closed, does not dual-write, and
  never falls back to a Google Sheets Statement tab. Full Order Current and
  Product Master retain their approved existing authorities; Product Master is
  explicitly retained on staff-editable Google Sheets.
- Historical Google Sheets/PostgreSQL parity is verified: 18,381 Statement Data,
  397,647 Financial Components, 448 Summary, and 20 Adjustments. Phase 2A
  widened only incompatible local varchar columns to their ORM contracts, with
  before/after row counts unchanged. No PostgreSQL business data was committed.
- The MY repository reuses the active Full Order/complete-bundle preflight,
  then performs a fresh `SERIALIZABLE` PostgreSQL preflight under a transaction
  advisory lock, atomically writes all four domains, and verifies every expected
  committed fact by authoritative readback. Unavailable/invalid local target or
  credentials fail closed with no Sheets fallback. A forced four-domain
  rollback control preserved counts exactly.
- Transactional readback now shares
  the classifier's comparison-only Decimal primitive for declared SQLAlchemy
  `Numeric` columns only. Thus `12.9` and PostgreSQL `12.90` are the same fact,
  while genuine numeric changes and every non-Numeric field remain fail-closed
  exact comparisons. The verifier still requires each stable key and every
  expected fact before transaction commit; no schema, Numeric precision, or
  business rule changed.
- The shared complete-bundle
  classifier now gives classifier-bound monetary fields backend-neutral Decimal
  identity only: insignificant scale and signed zero compare equal, while
  NULL/blank, provenance, structure, and real numeric differences remain
  distinct. PostgreSQL `Numeric(12,2)` storage and UI formatting are unchanged.
  A read-only replay of the protected 1,195-order MY source against its 198
  matching Statement rows / 4,203 components returned 1,119 NEW, 76 exact
  duplicates, 0 conflicts, 0 incomplete/unsafe, and 0 adjustment conflicts.
- MY Weekly Billing receives its three Statement datasets from PostgreSQL while
  preserving its existing non-Statement source reads and projector. Focused
  tests prove the MY Weekly Billing and reporting readers reject all Google
  Sheets Statement-tab access; existing MY/SG Statement regressions still pass.
  The required local runtime
  credential variables must be configured before hosted use. The first real
  PostgreSQL transaction proved `FULL_ROLLBACK_CONFIRMED` after its pre-COMMIT
  readback mismatch; after the representation-boundary fix, a subsequent
  authorized MY Commit succeeded and the same source reprocessed as
  `ALREADY_IMPORTED` with no additional write. Local checkpoint:
  `3b1244429abd787a69e12816edf89830b55ec0e2`
  (`feat: migrate shopee my statement runtime to postgres`); it is explicitly
  **NOT PUSHED**. Deployment remains **NOT VERIFIED**.

### POSTGRES_UPPER_LAYER_CONSUMER_AUDIT - AUDITED / MIGRATION DESIGN PENDING

- Read-only audit confirmed the reusable market-locked `StatementFinancialRead`
  and `OrderAdjustmentRead` projections already provide the correct exact
  Full Order/Statement boundary for SG Analysis. MY Live Analysis and MY
  Weekly Billing still retain `Invoice_Orders` / `Invoice_Items` Google Sheets
  readers for legacy financial/product semantics; Monthly Billing additionally
  remains bound to legacy Monthly Statement Google Sheets tabs. Consolidated
  Payout Product Summary is superseded by the canonical reader below. No
  upper-layer code should raw-join PostgreSQL authorities independently.

### MY_ANALYSIS_V2_CANONICAL_POSTGRESQL - CLOSED / FINAL UIUX ACCEPTED

- Shopee MY Analysis V2 is a read-only MY provider over the shared canonical
  Shopee Analysis view model and renderer. It loads only the explicit
  `SHOPEE_MY` Full Order Current repository and committed Statement reader,
  then obtains one `OrderFinancialProjection` per market/order. Product Sales
  is the selected Full Order Item `product_subtotal` sum; released amount,
  native signed components, refund evidence, and linked adjustment events are
  projection facts. Adjustments remain separate from release.
- The V2 page has shared Order Created/Payout date selection, product and
  status filters, canonical advanced filters, Overview, Product Analysis, and
  Financial & Reconciliation tabs. Its shared renderer keeps Product,
  Settlement, and secondary Adjustment KPI groups distinct; uses market-neutral
  compact reconciliation, signed-component, and product-ranking charts; and
  provides Daily/Weekly/Monthly trend display aggregation without widening the
  selected date authority. The shared renderer takes presentation metadata
  rather than PostgreSQL/table knowledge and is contract-tested for both MY
  (`RM`) and SG (`S$`) without changing SG Analysis semantics.
- Settlement presentation now keeps its populations explicit: Total Product
  Sales remains every selected eligible Full Order item, while Settled Product
  Sales, Released Amount, Statement components, and Settlement Difference use
  only the exact committed Statement ORDER cohort. Coverage reports settled
  versus selected orders; missing Statement remains a normal lifecycle state.
  Trend frames and their explicit chart domain are clipped to the selected
  date interval. Order inspection is search-first beside reconciliation and
  preserves missing Statement as a calm lifecycle state rather than a mismatch.
- The retired MY Analysis consumer no longer reads `Invoice_Orders`,
  `Invoice_Items`, Google Sheets Invoice tabs, Invoice/PDF classification,
  layout states, or legacy final/refund/line-subtotal fields. There is no
  fallback. Missing Statement evidence stays explicit and reconciliation is
  fail-closed. Business/schema/data writes: 0. Deployment: **NOT VERIFIED**.
- The final shared-renderer refinement uses a responsive two-row filter layout,
  deliberate 2x2 Product and Settlement KPI groups, and a simplified Overview
  with only settlement breakdown and reconciliation coverage. Product and
  Financial tabs own their respective date-basis-labelled trends. Reconciliation
  is a labelled 100% stacked bar with distinct healthy, pending-lifecycle, and
  mismatch presentation; native component impact excludes base Product Price
  from the visual ranking but retains it in exact detail. Order inspection now
  appears before the reconciliation dataframe. No business formula, authority,
  projection, cohort, component sign, filter meaning, or database behavior
  changed. Product Owner final UI/UX acceptance is recorded. Deployment remains
  **NOT VERIFIED**.

### SHOPEE_ANALYSIS_FINAL_UIUX_REFINEMENT - CLOSED / PRODUCT OWNER ACCEPTED

- The final shared-renderer refinement is accepted as the completion state for
  the canonical MY Analysis V2 consumer. The shared MY/SG presentation contract,
  authoritative cohort semantics, and read-only PostgreSQL path remain unchanged.
  Deployment remains **NOT VERIFIED**.

### SG_ANALYSIS_SHARED_TEMPLATE_CUTOVER - CLOSED / PRODUCT OWNER ACCEPTED

- Shopee SG now has a thin market-locked `SHOPEE_SG` provider over the existing
  shared canonical Shopee Analysis view model and renderer. The former separate
  SG Phase A dashboard and its parallel report types are retired. Full Order
  items remain the Product cohort; `OrderFinancialProjection` remains the
  settlement, signed native-component, refund, and linked-adjustment authority.
  Missing Statement is still a Pending Statement evidence lifecycle state, not
  a mismatch. The shared dashboard receives `S$` presentation metadata and has
  no SG-specific renderer or legacy Invoice runtime read. Product Owner verified
  live SG canonical data in the InvoiceGather localhost UI. Business/schema/data
  writes: 0. Deployment: **NOT VERIFIED**.

### ORDER_FINANCIAL_PROJECTION - CLOSED / AUTHORITY BOUNDARY VERIFIED

- A shared read-only `OrderFinancialProjection` now composes the market-bound
  PostgreSQL Full Order Current repository, a Statement-only committed
  financial reader, and committed Order Adjustment reader for one exact
  `MarketContext + order_id`. It exposes Full Order date/status, committed
  Statement payout/released/product-price facts, raw native financial
  components plus deterministic native-name totals, refund components, and
  independent adjustment events/totals. It does not define a net-income
  formula, allocate order-level money to items, or select/filter consumer UI
  policy.
- A Statement ORDER without the matching market-bound Full Order Current row
  fails closed. A Full Order with no committed Statement is explicit as
  `MISSING_STATEMENT`, without any Invoice/PDF fallback. MY/SG platform labels
  remain normalized only by `MarketContext`; cross-market readers fail closed.
  The new layer has no Product Master, Invoice_Orders, Invoice_Items, or Google
  Sheets Invoice-tab runtime dependency. UAT2/business/schema write delta: 0.
- Authority-boundary verification moved all Full Order snapshot access,
  `order_created_date`, and `order_status` composition out of
  `shopee_statement_financial_read.py`. That reader now returns only committed
  native Statement facts; `OrderFinancialProjection` performs the exact-market
  Full Order/Statement join and owns its `MISSING_STATEMENT` state. SG Analysis
  retains the same visible behavior while consuming `OrderFinancialProjection`
  for its order date/status, settlement, released amount, and components; its
  separate Full Order snapshot is now item-only (quantity/subtotal), so it does
  not recreate a second Full Order/Statement composite.
- Focused projection, Statement-financial-read, and SG Analysis regressions
  passed (14 tests); changed modules compile and the diff check passes. Code
  checkpoint `cf80bd1` (`feat: add order financial projection`) contains only
  the projection, boundary cleanup, and SG compatibility wiring/tests. The
  requested real PostgreSQL smoke remains blocked before a query because
  `INVOICEGATHER_PG_USER` and `INVOICEGATHER_PG_PASSWORD` are not present in
  this Codex environment; no Google Sheets fallback was attempted. Business
  writes: 0. Push: **NOT PUSHED**. Deployment: **NOT VERIFIED**.

### SG_ANALYSIS_ORDER_FINANCIAL_PROJECTION_CUTOVER - CLOSED / VERIFIED

- Shopee SG Analysis now uses `OrderFinancialProjection` for all order-row
  authority: Full Order status/date plus Statement payout/released/settlement
  and native financial/refund components. Its retained Full Order snapshot is
  item-only for quantity and product subtotal. The independent market-locked
  adjustment reader remains solely for the global timeline, including unlinked
  events; it does not alter released amount or create a derived net formula.
- Market identity remains `shopee_sg` / `Shopee SG`; there is no Invoice or
  Google Sheets Invoice runtime read/fallback, no product-level fee allocation,
  and native component rows are preserved without multiplication. The shared
  `HistoricalInvoiceStorageError` exception type is not a data dependency.
- Focused SG Analysis plus OrderFinancialProjection regressions passed (11
  tests); changed modules compile and diff checks pass. Code checkpoint
  `72855d2` (`feat: complete sg analysis projection cutover`) contains only
  the SG service wiring and focused regression. Business/schema writes: 0.
  Push: **NOT PUSHED**. Deployment: **NOT VERIFIED**.

### CONSOLIDATED_PRODUCT_SUMMARY_POSTGRESQL_CUTOVER - CLOSED / REAL LOCALHOST SMOKE VERIFIED

- Payout Product Summary now selects only `PASSED` + `COMMITTED` Statement
  `ORDER` rows from the market-bound PostgreSQL Statement repository, keyed by
  `MarketContext` and `payout_completed_date`. The selected exact
  `(market, order_id)` values resolve through the corresponding PostgreSQL Full
  Order snapshot; Full Order provides all item/product facts and the existing
  product-summary engine performs the same direct subtotal calculation as
  Order-Created mode.
- Payout mode has zero runtime Invoice/PDF dependency: it does not construct or
  read a legacy Invoice gateway, `Invoice_Orders`, `Invoice_Items`, or Google
  Sheets Invoice tabs. Statement SKU rows, Financial Components, and
  Adjustments are not used or allocated to product rows. A payout-selected
  order missing its Full Order order or items fails explicitly; no fallback is
  permitted.
- MY and SG real localhost PostgreSQL runtime smoke passed. Displayed Payout
  Product Summary data matched direct PostgreSQL inspection; MY and SG
  physical Statement platform to `MarketContext` routing and corresponding
  Full Order market isolation remain fail-closed. No Google Sheets or legacy
  Invoice fallback was observed. Invoice_Orders reads: 0; Invoice_Items reads:
  0; Google Sheets Invoice-tab reads: 0. Product Master is intentionally
  unchanged. Deployment remains **NOT VERIFIED**.

### ORDER_CREATED_PRODUCT_SUMMARY_FULL_ORDER_ONLY - CLOSED / REAL LOCALHOST SMOKE VERIFIED

- `Order Created Date` Consolidated Product Summary now selects a dedicated,
  market-bound PostgreSQL Full Order snapshot before its date filters, table,
  export, and Compact Barcode inputs are built. It does not construct or read
  a legacy Invoice gateway, `Invoice_Orders`, `Invoice_Items`, Google Sheets
  Invoice tabs, or Statement data. The Full Order boundary fails closed if a
  market-bound repository returns another market.
- Payout-date Product Summary is now the adjacent canonical PostgreSQL
  Statement-order-selection plus Full Order-item reader. Product Master,
  Billing, Analysis, persistence, and schema behavior remain unchanged.
- MY and SG real localhost PostgreSQL runtime smoke passed. MY's retained
  reference result is 429 products and 31,768 quantity. No fallback to Google
  Sheets or legacy Invoice data was observed. Deployment remains **NOT
  VERIFIED**.

### POSTGRES_SG_STATEMENT_POSTGRESQL - IMPLEMENTED / TESTED / REPLAY VERIFIED / LOCAL CHECKPOINTED / NOT PUSHED

- SG Statement dynamic zero `SERVICE_FEE_DETAIL` overlap handling is **CLOSED /
  REAL SMOKE VERIFIED**. The mixed 30/08/2026-05/09/2026 source classified 427
  orders as 113 NEW and 314 exact duplicates, with 0 conflicts/unsafe and all
  113 NEW orders covered by Full Order. The previously failing
  `Income.released.sg.20260929_20261006.xlsx` source classified 709 orders as
  already imported (910 SKU rows), with zero additional writes. Historical
  PostgreSQL facts were not deleted or rewritten.

- SG `Statement_Data`, `Statement_Financial_Components`, `Statement_Summary`,
  and `Order_Adjustments` now use the shared market-scoped PostgreSQL
  repository when the explicit SG Statement backend is `postgresql`. Data
  Import, Reconcile/Review/preflight/Commit/readback, financial component and
  adjustment projections, and SG Analysis Phase A receive the same PostgreSQL
  Statement reader/writer; there is no union, fallback, or direct legacy SG
  Statement Sheet access in that mode. Full Order Current and Product Master
  remain Google Sheets authorities.
- The repository has a deterministic SG-only advisory transaction lock and
  retains the MY shared complete-bundle classifier, `SERIALIZABLE`
  transaction, four-domain atomic write, pre-COMMIT full-field readback, and
  Decimal/Numeric comparison boundary. Explicit `google_sheets` selection
  retains the stable SG Sheets writer during the migration period, but it is
  never an automatic PostgreSQL fallback. SG Weekly/Monthly Billing remains
  fail-closed/not enabled, so no unapproved SG Billing behavior was added.
- Historical PostgreSQL parity remains 5,606 Statement Data, 123,033
  Financial Components, 68 Summary, and 17 Adjustments. The current populated
  SG history is the read-only duplicate-replay population: 2,417 ORDER bundles
  and 17 adjustments are exact duplicates; no genuinely NEW live source was
  selected. Focused factory, active Statement, SG Analysis, MY isolation, and
  local four-domain transaction/rollback tests pass. A real NEW SG Statement
  Commit is deliberately **NOT PERFORMED** and requires separate Product Owner
  authorization. Deployment is **NOT VERIFIED**.
- The Product Owner-approved dynamic Service Fee overlap rule is implemented at
  the shared complete-bundle comparison boundary: a `SERVICE_FEE_DETAIL` is
  ignored only when it is exactly zero and the opposite parsed export proves
  that the exact dynamic header is absent. This does not alter stored facts or
  general missing-value semantics. The real 709-order overlap replay is pending
  because the local Docker PostgreSQL container/runtime credentials were not
  available in this execution; no checkpoint is authorized until that replay
  confirms the expected zero-write result.

### SHOPEE_SG_ANALYSIS_PHASE_A - IMPLEMENTED / TESTED / UNCHECKPOINTED

- Shopee SG Live Analysis now uses only market-scoped Full Order Current,
  Statement_Data / Statement_Financial_Components through the shared
  `read_committed_statement_financials` boundary, and a new read-only
  Order_Adjustments boundary. The route never invokes the Invoice/PDF-based MY
  service. Order Created Date reports order count, item quantity, and source
  product subtotal; Payout Date reports only settled order count and native
  released amount. An order without Statement evidence remains explicitly
  unavailable, not zero.
- Financial components retain their native persisted rows and component-name
  totals only; the adjustment timeline remains independent of released amount.
  Product-level settlement allocation, Original Sales, Discount, MY-02B
  financial layouts, net result, profit, Payout Product Summary, and Billing
  remain deferred. Market mismatch at either financial or adjustment reader
  fails closed; no SG-to-MY fallback is available.
- Live SG read-only validation: 8,173 Current Orders / 10,886 Items;
  2,417 available Statement orders and 5,756 without settlement; 123,033
  component rows, all linked to the 2,417 settled orders; 17 adjustment events
  totalling S$207.16 separately. UAT2/business/schema write delta: 0.

### STATEMENT_WORKFLOW_REVIEW_CHECKPOINT - COMMIT OUTCOME FIX IMPLEMENTED / RUNTIME REPLAY PENDING

- MY and SG share a session-local Statement workflow checkpoint separate
  from the mutable presentation cursor. A coherent staged review restores the
  highest explicitly completed step after a Streamlit rerun, harmless widget
  interaction, or active-session hydration; an explicit Back action remains
  authoritative. A lower Validate cursor can no longer silently replace a
  current Review & Commit destination while its review/commit plan remains
  valid in app-harness coverage. This checkpoint is not yet accepted as a
  real-runtime fix.
- Genuine evidence drift now retains the Review destination as explicitly
  blocked: stale evidence and prior plan details are hidden, `Commit Statement`
  is disabled, and only a successful Refresh validation replaces the
  stage/review pair and clears the stale reason. This changes no Statement
  parser, evidence classifier, duplicate/partial-commit rule, Full Order gate,
  adjustment classification, or persistence behavior.
- Verification: shared state tests plus MY/SG AppTest rerun/hydration/stale
  coverage passed; the focused Statement suite passed 53 tests with 14
  explicitly archived V2 skips; changed modules pass `py_compile` and
  `git diff --check`. The local 1,195-order source was not available for a new
  runtime replay in this execution; its last verified 1,119 NEW / 76 exact
  duplicate / 0 conflict / 0 Full Order gap evidence remains unchanged because
  business classification code was not modified. Google Sheets, UAT2,
  schema, and business write delta: 0. Git stage/commit/push: 0; deployment:
  **NOT VERIFIED**.
- Follow-up source-selection audit found a separate real crash path:
  `_render_source_selection` directly indexed the selected-label map although
  Streamlit may return `None` or a stale widget value after page/session
  restoration. The selector now restores only a valid persisted/evidenced
  source, otherwise requires an explicit selection; it never guesses from a
  batch ID. The focused AppTest regression passes, while an authenticated
  browser replay of the MY Step 5 case remains required. Business/schema/UAT2
  write delta: 0; Git stage/commit/push: 0; deployment: **NOT VERIFIED**.
- Commit no longer maps every `committed=False` result to Validate. The shared
  commit result now carries an authoritative navigation outcome: successful
  write completes normally; `ALREADY_IMPORTED / NO_NEW_FACTS` is an idempotent
  zero-write completion; stale, conflict, unsafe, and generic blocked outcomes
  remain in Review with an explicit refresh blocker; verified no-write writer
  failure remains in Review with a technical error. The handler does not call
  staged-source cleanup, recovery removal, or global reset. The separate
  Streamlit import/session disappearance remains under investigation. The
  opt-in redacted trace now includes outcome and safe reason codes, in addition
  to handler, preflight, writer, transition, rerun, and first-render markers.
  Focused MY/SG outcome, service, persistence, writer, and pilot tests pass.
  Business/schema/UAT2 write delta: 0; Git stage/commit/push: 0; deployment:
  **NOT VERIFIED**.
- Refreshing a stale Review or retrying after verified `WRITE_NOT_APPLIED` now
  intentionally returns to Step 4 Reconcile before a new Review/Commit plan is
  exposed. If a refresh cannot construct the authoritative writer preflight,
  Step 4 retains the exact blocker and exposes `Rebuild reconciliation`; it no
  longer renders a generic unavailable state with no recovery action. Missing
  staged source bytes fail closed with a re-upload instruction and preserve the
  existing review rather than reporting a false successful refresh. Shared
  MY/SG AppTest coverage verifies stale Review -> Reconcile -> Review,
  unavailable-preflight rebuild, refreshed conflict blocking, and source-byte
  loss. Business/schema/UAT2 write delta: 0; Git stage/commit/push: 0;
  deployment: **NOT VERIFIED**.

### SHOPEE_STATEMENT_COMPLETE_BUNDLE_PARTIAL_COMMIT - IMPLEMENTED / TESTED / UNCHECKPOINTED

- MY and SG now share one authoritative committed-snapshot classifier and
  write-plan builder in Reconcile, Review, and locked Commit. Each order is
  classified as `NEW`, `EXACT_DUPLICATE`, `CONFLICT`, or
  `INCOMPLETE_UNSAFE` from its complete evidence bundle: ORDER, native SKU
  rows, financial components, refund facts, and provenance. Repeated child
  rows are compared as multisets; missing, extra, ambiguous, or changed facts
  cannot be discarded as duplicates.
- A mixed source may skip complete exact-duplicate bundles and commit only
  complete new bundles with all associated children. Full Order Current is
  checked only for `NEW` order IDs. Adjustments are classified independently;
  exact duplicates are skipped, new linked or unlinked events are retained,
  and conflicting/unsafe events fail closed. A fully duplicate source with no
  new adjustment ends as `ALREADY_IMPORTED / NO_NEW_FACTS` with zero rows and
  no duplicate artifact.
- Source-level control totals remain the immutable validation contract and are
  displayed separately from the filtered new-facts write set. The UI reports
  new, skipped, conflict, unsafe, and new-only Full Order counts. Commit obtains
  the application lock, reloads committed evidence, and reruns the same builder;
  material drift invalidates or safely recomputes the authorized plan.
- Fresh read-only MY replay of `Income.released.my.20261011_20261023.xlsx`
  classifies 1,119 complete new bundles, 76 complete exact duplicates, 0
  conflicts, and 0 incomplete/unsafe bundles. All 1,119 new orders have Full
  Order Current coverage. Source Total Released remains RM35,759.06; the
  filtered new-order write set is RM33,126.14. Nine adjustments are new, one is
  skipped as an exact duplicate, and none conflict. Status is `MIXED` and the
  plan is eligible without rewriting any duplicate facts.
- Google Sheets does not provide database-level cross-sheet transactions. The
  writer retains one batch request plus exact readback and recovery checks, and
  the residual partial-write/uncertain-outcome risk remains explicit. No live
  Google Sheets, database, schema, or business write was performed. Git
  stage/commit/push: 0; deployment: **NOT VERIFIED**.

### SHOPEE_MY_SG_ANY_PERIOD_STATEMENT_MODEL - IMPLEMENTED / TESTED

- **Shopee MY now uses the same native any-period Statement workflow as Shopee
  SG.** Both markets parse their own source contract, preserve actual source
  period start/end (including irregular and cross-month periods), validate
  exact market-scoped Full Order Current order existence, and commit only the
  immutable Statement-owned `Statement_Data`,
  `Statement_Financial_Components`, `Statement_Summary`, and
  `Order_Adjustments` artifacts.
- The active MY/SG gate is `FULL_ORDER_CURRENT_ORDER_EXISTENCE` with semantic
  overlap evidence dedupe: exact repeated source facts return
  `ALREADY_IMPORTED`; overlapping unrelated orders remain eligible; a changed
  payout/release fact is `SETTLEMENT_EVIDENCE_CONFLICT` and fails closed. No
  weekly/monthly calendar boundary, Invoice/PDF coverage, Product Master
  evidence, SKU allocation, or legacy financial-layout classification is an
  active Statement gate.
- A shared read-only financial projection now joins Full Order Current and
  committed Statement facts by normalized `(market, order_id)`. It exposes
  created date, payout date, released amount, Statement Product Price, native
  signed components with batch/file/source-row provenance, and explicit
  missing, unmatched, duplicate, or conflicting evidence states. Components
  remain at their native ORDER/SKU/ADJUSTMENT granularity; no item allocation
  or Invoice/PDF fallback is inferred.
- **Invoice PDFs, `Invoice_Orders`, `Invoice_Items`, and PDF-derived fields are
  permanently deprecated as business-data runtime sources.** They may remain
  read-only historical comparison/audit data until separately retired. Weekly
  Billing, Monthly Billing, and the still-legacy financial/product report
  modules are outside this migration and must not be treated as migration
  blockers or active-source fallbacks.
- Checkpoint `c7908deaaa520794e78e40b4f765c3d5ed2a83b1`
  (`feat: align shopee my with any-period statement pipeline`) is committed and
  pushed to `origin/feature/uat2-invoice-persistence-integration`. No Google
  Sheets write, schema migration, or deployment occurred. Next independent
  scope: migrate a report surface to the shared Full Order + committed
  Statement read contract.

### SHOPEE_MY_CLOSING_PHASE_1 - MY-01 IMPLEMENTED / TESTED / PUSHED; MY-02 AUDITED

- **MY-01 date-source alignment is implemented.** Shopee MY Live Analysis and
  Consolidated Product Summary now take `Order Created Date` exclusively from
  `Shopee_FullOrder_Current_Orders.order_created_date` and `Payout Date`
  exclusively from `PASSED` + `COMMITTED` `Statement_Data` `ORDER` rows, using
  normalized `(market, order_id)`. Repeated Statement SKU/component rows cannot
  multiply reporting orders or amounts; duplicate equal payout rows collapse to
  one date and conflicting payout dates fail closed. A missing Statement match
  remains unknown/unreleased with no Invoice/PDF date fallback.
- The retained Payout Product Summary and Live Analysis financial-layout code
  still contains legacy Invoice/PDF reads. It is historical compatibility code,
  not an approved active-source fallback; migration to Full Order + committed
  Statement remains an independent follow-up task.
- **MY-02 audit:** the active 808-order warning is caused by
  `shopee_full_order_reconciliation.py` producing the legacy compatibility
  layout `FULL_ORDER_STATEMENT_PROVED`; Live Analysis deliberately recognizes
  only `NORMAL_ORDER`, `CANCELLED_ORDER`, and `RETURN_REFUND`, so those records
  fail closed. No reclassification was made. Legacy Invoice/PDF tables remain
  preserved; no deletion/decommissioning is authorized.
- Read-only September 2026 comparison is unchanged before/after alignment:
  Order Created 2,168 selected / 2,160 normal / RM60,468.12 sales / 4,341 units
  / RM263.70 refunds; Payout 2,049 selected / 2,039 normal / RM59,552.65 sales
  / 4,215 units / RM320.31 refunds. Focused MY report tests: 59 passed.
  Google Sheets/UAT2/schema/business write delta: 0. Checkpoint `71d0135`
  (`feat: align Shopee MY reporting dates with Full Order and Statement`) is
  pushed to `origin/feature/uat2-invoice-persistence-integration`; deployment:
  **NOT VERIFIED**. Next: Product Owner scope for MY-03 legacy financial-layout
  migration, if desired.

### SHOPEE_SG_STATEMENT_SETTLEMENT_PILOT - VERIFIED / CHECKPOINT READY

- Product Owner confirmed the source authorities: Full Order Current is the
  operational order/product authority; Statement_Data is the order-level
  settlement authority; Statement_Financial_Components is the detailed
  financial-fact authority for future Dashboard drill-down; and
  Order_Adjustments is the immutable post-payout lifecycle authority. Statement
  facts are not projected into Full Order Current and Statements never use an
  overwrite/current-snapshot model.
- A Statement upload is one immutable native artifact/batch, may cover any
  valid Shopee-supported source period, and retains its actual source dates,
  deterministic hash/provenance, duplicate/conflict controls, fresh precommit
  reload, application lock, atomic write/readback verification, and market /
  currency isolation. No seven-day or invented calendar-month rule is allowed;
  a multi-file monthly assembler is deferred.
- The active SG pilot gate is exact `(market, order_id)` existence against Full
  Order Current plus Statement-internal Summary/component validation. Invoice /
  PDF, Product Master, merchandise/SKU/group reconciliation, and
  `EXACT`/`EXPLAINED`/`NONE` residual classification are not active gate
  dependencies. `Total Amount` and `Grand Total` remain rejected as independent
  Full Order settlement-income sources.
- SG source differences must be represented by market-aware Statement source
  contract/configuration (S$ released header, GST components, BCRS, and absent
  shipping-discrepancy worksheet), then normalized into shared canonical models.
  The pilot persists to SG UAT2 Statement_Data, Statement_Financial_Components,
  Statement_Summary, and Order_Adjustments only, with no MY fallback and no
  PostgreSQL runtime change.
- **Controlled live SG smoke:** `Income.released.sg.20260830_20260905.xlsx`
  parsed as `shopee_sg` for 2026-08-30 through 2026-09-05 (427 ORDER rows,
  554 SKU rows, 4 adjustment events, S$4,496.13 released, S$12.96 adjustment
  control). Read-only preflight found 427/427 exact `(shopee_sg, order_id)`
  Full Order Current matches, no duplicate/conflict/blocker, no Invoice update,
  and an SG-locked writer. The one authorized commit batch
  `20261024073616-543e2eca` passed exact writer readback and independent
  read-only tab verification: 985 Statement_Data (427 ORDER / 554 SKU / 4
  ADJUSTMENT), 21,176 Financial Component, 34 Summary, and 4 Order_Adjustment
  rows; every persisted platform is `Shopee SG`, ORDER payout dates span
  2026-08-30 to 2026-09-05, released total is S$4,496.13, component amount
  aggregate is S$8,664.18, and adjustment total is S$12.96. Immediate re-upload
  returned `ALREADY_IMPORTED`. MY writes: 0; PostgreSQL writes: 0. Git stage,
  commit, and push remain 0; deployment remains **NOT VERIFIED**.
- **Streamlit stale-review lifecycle:** the first SG UI review after the pilot
  was trapped by a presentation-only legacy proof-version comparison: a valid
  refreshed SG review carries `FULL_ORDER_EXISTENCE_V1`, while the UI expected
  only the MY Full Order proof version and immediately re-marked it stale. The
  shared UI guard now derives the expected proof version from the active market
  context, so an obsolete review remains blocked, Refresh validation atomically
  replaces and clears it, and the refreshed SG review proceeds Validate →
  Reconcile → Review & Commit. Fresh source evidence remains protected by the
  existing pre-commit evidence comparison. The prior smoke source is now
  correctly `ALREADY_IMPORTED`, so no second write was attempted.
- **Final conflict verification:** a controlled in-memory copy of the committed
  SG native source changed the payout date for persisted order
  `2609056XC1TSRC` (2026-09-05 to 2026-09-04) while retaining a distinct hash.
  The shared, market-configured semantic overlap gate now reports
  `SETTLEMENT_EVIDENCE_CONFLICT`, surfaces the exact order blocker in Streamlit,
  and produces no commit plan; fresh writer preflight repeats the same guard.
  Date overlap alone is allowed, exact deterministic order facts dedupe as
  `ALREADY_IMPORTED`, and mixed duplicate/new evidence fails closed. Live SG
  before/after state remained identical (1 committed source, 2,417 order facts,
  SG platform only); no Statement_Data, component, summary, adjustment, MY, or
  PostgreSQL rows were written. Focused SG/MY/writer/UI tests: 151 passed;
  `py_compile` and diff checks pass. Checkpoint and push are authorized next.

### SHOPEE_STATEMENT_FULL_ORDER_ARCHITECTURE_MIGRATION_CLOSED - PERSISTENCE VERIFIED / TESTED / CHECKPOINT AUTHORIZED

- Active Shopee MY and SG Statement readiness is now Full Order Current + market-scoped Product Master + Statement only, joined by exact `(market, order_id)`. Validate, Review, ready-to-commit, currency check, and the fresh locked pre-commit rebuild use the same review builder; the pre-commit writer reads and writes only Statement-owned tabs.
- `Invoice_Orders`, `Invoice_Items`, PDF coverage, legacy Invoice price/quantity/refund evidence, and Invoice-to-Statement coverage no longer affect the active Statement workflow. The legacy V2 persistence/writer route remains preserved for archive/audit callers only. Full Order-derived transient compatibility objects are not persisted to Invoice tables.
- Fail-closed controls remain: missing Full Order, unresolved PM identity/NAV/USOLF, malformed or conflicting Statement financial evidence, non-zero merchandise/financial mismatch, duplicate Statement facts, stale evidence, and market mismatch. MY/SG platform filtering is explicit; SG cannot fall back to MY Current data.
- Read-only replay: MY 2026-10-05 to 2026-10-11 and 2026-09-14 to 2026-09-20 are correctly `ALREADY_IMPORTED` (463 and 543 order rows). MY 2026-07-20 to 2026-07-26 has 439 Statement orders, 439/439 Full Order coverage, 439 exact settlements, 0 unexplained settlements, 6 refund SKU rows, 3 adjustments, and is Ready with `ACTIVE LEGACY INVOICE DEPENDENCIES = 0`. SG settings resolve to `Shopee SG` and the isolated writer destination; no business write was made.
- Regression: 181 focused Statement/Full Order/writer/persistence/UI tests pass; changed modules pass `py_compile`; `NEW_REGRESSION = 0`. The Product Owner confirms two separate real MY weekly Statement smoke commits completed through this active flow, including writer/readback; persistence is therefore **VERIFIED**. UAT2, Statement, Full Order, Product Master, Invoice/PDF, and schema business writes by this checkpoint task: 0. Git checkpoint/push: authorized.

### POSTGRESQL_FOUNDATION_READY - VERIFIED / FROZEN AT LOCAL FOUNDATION

- The bounded one-way Google Sheets to local Docker PostgreSQL migration completed on 2026-10-23 at the verified target `127.0.0.1:5433/invoicegather` in the running `invoice_postgres` PostgreSQL 15 container. This is the durable `POSTGRESQL_FOUNDATION_READY` stop point. Application repositories remain on Google Sheets; no cutover, dual-write, production deployment, repository integration, or Google Sheets business write occurred.
- The failed historical attempt had left all eight expected tables present with zero rows. Its root causes are corrected: `order_status` is safely widened, `resolve_sku` is unrestricted text, all ORM financial fields use `Decimal` / exact `Numeric`, credentials are required from environment variables, and the migration verifies the exact local Docker target before schema or row writes. The sync contains no truncate/delete/reset path; it rejects target-only keys instead of pruning them.
- Source and PostgreSQL counts match: Invoice Orders 5,997; Invoice Items 8,575; Full Order Current Orders 14,456; Full Order Current Items 21,805; Statement Data 18,381; Statement Financial Components 397,647; Statement Summary 448; Order Adjustments 20. All source business keys were unique. Verification matched 24 representative rows, NULL/non-NULL counts for all 180 migrated source columns, exact sums for all 51 financial columns, and the complete 21,805-row `resolve_sku` key/value digest; maximum preserved `resolve_sku` length is 58.
- An initial verification-code mismatch failed closed and rolled back every pending row insert; the issue was only tuple serialization in the `resolve_sku` digest. The corrected migration then committed successfully. A full real rerun reported `affected=0` for every table while repeating all verification checks, proving no duplicates and no rewrites for an unchanged source snapshot.
- DBeaver connection is confirmed against the same target: host `127.0.0.1`, port `5433`, database `invoicegather`, and the Docker-configured database user; the password remains external to source control. Local PostgreSQL schema/data writes occurred only for this authorized migration. Repository integration, an application PostgreSQL backend, shadow/dual write, hybrid/full cutoff, production deployment, Google Sheets replacement, and company-server-versus-cloud hosting remain deferred pending a new Product Owner decision. Checkpoint: `feat: establish verified postgres foundation` on `feature/uat2-invoice-persistence-integration`; deployment: **NOT AUTHORIZED**.
- **Cross-domain market-boundary prerequisite closed (2026-10-27):** Statement PostgreSQL remains physically isolated by approved `platform` labels (`Shopee` / `Shopee SG`), while Full Order PostgreSQL remains physically keyed by canonical `market`. The shared `MarketContext` boundary is the only approved normalizer; direct raw `Statement.platform` to `FullOrder.market` joins are prohibited. Non-writing regression now proves that an identical MY/SG `order_id` reads only its own Statement evidence and Full Order row, and that the Full Order existence gate rejects the same ID from the other market in both directions. Current Full Order PostgreSQL history is MY-only: 14,456 Orders / 21,805 Items; no SG rows. Schema/business-data writes: 0.
- **MY Full Order PostgreSQL real-source runtime smoke — PARTIAL / CUTOVER BLOCKED (2026-10-27):** the Product Owner supplied `Order.all.order_creation_date.20260927_20261027.xlsx` (SHA-256 `314e99df…bd750f19`), which parsed as 2,629 MY Orders / 3,711 Items with zero PM issues. The explicit MY-only PostgreSQL Full Order backend atomically processed 460 NEW, 771 UPDATED, and 1,398 UNCHANGED Orders (661 inserted and 1,953 updated Item rows), with transaction-internal reconstruction readback. PostgreSQL now has 14,916 Orders / 22,466 Items; Sheets business-key parity is exact, and Statement counts remain 26,722 / 578,653 / 548 / 46 with SG still absent. A same-source replay reported 0 NEW / 0 UPDATED / 2,629 UNCHANGED. The Statement Full Order gate (8,557 committed MY ORDER identities), Full Order-backed date authority, and financial Product Summary grouping all replay correctly through PostgreSQL without Full Order Sheets reads. Cutover remains blocked because four pre-existing `resolve_sku` values differ from Sheets (`8717496900999` in PostgreSQL versus `5004182` in Sheets); none is present in the supplied source, so this task must not overwrite them from a Sheets fallback or mix the separate Compact Barcode workstream. No Google Sheets write, SG migration, Statement write, schema change, Git checkpoint, or deployment occurred. Required next action: separately authorize/provide the authoritative resolution for those four historical `resolve_sku` facts, then rerun final parity acceptance.

- **MY Full Order PostgreSQL runtime - LOCALLY VERIFIED (2026-10-27):** This supersedes the preceding partial/blocker status. The Product Owner approved current Google Sheets Full Order as authoritative for four historical `resolve_sku` values. A serializable exact-key PostgreSQL transaction corrected only `(shopee_my, 2606169B4UWA26, 3)`, `(shopee_my, 2606250FQ35NGU, 10)`, `(shopee_my, 2609046406GEH9, 0)`, and `(shopee_my, 260908ET48YAJV, 3)` from `8717496900999` to `5004182`, with locked-row readback proving all other columns unchanged. Final parity is 14,916 / 14,916 Orders and 22,466 / 22,466 Items, with no missing, extra, or business-field mismatches. Reconstruction, the 8,557-identity Statement Full Order gate, Analysis date authority, and Order Created Product Summary (20,950 items / Qty 31,768 / 429 groups) pass through the explicit MY PostgreSQL backend. The supplied source replays as 0 NEW / 0 UPDATED / 2,629 UNCHANGED. Statement remains 26,722 / 578,653 / 548 / 46 and SG Full Order remains 0 / 0. PostgreSQL selection provides Full Order reads/writes without Sheets fallback; Product Master Sheets remains read-only and explicit `google_sheets` remains separately available. Google Sheets writes, Statement writes, SG migration, schema changes, push, and deployment: 0.

### SHOPEE_MY_FULL_ORDER_USOLF_MIGRATION_CLOSED - FINAL ACCEPTANCE PASS / UNSTAGED

- Product Owner decision is closed: Shopee MY Full Order / All Order persists `Shopee_FullOrder_Current_Items.unit_price` from `UNIT PRICE USOLF` on the same deterministic Product Master row as NAV. For a conflicting MY Seller-SKU / Parent-SKU family, exact normalized source Variation may uniquely disambiguate that same Parent family; no fuzzy match or first-row selection is permitted. Shopee SG Full Order separately resolves exact normalized Product Name + Variation first, with Seller SKU only as exact-candidate disambiguation; NAV, `UNIT PRICE USOLF`, and `resolve_sku` come from that same SG Product Master row, with no `Price SGD` or MY fallback. Canonical Invoice/PDF, Statement, Weekly Billing, and schema semantics remain unchanged.
- Final read-only MY Current audit: 21,378 items; 21,355 deterministic valid-USOLF items; `PRICE_MISMATCH = 0`; `PM_UNRESOLVED = 0`; `PM_AMBIGUOUS = 0`; and the 11 Red Chili family price/identity mismatches are 0. The 23 blank/invalid-USOLF legacy items are Product Owner-approved historical exceptions and remain non-blocking. The 3 historical Cactus rows retain correct RM22.50 / NAV 5004108 values with accepted metadata-only resolved-SKU drift; no Current rewrite occurred.
- Final end-to-end acceptance: Order Created reporting directly matches every one of 19,977 non-cancelled Current items for Qty, persisted Unit Price, Original Sales, Amount, and Discount; Payout regression retains Statement batch `20261020035550-46d32fdd` at 463 orders / 190 products / Qty 1,007 / RM21,588.80 Original Sales / RM21,037.53 Amount / RM551.27 Discount. Focused Full Order, resolver, Consolidated, Payout/canonical, Statement, and Weekly regression: 278 passed; `py_compile` and `git diff --check` pass; `NEW_REGRESSION = 0`. MY/SG Current, canonical, Statement, Product Master, schema, and snapshot-hash writes: 0. Git stage/commit/push: 0; deployment: **NOT VERIFIED**. Next gate: `SHOPEE_MY_FULL_ORDER_USOLF_MIGRATION_CLOSED`.

### RESOLVE_SKU_V1 - IMPLEMENTED / TESTED / COMMITTED / PUSHED / ZERO BUSINESS WRITE

- Product Owner-approved Full Order Current schema evolution is one nullable text column named `resolve_sku`. It retains the complete Listing `Resolved SKU` string, remains separate from financial `resolved_seller_sku`, and does not add a status column. The Product Master source reads the named Listing header for both MY and SG while preserving raw multiple components, NAV/manual identifiers, hyphens, and separators.
- Full Order enrichment carries the Listing value without changing existing financial identity or USOLF/SG pricing. The Current repository persists the value for new rows, preserves every existing stored value (including blank) across reuploads and Listing edits, and never backfills historical rows. A source snapshot hash intentionally excludes this operational instruction so a Listing-only change does not cause a Current rewrite.
- The separately authorized live UAT2 extension is now read-only verified: `Shopee_FullOrder_Current_Items` has the exact 14-field header with `resolve_sku` at N1; the live Current reader accepts 14,422 orders / 21,767 items, with all historical `resolve_sku` values blank. This execution did not invoke the migrator or write schema/business data. The exact 13-column compatibility plan remains fail-closed code for only an unmigrated destination.
- A later real MY Full Order upload has now been traced read-only through the live September 2026 report path. The Baby Noodles row is persisted with the four-component Listing value, and a fresh current-to-summary-to-Compact build returns the four checksum-valid EAN-13 values with POS quantity 1 each while preserving Qty 1 / Amount RM43.12. The matching September rowset remains 293 products / Qty 4,336 / Original Sales RM92,736.30 / Discount RM4,210.45 / Amount RM88,525.85.
- Root cause of the observed Compact PDF was stale in-process Streamlit summary data: the 45-second cached Cross Platform reporting snapshot was not invalidated after a successful Full Order Current sync. The UI now clears that cache only after the repository sync returns successfully; fresh reads already propagated `resolve_sku` correctly. No grouping, amount, or barcode-parser calculation changed.
- Audit found and fixed one source-fidelity defect: the repository's generic text normalization trimmed leading/trailing whitespace from `resolve_sku` during an *updated* reupload. The field now has a narrow raw-text serializer/deserializer, so a nonblank Listing value (including spaces and newlines) is retained exactly; all financial field normalization is unchanged. New fixtures cover populated, blank, unavailable, and conflicting Listing mappings through the update path.
- The shared MY/SG parser preserves each original Compact Barcode PDF component: EAN-13 components may render a barcode and NAV/manual components require manual entry. Product Owner now confirms an operational-only equal-per-physical-unit allocation for multi-component POS handling: Original Sales and signed Discount are split deterministically to cents in Listing order, component Net is Price minus Discount, and the component totals reconcile exactly to the unchanged source financial table/Amount. No Product Summary, persistence, Listing, or legacy-report value is changed. Visual QA rendered the four-component MY page and found the barcode, POS Qty, allocated price/discount/net, source table, and footer legible without clipping.
- Live read-only Listing verification loaded both `shopee` (1,037 records / 976 nonblank Resolve SKU values) and `shopee sg` (649 / 613) through their market-bound source paths. An isolated MY and SG source-to-PDF trace preserved the raw value through Listing lookup, Full Order, 14-column storage/readback, and Compact PDF. Visual PDF QA covered valid EAN-13, -2/-6/-7 bundles, manual/NAV, nonnumeric and malformed values, multi-component and newline input, missing/invalid components, and MYR/SGD presentation without duplicated amount or clipping.
- Resolve SKU/Product Master/Full Order/Cross Platform/legacy Barcode/Order Created/Statement-Payout regression: 316 passed. The formerly failing unconfigured-SG test was an isolation defect caused by fallback to configured process `streamlit.secrets`; its fixture now explicitly isolates that fallback, and the market suite passes 24/24. `py_compile` and `git diff --check` pass. Checkpoint `5df8867a5ec8ed5bc3449024d57891fdb3652068` (`feat: complete resolve sku and compact barcode pdf integration`) is pushed and verified at `origin/feature/uat2-invoice-persistence-integration`. Deployment: **NOT VERIFIED**.

### SHOPEE_SG_LOCAL_UAT2_CONFIG - VERIFIED / ZERO BUSINESS WRITE

- Local ignored `.streamlit/secrets.toml` now has `markets.shopee_sg.uat2_data` with the dedicated SG spreadsheet `19DV6ubFyIrjJ-8lbNNqjn8P69yjSaCS0cUoNjEHZWGo` and reuses the existing MY UAT2 service-account file path. No credential contents are recorded in source control or Project Status.
- Fresh read-only settings checks confirm MY remains on its existing destination, SG resolves only to its dedicated destination, and SG does not resolve to MY. The service account can read all eight required SG tabs; each currently contains only its header row. A one-row in-memory SG Full Order review reached the SG Current repository and returned ready/new without a commit or Google Sheets write. Deployment remains **NOT VERIFIED**.

### SHOPEE_SG_DATABASE_ROUTING - IMPLEMENTED / UNSTAGED / ZERO BUSINESS WRITE

- Canonical `Invoice_Orders` / `Invoice_Items`, Full Order Current, and the shared Statement writer now take the explicitly bound MarketContext through their configuration boundary. MY uses its existing UAT2 destination; SG can use only `INV_SHOPEE_SG_UAT2_DATA_SPREADSHEET_ID` and its SG credential path. Missing context, unsupported market, missing SG configuration, or an SG attempt to use the default MY destination fail closed before Google Sheets I/O.
- SG Statement writer construction is destination-safe for the already bootstrapped SG tabs (`Statement_Data`, `Statement_Financial_Components`, `Statement_Summary`, and `Order_Adjustments`) and persists `platform = "Shopee SG"`; this does not expose an SG Statement upload/commit UI or write a business row. Monthly Statement and Billing remain **MY_ONLY_BY_DESIGN**.
- Product Master remains shared infrastructure but is passed the same explicit MY/SG context; SG Full Order uses exact `shopee sg` normalized Product Name + Variation evidence, with Seller SKU only as exact-candidate disambiguation and no `Price SGD` or MY fallback. Existing Full Order source detection and `(market, order_id[, item_index])` Current identity are unchanged.
- Mocked routing regression: 49 tests passed for market settings, canonical/Current/Statement factory destinations, Product Master market cache, Full Order market guard, and Full Order processing lifecycle. `NEW_REGRESSION = 0`; schema migration: 0; UAT2/SG/Product Master/Statement/canonical business writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. First real SG Full Order preflight remains pending a separately supplied SG source and live configuration verification.

### Shopee SG Full Order Source-Market Guard - IMPLEMENTED / UNSTAGED

- Full Order source market is now determined exclusively from the native `Country` column. Every populated order row must be MY or every row must be SG; mixed MY/SG is blocked as `MIXED_MARKET_FULL_ORDER_SOURCE`, while missing or unsupported Country is blocked before Product Master or Current persistence access. Filename, UI selection, SKU, shop name, and currency alone are not market authority.
- A valid cross-market workbook stops at `MARKET_MISMATCH` before any Product Master/Current write. The Upload UI identifies the current and detected markets and offers only `Switch to Shopee <market> and continue` or `Cancel`; confirmation retains the original bytes/hash and revalidates against only the detected market's Product Master and Current destination. No silent redirect or persistence occurs.
- The existing Current identity is market-scoped: Orders use `(market, order_id)` and Items use `(market, order_id, item_index)`; snapshot hashes include market. No schema migration was needed. MY and SG Current destinations remain separately configured and cannot fall back to one another.
- SG Current configuration is now locally available and read-only verified against its dedicated empty/current-header-only database. No real SG workbook was supplied, so a real source preflight and any SG Current write were not attempted. Product Master, canonical, Statement, and schema writes: 0. Deployment: **NOT VERIFIED**.

### SHOPEE_SG_FULL_ORDER_PERSISTENCE - VERIFIED / CHECKPOINT AUTHORIZED

- Product Owner's SG-specific identity rule is now explicit: exact normalized Product Name plus Variation is primary; Seller SKU may only disambiguate multiple exact candidates. Parent SKU, Price SGD, aliases, fuzzy matching, and first-row selection are excluded from this SG Full Order path. Explicit `FULL_ORDER_MATCH_ONLY` SG Listing rows participate in that exact identity set because they are the Product Owner's Full Order records, never a cross-market fallback. A success proves exactly one `shopee sg` Listing row and carries its `resolved_seller_sku`, NAV, `UNIT PRICE BASED USOLF` (recognized as the approved USOLF header alias), and `resolve_sku` together.
- The refreshed `shopee sg` Listing includes the required exact rows, including `FULL_ORDER_MATCH_ONLY` records and blank-SKU identities where exact Name + Variation remains unique. The completed live SG Full Order run validated and persisted to the dedicated SG Google Sheet; the former `Shopee_FullOrder_Current_Orders!A1` header-drift blocker is superseded by that verified live result. No additional schema migration is authorized or required for this checkpoint.
- Full Order now exposes `Refresh PM & Revalidate`: it clears the market-scoped PM cache, reloads only the source-owned MY/SG market, and replaces the retained review only after successful no-write validation. SG-only Google PM loading admits blank Listing SKU/Parent-SKU rows for exact Name+Variation proof, but a resolved SG row with neither same-row canonical Seller/Parent SKU or no `resolve_sku` is now `PM_ENRICHMENT_REQUIRED`; it is never persisted with an unusable identity.
- SG persistence is destination-isolated. The live upload verifies deterministic SG PM enrichment and a same-row `NAV`, `UNIT PRICE USOLF`, and `resolve_sku`; it has no `Price SGD` or MY/cross-market fallback. No Statement, canonical Invoice/PDF, Payout Product Summary, or Compact Barcode behavior changed in this checkpoint.
- Full Order consecutive-upload lifecycle is now explicit: after a completed sync, `Upload another Full Order` clears only the staged Full Order review, PM-refresh/mismatch/sync presentation, active batch ID, and Full Order uploader widget before returning to Upload; it preserves the selected SG market and Full Order source mode. A stale Full Order step at Validate or later without a typed review is normalized back to Upload, so the stepper cannot claim Upload `Completed` while the validation page has no review.
- Post-sync Full Order state now renders the verified sync result before consulting the staged review. A review cleared after a successful sync therefore remains an explicit success state with `Upload another Full Order`, while failed or partial syncs (which never set the result) remain non-success states.

### CONSOLIDATED_PRODUCT_SUMMARY_LEGACY_GOOGLE_SHEETS_MIGRATION - HISTORICAL / SUPERSEDED

- Baseline checkpoint: `7fecdc555967877fe520a9a101480ac3cd70ea6e` (`feat: finalize full order statement workflow`). Full Order integration is successful and closed.
- This historical Google Sheets checkpoint pre-dates the PostgreSQL Product
  Summary cutover. Its former Payout-Date `Invoice_Orders` / `Invoice_Items`
  contract is superseded and must not be read as an active runtime rule.
- Refund semantics are finalized as gross/original purchase reporting: `26100849V7RC9P` retains the target Current item at Qty 2 / RM23.22. Statement remains the financial/refund authority; no returned quantity is inferred.
- September validation is closed: Full Order gross product subtotal RM88,525.85 less confirmed refunds RM263.70 equals ERP staging net RM88,262.15; no known SKU, quantity, price, or missing-order discrepancy remains.
- May historical genuine-All-Order missing-only backfill is completed and accepted by the Product Owner. Existing Current rows are retained, historical data does not overwrite newer Current facts, and `UPDATED = 0` remains the contract.
- Closure regression: Weekly batch `20261020035550-46d32fdd` retains 463 orders / 190 products / Qty 1,007 / RM21,588.80 original sales / RM21,037.53 amount / RM551.27 discount. No unintended Statement, canonical, Product Master, or schema change is part of this migration. `NEW_REGRESSION = 0`.
- Product Owner authorized selective stage, commit, and push for this checkpoint. Git checkpoint `63e912450bb093e86f3b540955fb8875a8e3b7b6` (`feat: finalize consolidated product summary migration`) is committed and pushed to `origin/feature/uat2-invoice-persistence-integration`; local and upstream were equal after the normal push. Deployment: **NOT VERIFIED**.

### Platform Selector Refinement V1 — IMPLEMENTED / TESTED / REVIEWED / COMMITTED / PUSHED

- Weekly Billing and Monthly Billing now show exactly `Shopee MY`, `Shopee SG`, `Lazada`, and `TikTok`, without `All`. Platform and Statement Period/Month share one row. Shopee MY remains the default and retains its existing backend.
- Shopee SG, Lazada, and TikTok remain selectable but fail closed before any MY dataset, report, or export can be used. No SG, Lazada, or TikTok parser, persistence, Product Master, Statement, or billing backend was implemented.
- Consolidated Product Summary now shows exactly `Shopee MY` and `Shopee SG`; Date Type, Platform, From Date, To Date, and Date range share one row. `All`, Lazada, TikTok, Shopee SG/MY merging, and FX aggregation are not offered. Shopee MY is the predictable default; Shopee SG reads only its explicit SG committed-data configuration and does not fall back to MY. Empty SG data is a normal empty result.
- Consolidated Product Summary date filters default to `Order Created Date` and offer `Payout Date` as the alternative, with two explicit From/To mini-calendar controls, a visible `Current view` label, and a parallel `Date range` filter after To Date. It defaults to `Selected dates`; choosing `All dates` shows the first/last valid date as a disabled full-range pair. In selected-date mode, To Date only permits valid dates later than From Date. Both modes use inclusive ranges and only enable committed dates for the selected fact/platform. Order Created Date mode includes committed rows without payout evidence; Payout Date mode remains payout-evidence-only. Changing platform or Date Type clears stale selections, and the service rejects injected/stale endpoints before filtering.
- The UAT2 repository-creation boundary rejects Lazada/TikTok contexts, prevents a manually constructed Shopee SG context from retaining the default MY spreadsheet destination, and preserves valid Shopee MY repository construction. This does not enable an SG persistence workflow.
- The sidebar section is now `LIVE ANALYSIS`, ordered `Consolidated Product Summary`, `Shopee MY`, `Shopee SG`, `Lazada`, `TikTok`. Dashboard, ambiguous Shopee, and ZENXIN navigation entries are removed. Shopee MY preserves the existing internal Shopee analysis path; Shopee SG and TikTok render only fail-closed unavailable states.
- Data Import now presents TikTok instead of Zenxin Website. TikTok has no supported source type, no expected persisted platform, and no route to the historical ZENXIN parser/backend; every import workflow fails closed before Upload.
- Historical persisted `platform = "Shopee"` is mapped to the visible `Shopee MY` label without migration. TikTok has no ZENXIN fallback or shared backend. Existing legacy ZENXIN parser/data paths were not deleted or rewritten.
- Checkpoints: `996817ec30de5895c093fdaa3c91fc08f74bf695` (`feat: refine platform selection and routing`) and `b64f7a3a23e1ecd266a3dd8600a3f3e4919494a6` (`docs: record platform selector checkpoint`) pushed normally to `origin/feature/uat2-invoice-persistence-integration`; local, upstream, and remote were verified equal after push. Deployment: **NOT VERIFIED**.
- Correction verification: 38 Cross/UAT2/PDF tests, 9 Weekly/Monthly selector tests, 54 navigation/Data Import/TikTok-isolation/MY-routing tests, and 114 directly affected Billing/reporting service tests passed. The fixed-timeout Weekly full-app AppTest still timed out at 20 seconds. The Data Import state suite had 45 passed and the same observed 9 direct-session-state failures (one missing uploader and eight missing `Statement check required` assertions); no independent clean-baseline evidence was established, so they are not labelled confirmed baseline failures. All changed Python files passed syntax validation and `git diff --check` passed. UAT2, schema, Product Master, and business-data write deltas: 0. Deployment: **NOT VERIFIED**.

### Monetary Formatting V1 — IMPLEMENTED / TESTED / REVIEWED / COMMITTED / PUSHED

- Business-facing MYR values now use `RM`, thousands separators, and exactly two decimal places across Data Import, Manual Review, Statement/reconciliation presentation, Weekly Billing, Monthly Billing, Cross Platform, Product Summary tables, Excel reports, and monetary PDF/barcode reports. Missing values retain their existing unavailable representation and are not converted to zero.
- One presentation-only currency formatter owns string, Streamlit numeric-column, and Excel number-format conventions. MYR and SGD symbols are supported as formatting infrastructure only; no Shopee SG source, market-selection, persistence, or reporting flow was enabled.
- Streamlit/DataFrame source values remain numeric. Excel monetary cells remain numeric and use a currency/thousands number format; the Weekly/Monthly ERP Staging Data worksheet and diagnostic/machine CSV contracts retain raw numeric interchange values.
- No parser extraction, persisted schema/value, arithmetic, aggregation, tolerance, reconciliation, Product Master lookup, promotion allocation, or business total changed. UAT2, SG, Product Master, and schema write deltas are 0. Deployment: **NOT VERIFIED**.
- Review: no missed business-facing monetary formatting was found; Streamlit thousands formatting, numeric Excel cells, PDF monetary text, machine-interchange contracts, NAV identifier handling, and presentation/business-logic separation were confirmed. Optional PDF raster inspection is non-blocking.
- Git state: feature checkpoint `11bdf155b4adde530bb300c1c8e7892a4b72e580` (`feat: standardize monetary formatting`) and Project Status checkpoint `40f0f30620110c04c81d899a76de5b1f75b7ece2` (`docs: record monetary formatting checkpoint`) were pushed normally to `origin/feature/uat2-invoice-persistence-integration`; local, upstream, and remote matched after push. Deployment: **NOT VERIFIED**.

### Data Import Platform Entry — COMMITTED / PUSHED

- `Data Import` now opens a native 2×2 platform chooser for Shopee MY, Shopee SG, Lazada, and TikTok, then renders one shared five-step Data Import shell. Shopee market labels are text-only with no flag icon or Unicode flag. Back is left-aligned above the platform title as secondary navigation.
- All platform contexts expose Invoice Import, Weekly Statement, and Monthly Statement. Shopee MY retains its existing three workflows; Lazada admits only Invoice Import; Shopee SG now admits PDF/ZIP Invoice Import in test mode; TikTok has no enabled source path. Unsupported combinations remain visible and fail closed before Upload. TikTok is not backed by the historical ZENXIN parser or data.
- Invoice uploads now carry an explicit expected platform while retaining automatic detector validation. A detected mismatch becomes `PLATFORM MISMATCH`, reports selected and detected platforms, and contributes no order/product/review records to the active batch. New mixed-platform batches are not admitted.
- One active batch remains the contract. Back preserves it; choosing another platform presents Continue or the existing confirmed Discard lifecycle. The target context binds only after discard reset, which clears both `data_import.active_platform` and `data_import.active_market`.
- Shopee SG Invoice Import uses an explicit SG context and SG-only source/currency adapter over the shared Shopee core. It cannot reach MY persistence, Product Master, or Statement workflows and stops at Manual Review / Accepted Candidate without a commit control. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema delta: 0; deployment: NOT VERIFIED.
- Checkpoint: `e72725827921ef5f45127d4fe63518aab9428dfb` (`feat: add platform-aware data import entry`) pushed to `origin/feature/uat2-invoice-persistence-integration`; remote push: **VERIFIED**; deployment: **NOT VERIFIED**.
- Active-market platform-switch checkpoint: `3fe38c2fa774ed6d95c5dbe94996e2e4520ace2e` (`fix: handle active data import platform switching`) is **COMMITTED / PUSHED** to `origin/feature/uat2-invoice-persistence-integration`; remote push: **VERIFIED**; schema delta: **NONE**; deployment: **NOT VERIFIED**.

### Data Import Market Switching Fix — IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED

- Root cause: the sidebar treated `batch_id` as the active-batch fact, while the market guard treated a leftover `data_import.active_market` binding as proof of a batch. A no-batch chooser entry could therefore show `No active batch` and still raise `MarketStateIsolationError`; the old platform binder also set the UI platform key before the market guard could reject a conflict.
- `has_active_import_batch()` now makes nonblank `batch_id` the shared active-batch predicate for the sidebar and the MY/SG switch guard. A stale market binding is re-bound when no real batch exists; a real batch remains market-isolated. Platform binding now validates/binds its market before it updates the selected UI platform key.
- The chooser keeps its controlled Continue/Discard panel for real batch conflicts and intentionally translates any known unexpected `MarketStateIsolationError` at that boundary into a concise business warning. It does not suppress unrelated exceptions.
- Real local SG UI smoke used `26090462PGA4FE.pdf` from `D:\download material\30082026 to 05092026.zip`: Shopee SG entry opened, one PDF processed into one order/one product with no processing errors or market-isolation crash. This exercised only staging; no reconcile/commit or production write was performed.
- Verification: 145 focused market-context, Data Import UI, SG routing/currency/Product Master/Manual Review, MY parser/product/promotion/platform, real-sample, and financial-layout tests passed. Changed Python modules passed `py_compile`; `git diff --check` passed.
- Scope/persistence: SG remains PDF/ZIP test-only with no SG persistence, no MY fallback, and no UAT2 write. Shopee MY parsing and its existing import route are unchanged. Deployment: **NOT VERIFIED**.

### Platform Invoice — COMMITTED

Visible workflow: `1. Select Source -> 2. Upload -> 3. Validate -> 4. Reconcile -> 5. Review & Commit`.

- **Validate** owns source/PDF extraction, current-batch preview, source validation, Manual Review, supported source correction/revalidation, and re-upload/processing blockers. Manual Review is a hard gate.
- **Reconcile** owns Product Master/NAV validation and historical Invoice DB classification (`NEW`, `ALREADY_IMPORTED`, `SOURCE_CONFLICT`), including current-batch removal only. It never deletes historical rows.
- **Review & Commit** owns final readiness and guarded Invoice persistence.

`f989a8a` restored the five-step workflow; the rejected four-step description is stale.

### Shopee Statement / Reconciliation V2 — COMMITTED

- Native XLSX ingestion, internal validation, Reconciliation V2, and the Streamlit review path are implemented.
- Each order carries independent `ITEM`/`GROUP`/`UNRESOLVED` identity, merchandise, allocation, and `EXACT`/`EXPLAINED`/`NONE` settlement evidence. `GROUP` is valid when physical allocation cannot be proven.
- V2-aware commit fresh-reads Invoice data and Product Master under the shared lock, reruns V2, compares reviewed evidence, performs one logical atomic Google write, and verifies readback. Only `ITEM` may enrich a proven Invoice item; `GROUP` preserves Statement evidence without Item enrichment.
- Current schema: `Invoice_Orders` 44 columns; `Invoice_Items` 23; `Statement_Data` 40; `Statement_Financial_Components` 17; `Statement_Summary` 14.

The only complete end-to-end Statement acceptance corpus remains 2026-08-31 to 2026-09-06: 296 orders / 442 SKU rows, 138 `EXACT`, 158 `EXPLAINED`, 0 `NONE`, RM0.00 unexplained residual. It is not cross-week production proof.

### Shopee Statement Adjustment Source-Driven Canonical Typing - IMPLEMENTED / COMMITTED / PUSHED

- `Adjustment Type | Description` is now the deterministic source of canonical `adjustment_type`. Published `Return Refund Adjustment After Order Completed` retains the immutable `RETURN_REFUND_AFTER_ORDER_COMPLETED`; every other usable nonblank source description uses mechanical ASCII normalization only, never AI/fuzzy/keyword/sign/reason semantics.
- Raw `adjustment_description` and `adjustment_reason` remain separate source facts. `Statement_Data` retains every raw ADJUSTMENT row, while a structurally valid linked Adjustment also creates a canonical independent `Order_Adjustments` event. Linked order, complete date, amount, and Statement provenance remain fail closed.
- Real `26082480BKAV7A` evidence is `Return Refund Adjustment/Compensation`, `+137.24`, linked order `26082480BKAV7A`; dry-run commit-plan regression creates `RETURN_REFUND_ADJUSTMENT_COMPENSATION` and preserves its raw row. It is **REAL SOURCE / NOT COMMITTED / PRE-COMMIT REGRESSION VERIFIED**; no historical backfill is required.
- `Order_Adjustments` schema: **UNCHANGED / 21 COLUMNS**. Invoice facts, Product Summary, reporting, settlement authority, UAT2, SG, and Product Master are unchanged; write delta is 0 for each. Ghost UI remains **PAUSED / ROOT CAUSE NOT PROVEN**. Deployment: **NOT VERIFIED**.

### Shopee Statement Cross-Period Order Adjustment Evidence - APPROVED / COMMITTED / PUSHED

- Product Owner-approved rule: current Statement settlement remains independently reconciled to Invoice `order_income` under the existing component rules. When Invoice `final_amount - order_income` is non-zero, only committed canonical `Order_Adjustments` from a different Statement source may independently explain that final-amount delta.
- Checkpoint: `2da7181566e3c5f5accf1ce10624dbcb48aea2b1` (`fix: reconcile cross-period order adjustments`) is **COMMITTED / PUSHED** to `origin/feature/uat2-invoice-persistence-integration`; remote push: **VERIFIED**; schema delta: **NONE**; deployment: **NOT VERIFIED**.
- The V2 evaluator receives this evidence as an explicit read-only input from the fresh writer snapshot; it does not query Google Sheets. Exact Shopee platform and linked Order ID are required. Eligible canonical events must have unique fingerprints, preserved provenance, non-conflicted evidence, and an exact collective signed amount; missing, wrong, duplicate, conflicted, or non-exact evidence remains fail closed.
- Cross-period evidence is represented only in reconciliation effects. It is not added to current Statement released totals or financial components, does not rewrite Invoice source facts, and never changes Statement-period or adjustment source-batch ownership. The review currency hash includes the relevant canonical adjustment snapshot, so an evidence change blocks a stale commit with zero write.
- Schema delta: **NONE**. UAT2 data write delta: **0**. Weekly Billing, Monthly Billing, Cross Platform Summary, Shopee SG, Lazada, Zenxin, parser semantics, canonical typing, and fingerprint algorithms are unchanged. Deployment: **NOT VERIFIED**.

### Weekly Billing — COMMITTED

- Committed-data Shopee Statement-period report with Product Summary, Financial Summary, and Excel export.
- It uses the shared final rowset; it does not reparse or write UAT2 data.
- **Visible:** Excel export and `Export Product Summary Barcode PDF`.
- **Hidden:** the old `Export Barcode PDF` button (`8d79888`).
- Barcode output preserves final-row order. Valid EAN-13 renders vector barcode plus digits; an invalid SKU remains verbatim with `Barcode unavailable` and is never guessed or omitted.

### Monthly Billing — COMMITTED / PUSHED

- Architecture: **CONSERVATIVE MONTHLY ADAPTER**. Weekly Billing remains unchanged as the operational/debug path; Monthly is a distinct committed full-calendar-month projection. Cross Platform remains unchanged and payout-completed-date based.
- Source: reads exactly `Invoice_Orders`, `Invoice_Items`, `Monthly_Statement_Data`, `Monthly_Statement_Financial_Components`, and `Monthly_Statement_Summary`; Monthly schemas remain **34 / 17 / 14 / UNCHANGED**. No Weekly Statement tab is read for Monthly Billing.
- Shared stable business behavior: canonical Product Summary identity, promotion allocation, Shopee MY Staging Data, Financial Summary controls, three-sheet workbook, and final-row barcode rules. Weekly core/service/UI/domain/Staging/export entry points remain unchanged.
- UI: separate `Monthly Billing` navigation, `monthly_billing_*` widget/session namespace, isolated cache keyed by market, spreadsheet identity, and Monthly source contract; the on-page Staging Data section is intentionally hidden while the unchanged Excel workbook retains its Staging Data worksheet; Excel uses `Monthly_Billing_YYYY-MM.xlsx`; Barcode PDF names and source wording are Monthly-specific.
- August Monthly: **BUILD VERIFIED**. After the Product Owner's external UAT2 repair, a read-only 2026-09-29 snapshot finds exactly one Shopee `Invoice_Orders` row and one canonical `Invoice_Items` row for `260823596770U3`; the committed August Statement still has its `ORDER` and `SKU` rows. The adapter resolves the target and builds 2,775 Orders / 4,010 Items / 320 Product Summary rows with all four native financial controls passed. No application rule changed and no incomplete preview or inferred Invoice fact was used. **Monthly Statement referential-integrity commit gate = FOLLOW-UP AUDIT REQUIRED**; it is outside this Billing implementation.
- Monthly checkpoint: **ac33d6385f2a7ad20e4835c3e87826613d48b872** (`feat: add monthly billing adapter`), pushed to `origin/feature/uat2-invoice-persistence-integration`; Remote push: **VERIFIED**. Deployment: **NOT VERIFIED**.
- Weekly Billing: **UNCHANGED / REGRESSION VERIFIED**. Cross Platform: **UNCHANGED**. Schema / persistence: **UNCHANGED**.
- Monthly revision/superseding policy: **UNRESOLVED / FAIL CLOSED** for distinct committed sources in one month. SG Monthly: **OUT OF SCOPE / FAIL CLOSED**.

### Consolidated Product Summary — COMMITTED, LIVE, READ-ONLY

- Live report over one selected market's committed `Invoice_Orders` and `Invoice_Items`; it creates no reporting table and makes no UAT2/schema writes.
- Current adapter scope is committed Shopee MY or Shopee SG rows with the selected date fact. **Order Created Date** is the default and may include rows without payout evidence; the **Payout Date** alternative remains payout-evidence-only. Platform and inclusive selected-date From/To filters apply before allocation and aggregation. Markets remain separate; no MY fallback or FX aggregation exists.
- **Order Created status eligibility is shared for Shopee MY and SG.** Both use the exact normalized MY predicate: only `To Ship` and `Cancelled` are excluded; `Canceled` and unknown statuses retain the existing inclusion behavior. No SG-specific status branch or allowlist is permitted.
- One final rowset drives dashboard, ten-column Product Summary table, and `Consolidated Product Summary Barcode Table PDF`.
- Compact Barcode PDF V1 is implemented and unstaged as an additional reusable portrait POS checklist. Its Cross Platform adapter consumes that same already-filtered final rowset without querying, regrouping, or writing; the pure renderer has no Streamlit, database, Product Master, or network dependency. The existing Barcode Table PDF remains unchanged. Terminal positive numeric SKU suffixes are Product Owner-confirmed bundle multipliers (`Report Qty × suffix = Scan Qty`); non-numeric suffixes remain non-bundle. Negative discount on a normal row shows an override only when Decimal two-decimal multiplication exactly reproduces Amount; bundle rows retain the source bundle price and show `BUNDLE PRICE REVIEW REQUIRED`.
- Canonical grouping: **NAV + resolved Seller SKU + persisted historical Product Master Unit Price**. Product Name and Variation are display facts only and do not split rows. Same SKU/price with different NAV is separate. Missing NAV blocks the report; never show `N/A`.
- Visible page: filters, dashboard, Product Summary, Consolidated Product Summary Barcode Table PDF, and Export Compact Barcode PDF. Old All Products, All Manual Review, Missing SKU, Excluded, and session-detail panels are not reachable.
- Columns: No., SKU Code, NAV, Description, Qty, UOM, Unit Price, Original Sales, Disc Amt, Amount. UOM is `EA`; no columns are intentionally pinned.
- Dashboard uses that exact final rowset: count, quantity, original sales, signed discount, and amount. Discount is neither absolute-valued nor clamped.
- Cross Platform Payout Calendar V2 checkpoint: restored the native-style filter layout for `Platform` / `From Date` / `To Date`. Each date field opens its own evidence-constrained popup calendar. The later Date Type / Date range enhancement preserves range filtering, with `All dates` showing the complete disabled range rather than allowing calendar selection. When the current supported platform scope has no valid payout date, both calendars remain disabled and the date-selection area explicitly says `No payout dates are available for the selected platform.` The local Streamlit Components v2 implementation adds no dependency and performs no UAT2/schema write. Checkpoint `140ec85c02e0cde79eb71cd2c4cf5c3633f9a031` was preserved through merge commit `02cfef82a49870a14cdec9aa1c9a7e6a06bee363` and is **COMMITTED / PUSHED** to `origin/feature/uat2-invoice-persistence-integration`. Local validation is not deployment verification; deployment remains **NOT VERIFIED**.

### Shopee MY Live Analysis V1 — IMPLEMENTED / TESTED / COMMITTED / PUSHED

- `LIVE ANALYSIS -> Shopee MY` now reads one cached, read-only snapshot from the existing historical repository over committed `Invoice_Orders` and `Invoice_Items`, restricted to persisted `platform = "Shopee"`. It no longer uses current-session orders/products/reviews for this route and calls no repository write method.
- The page implements one explicit Apply Filters row with `Order Created Date` / `Payout Date`, created-date `All` / `Released` / `Pending / No Payout Evidence`, payout-date forced Released semantics, calendar Day/Week/Month grouping, and Top 5/10/20. Its From/To controls reuse the Cross Platform evidence-constrained calendar: unavailable days stay visible but disabled, available endpoints reflect the current basis/status scope, stale endpoints clear when that scope changes, and the report still applies the ordinary inclusive interval. Pending means only that no payout evidence is currently recorded.
- Sales KPIs include `NORMAL_ORDER` only: Total Sales = `final_amount`, Orders = distinct Order ID, Units Sold = matching item quantity, and AOV = Total Sales / Orders. `CANCELLED_ORDER` is excluded. `RETURN_REFUND` contributes only the positive display magnitude of signed `refund_amount`; it is not subtracted from Total Sales. Unknown layouts fail closed and are surfaced as excluded.
- Product Sales uses matching NORMAL_ORDER `Invoice_Items.line_subtotal`, deliberately distinct from order-level Total Sales. Product grouping reuses the canonical `NAV + resolved Seller SKU + persisted historical Product Master Unit Price` identity. Sales Trend and Sales vs Orders use real current-period Day/selected-period Week/Month labels; prior Sales Trend metadata retains its own true date range. The refreshed, calm dashboard presentation contains a compact analysis-context strip, five integrated current/previous KPI cards, one deterministic KPI-relationship insight, two chart cards, then equal-width Best Selling Products and Most Sold Products ranking cards. Each rank is a visual list with full-name/SKU-NAV evidence, a text-free normalized bar, and the actual Sales (RM) or Quantity; no DataFrame or normalized ratio/percentage is exposed. Sales Concentration/donut, Product Movers, and standalone Payout Coverage are not rendered. This is presentation-only: filters, data sources, KPI formulas, product identity, and aggregation remain unchanged.
- Current checkpoint verification: 109 focused Live Analysis service/UI/navigation/current-batch/identity tests passed under the app `.venv`; changed Python modules compiled and working/staged `git diff --check` passed. Focused page tests confirm ranking cards render without a DataFrame and retain actual RM/unit labels rather than normalization ratios. Checkpoint `69d320cf66585929525b71fe38c0900290546fcb` (`feat: checkpoint shopee my live analysis`) is pushed to `origin/feature/uat2-invoice-persistence-integration`. UAT2, Product Master, other business-data, and schema write deltas: 0. Deployment: **NOT VERIFIED**.
- Product Owner accepted this pre-shared-engine checkpoint with the current price-sensitive identity intact: the September Payout Date / Released RM1,037.40 `9555208106944-6` / `3000573` row is one complete RM39.90 historical-price identity; its RM576.00 RM36.00 counterpart remains a separate identity. No display-level merge or identity-rule change is approved in this checkpoint; any future reporting-identity decision belongs to the shared-engine work.
- Latest presentation isolation fix: removed the Shopee MY page's broad `[data-testid="stButton"]` CSS selector, which had applied its top margin to the shared sidebar navigation after that page rendered. The page no longer targets shared sidebar buttons; data, filters, formulas, repository behavior, UAT2, and schema remain unchanged.

### Shopee SG — Architecture Foundation COMMITTED / PUSHED; PDF Test Entry Implemented / Persistence Fail Closed

- One InvoiceGather application uses shared workflow engines plus market-specific configuration, adapters, providers, and data sources. Data Import has an internal platform selector and retains the five-step workflow.
- Existing persisted `platform = "Shopee"` remains Shopee MY; no rename migration is approved. New SG persistence uses a distinct `"Shopee SG"` identity.
- **SG destination and configuration: PENDING.** MY and SG require separate destinations; no current application path may infer or reuse MY configuration for SG.
- **SG Product Master configuration: IMPLEMENTED / LIVE SOURCE VERIFIED LOCALLY.** The SG context binds the approved spreadsheet `1tZN0lHSY2omOUycXBss6sim9iVBwAvOe0_DJNIPMwMM`, exact `shopee sg` tab, and `Price SGD`. It may reuse only the existing read-only credential infrastructure; it cannot inherit the MY workbook, tab, rows, prices, or NAV. Missing credentials/access/tab/required headers or invalid `Price SGD` fails closed. Deployment remains **NOT VERIFIED**.
- **SG real Invoice source evidence: AUDITED / TEST ENTRY ENABLED; Statement source support remains pending.** The ordinary-order PDF corpus proves the shared parser/financial core only behind the SG source/currency adapter; exception layouts require separate evidence.
- Market context must bind repository/writer destination, commit lock, Statement population, Product Master source, currency, and reporting provider. MY↔SG reads, reconciliation, commits, and fallback are prohibited.
- Product Master infrastructure is shared but tabs/data scopes are separate. SG Unit Price and NAV must come from the same resolved SG Product Master row.
- Currency is MYR for MY and SGD for SG. No FX conversion, cross-currency aggregation, or combined financial totals are approved.
- Consolidated Product Summary may select Shopee SG as an independent committed-data market. It uses persisted `Shopee SG`, configured SG storage, SGD presentation, and no MY fallback, merge, or currency conversion.
- SG Product Summary reuses `NAV + resolved Seller SKU + persisted historical Product Master Unit Price`; Product Name/Variation remain display-only, missing NAV blocks, and different NAV values remain separate.
- Weekly Billing will evolve to a shared shell with market-specific providers for committed data, Statement population, currency, Product Master, Product Summary, and exports.
- Current code audit found reusable platform-neutral repository/schema and Product Summary primitives, but MY coupling remains in parser output, historical bundle creation, Statement reconciliation/writer keys, Weekly Billing filters/UI currency, one UAT2 settings object, one Product Master source selection, and unscoped Streamlit workflow state.
- **Current blockers to permanent SG support:** a separately approved SG destination/persistence acceptance contract, SG Statement evidence, and cancelled/refund/return evidence. Live Invoice Product Listing reconciliation is no longer blocked locally.
- **Next enablement prerequisite:** Product Owner accepted the local V1 flow and checkpoint `71cf1949da11005c5a3113a8c86106373be3bed0` is committed/pushed. A separate decision is still required for any SG destination/persistence work; permanent write controls remain unavailable. Preserve MY behavior and data unchanged.
- **Phase 1 foundation: COMMITTED / PUSHED (`9f67f4e`).** Immutable Shopee MY/SG market contexts provide persisted platform identity, MYR/SGD display metadata, Cross eligibility, state namespace, and capability availability. MY remains `"Shopee"`; SG is `"Shopee SG"` and permanently Cross-ineligible.
- UAT2 and Product Master configuration factories now accept an explicit market context. MY no-argument callers retain the existing configuration path. SG requires an explicit market-specific configuration block and otherwise raises a clear unavailable error; it cannot inherit MY spreadsheet, credentials, Product Master, or cache identity.
- Statement writer now binds spreadsheet and persisted-platform targets together, rejects a plan/adjustment for another platform before I/O, and continues to use `"Shopee"` for MY. SG Statement commit and Weekly Billing remain deliberately unavailable even if a future SG spreadsheet is configured.
- Weekly Billing has a context/provider seam with the current MY path as its default; no SG selector or fake SG dataset exists. Cross source eligibility is now an explicit MY persisted-platform allowlist, not UI-only behavior.
- Verification: 132 focused tests passed across the market foundation, existing UAT2 repository, Product Master, Statement writer, Cross Platform, Weekly Billing service/UI; `py_compile` passed. UAT2 writes 0; schema delta 0.

### Shopee SG Data Import V1 Complete Reconcile Entry — LOCALLY COMPLETE / PRODUCT OWNER ACCEPTED

- Current Batch monetary presentation now derives from the active MarketContext. SG KPI cards, order-table numeric columns, Manual Review formula/price evidence, and promotion evidence use `S$`; MY retains `RM`. Numeric source/domain values are unchanged.
- The approved live SG Product Listing is bound to spreadsheet `1tZN0lHSY2omOUycXBss6sim9iVBwAvOe0_DJNIPMwMM`, exact tab `shopee sg`, and `Price SGD`. The application reused the existing read-only Google credential only; no MY workbook/tab/data fallback exists. A fresh live read returned 685 worksheet rows, 410 priced candidate records, and 0 invalid prices.
- The SG Manual Review KPI remains `Pending` until Product Listing resolution successfully evaluates. After evaluation it shows the authoritative numeric count. Source failure remains fail-closed and does not falsely mark evaluation complete.
- Fresh **LIVE GOOGLE SHEET** corpus run through the Streamlit Data Import path: 157 PDFs processed, 157 orders structurally/financially accepted before Product Listing resolution, 199 product rows, 0 unsupported files, and 0 processing errors. Live resolution produced 69 exact SKU/Parent-SKU rows, 65 exact Name/Variation rows, 65 unresolved rows, 0 ambiguous/conflict rows, 10 incomplete-NAV rows, and 17 price-reference mismatches. The overlapping issue union produced 63 Manual Review orders and 94 Accepted Candidate orders; Reconcile returned those review records to Validate as designed.
- Ordinary invoice `26090462PGA4FE.pdf` proceeded through Upload, Validate, live Reconcile, and test-only Review: 1 order / 1 product / 0 reviews, Product Listing evaluation true, no source-unavailable error, no exception, and no commit control. Review explicitly states no SG `Invoice_Orders` / `Invoice_Items` write and no MY fallback.
- Focused tests completed before the Product Owner requested no further test reruns: 8 passed across SG source/configuration, currency-table/KPI/review presentation, and pending-to-evaluated Manual Review behavior. The subsequently started broader regression was interrupted at the Product Owner's request and has no final result. The Product Owner manually tested and accepted the V1 UI behaviour. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema delta: 0; Git stage/commit/push delta: 0; Deployment: **NOT VERIFIED**.

### Shopee SG End-to-End Parser Dry Run V1 — READ-ONLY AUDIT / NO IMPLEMENTATION

- Audited `D:\download material\30082026 to 05092026.zip`: 157 SG Order Detail PDFs (30 Aug–05 Sep 2026), all with `seller.shopee.sg`; all 157 had extractable text, Order ID, and status. The 199 parsed product rows had 69 visible source Seller SKUs, 130 source-blank Seller SKUs, 0 source GTINs, 44 explicit Variations, and 155 blank Variations.
- The user-supplied `shopee sg` Google Sheet tab was the only Product Listing source read. It contained 684 valid listing records and the populated headers `Product ID`, `Product Name`, `Variation ID`, `Variation Name`, `Parent SKU`, `SKU`, `NAV CODE`, `Price SGD`, `UOM`, and `USOFT product description`; it exposes no GTIN field. MY listing data was not read and no MY fallback was used.
- Original, unmodified SG PDFs contain `$` and no `RM`. The current MY parser was run unchanged on representative normal, promotion, and multi-item files: platform detection succeeds, but `$` causes `INCOME_EXTRACTION_MISSING`; source-blank Seller SKU samples are instead emitted as `SKU_RESOLUTION_REQUIRED`. These outcomes are current-MY-rule evidence, not a claim that the SG source is malformed.
- An in-memory SG-only dry run adapted the currency token solely for existing parser-contract evaluation; it did not edit production code. It produced 157/157 structural passes and 157/157 financial passes. Shipping subtotal, fees-and-charges, order income, and final amount each balanced for all 157 files. No return/refund/cancelled SG sample was present, so those layouts remain **NOT VERIFIED**.
- Under the Product Owner-specified deterministic SG hierarchy (exact source Seller SKU; no GTIN available; normalized exact Product Name + Variation; normalized unique Product Name only; no Parent-SKU or MY fallback), 168/199 item rows resolved: 23 by Seller SKU, 19 by Name + Variation, and 126 by unique Name. Thirty-one rows remain unresolved and require SG Product Master action; 15 resolved rows have a PDF price different from `Price SGD` and require price review. Forty-two rows had a visible source SKU that did not match listing `SKU` but did have a unique permitted description match; this is a source/listing identity-alignment issue, not a Parent-SKU match.
- Promotion fixture `260830NYW6C5MM`: `Any 3 at $15.80`, quantity 3, listed/reference unit price $7.90, source group total $15.80, merchandise subtotal $15.80, and final order income $9.71 all reconciled. Multi-item fixtures `260831R9P3EADV` and `2609058JNHGAHA` both reconciled financially; their remaining product identity gaps are documented in the audit report.
- UAT2 write delta: 0. SG business-data write delta: 0. Product Master write delta: 0. Schema migration delta: 0. Production-code delta: 0. Git staging/commit/push delta: 0. Deployment: **NOT VERIFIED**.

### Shopee SG PDF Upload Test Entry V1 — IMPLEMENTED / TESTED / UNCOMMITTED

- Data Import now admits Shopee SG PDF/ZIP Invoice Import through the shared five-step shell. Routing requires explicit `SHOPEE_SG` context plus `seller.shopee.sg`; `$` alone is insufficient. An SG-only adapter normalizes source money tokens for the shared Decimal/financial engine without broadening Shopee MY `RM` behavior.
- Source-blank Seller SKU is a valid parsed/financially-valid state and proceeds to SG Product Listing resolution. SG source SKU is matched against the exact union of listing `SKU` and `Parent SKU`; source-blank identity may use deterministic exact Product Name/Variation rules. Ambiguous, absent, incomplete NAV, and source-vs-`Price SGD` mismatch evidence becomes explainable Manual Review. No fuzzy, first-row, price-guess, or MY fallback exists.
- Runtime Product Master is restricted to a separately configured Google Sheets source using the exact `shopee sg` worksheet and `Price SGD`. Current local runtime configuration remains absent and therefore fails closed before resolution. Permanent SG persistence is not exposed; Review shows test candidates only and no SG or MY repository is created or written.
- Real corpus run: 157 PDFs discovered, 157 structurally parsed, 157 financial passes, 0 financial failures, 199 product rows, 0 unsupported, and 0 processing errors. The known `Any 3 at $15.80` fixture preserved quantity 3, source unit price 7.90, raw line multiplication 23.70, promotion/source group total 15.80, and passed the shared financial checks.
- Separate read-only listing diagnostic used the accessible local export of the supplied `shopee sg` tab, not the unavailable live application Google configuration: 69 exact SKU/Parent-key matches, 39 exact name/variation matches, 91 identity-unresolved rows, 0 ambiguous/conflict rows, 10 listing rows incomplete for NAV, 98 fully resolved rows, and 17 source/`Price SGD` mismatches. At order level the union produced 88 Manual Review candidates and 69 Accepted Candidates. The export yielded 408 valid `Price SGD` records and must not be represented as a live/fresh Google read.
- Verification: 141 focused SG routing/currency/Product Master/Manual Review/test-only UI plus MY parser/product/promotion/layout regressions passed; changed Python modules passed `py_compile`; `git diff --check` passed. A broader refund suite additionally reached 150 passes but had 1 failure and 4 setup errors solely because its recorded historical PDF paths are absent, so it is not counted as a clean pass. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema delta: 0; Git stage/commit/push delta: 0; Deployment: **NOT VERIFIED**.

### Platform Invoice Manual Review Ownership Bug — RUNTIME VERIFIED / COMMITTED / PUSHED

- This defect predates Shopee SG Phase 1. The SG market-isolation checkpoint did not change the affected Platform Invoice Validate/Reconcile code paths.
- A successful `Apply & Revalidate` removes the selected record from authoritative `session_state["reviews"]`, reinserts the corrected Accepted order/products through `apply_batch_rules()`, and invalidates the historical staging entries, refresh flag, and signature. Corrected Product Master identity evidence remains resolved in the existing focused service test.
- The ownership defect begins in Reconcile: `_surface_reconciliation_product_identity_reviews()` can discover a previously unchecked Product Master `PRICING_CONFLICT` or `PRICE_CONFIRMED_IDENTITY_AMBIGUOUS`, move the Accepted source into one new `PRODUCT_MASTER_IDENTITY_RESOLUTION_REQUIRED` review, and invalidate historical state. `_render_reconciliation_step()` then continues instead of returning the workflow to Validate.
- Historical staging consumes that newly created review. `build_current_batch_staging()` projects it as `NEEDS_REVIEW` with `Manual Review source remains in the current batch`, and the Reconcile exception queue displays it as a non-NEW blocker. This is a derived symptom, not a separate historical-classification rule.
- The `Requires Re-upload` / `Online Resolution` tab labels are emitted by one Validate renderer from the current authoritative reviews. The observed `6/0` to `6/1` change is consistent with Reconcile adding one new online-resolution review between renders. `upload_result_summary` is a separate stale action-time projection and must not be treated as current Manual Review authority. No second server-side renderer of the same split labels was found; a truly simultaneous duplicate block would still require a captured browser reproduction.
- Implemented invariant: one blocker, one authoritative owner, one visible resolution path. Reconcile now receives the explicit count from `_surface_reconciliation_product_identity_reviews()`. When it creates a Product Master identity review, the existing session-only invalidation is already complete; Reconcile selects the online-resolution presentation context, returns to Validate, and stops before historical staging or exception rendering can run. The next Reconcile pass recomputes from the newly revalidated authoritative state.
- `_current_import_result().source_specific_details["manual_review"]` now contains only actual Manual Review records. `upload_result_summary` remains a latest-upload audit projection only; it is not current readiness or current-review authority.
- Current status: RUNTIME VERIFIED / COMMITTED / PUSHED (`256570f`). Duplicate-presentation work did not change Reconcile-to-Validate ownership, historical staging/classification, or the Product Master identity transition.

### Platform Invoice Validate Duplicate Presentation â€” RUNTIME VERIFIED / COMMITTED / PUSHED

- Primary Validate `Needs Attention` now derives only from explicit blocking semantics. A current-batch Duplicate Order remains non-blocking, does not change forward readiness, and appears in `Skipped / Non-blocking information` with its original evidence and optional safe duplicate-removal action.
- The queue uses the existing authoritative duplicate projection and recovery service; it adds no duplicate store, does not create a historical status, and preserves the existing Weekly Statement non-blocking duplicate recovery path in its secondary notes.
- Verification: 156 focused tests passed across Validate recovery/presentation, ImportResult contract, Manual Review/Product Master ownership, workflow controls, and Shopee SG isolation/Statement writer/Cross/Weekly suites. Changed Python modules passed `py_compile`; `git diff --check` passed. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema migration delta: 0. No Git operations were performed.

### Global Faded / Ghost Streamlit UI â€” AUDITED / ROOT CAUSE NOT YET PROVEN

- The top-level `if / elif` router emits exactly one page renderer per completed run, and AppTest retains one authoritative current tree after both a Manual Review count transition and Reconcile-to-Validate handoff; no server-side duplicate page/tab renderer was found.
- **Proven identity churn:** Manual Review calls stateful `st.tabs(..., key="manual_review_tab_widget", on_change="rerun")` with labels and default derived from live counts. In local Streamlit `1.61.1`, stateful tabs compute their element ID from the `tabs` tuple and `default` with `key_as_main_identity=False`; therefore `6/1 -> 6/0` changes the element ID even with the same explicit key. The dedicated Reconcile-to-Validate handoff also deletes that widget state immediately before `st.rerun()`.
- **Manual Review count visibility: IMPLEMENTED / COMMITTED / PUSHED (`e1e3823`).** The stable exact tab labels remain `Requires Re-upload` and `Online Resolution`; one compact caption immediately above the tabs now shows both counts from the authoritative current `reviews` partition. The keyed tab widget, focus keys, review ownership, Apply/Revalidate behavior, and Reconcile-to-Validate widget-state reset remain unchanged.
- The corresponding AppTest probe keeps exactly two current tabs through an initial `6/1` state and ten reruns after `6/0`, while the simultaneous above-tab caption changes from `Requires Re-upload: 6 / Online Resolution: 1` to `6 / 0`; focused Manual Review, ownership, marker-only refund, and SG-isolation coverage passed.
- Navigation to Weekly Billing/Cross renders its title before the committed-data read, so it can expose Streamlit's normal stale-previous-tree visual state while data loads. Browser DOM, normal/hard-refresh behavior, and deployed Streamlit version were not capturable: the local app is running on `8501`, but no DevTools endpoint or Selenium/Playwright automation is available. No dependency, CSS, tab, session-clearing, sleep, reload, UAT2, SG, Product Master, or schema behavior changed.
- **Ghost UI: PAUSED / ROOT CAUSE NOT PROVEN.** Current classification remains **INSUFFICIENT EVIDENCE** for the exact visual ghost mechanism; the strongest suspect remains **proven Manual Review widget identity instability**. This count-visibility adjustment does not claim to remediate the ghost. Runtime Product Owner verification is required before separately considering the widget-state-deletion-and-rerun candidate.

### Shopee MY Pending Return/Refund Financial Layout — RUNTIME VERIFIED / COMMITTED / PUSHED

- Product Owner rule: a product-level `Return/Refund` marker alone is an operational pending-return fact, not completed refund financial evidence. With no explicit non-zero Refund Amount or Reverse Shipping Fee/SST, the original Invoice remains `NORMAL_ORDER` and normal validation must run. A completed post-order Adjustment remains a separately validated event.
- Real source `260910M4M573RT.pdf` reproduces the defect exactly: Delivered; three reliable product markers with return/refund quantities `1`, `1`, and `2`; Merchandise Subtotal/Product Price `55.45`; Estimated Order Income/Final Amount `42.44`; no Refund Amount; no Reverse Shipping Fee/SST; explicit `No adjustment has been made to this order yet`; no parsed Adjustment event. Current signals are exactly `{return_refund_marker}`.
- Root cause: `classify_invoice_financial_layout_from_signals()` treats every non-empty signal set lacking non-zero `refund_amount` plus another signal as `UNKNOWN_OR_MIXED`. Review policy then emits `FINANCIAL_LAYOUT_UNRESOLVED`; `resolution_plan()` has no safe plan for that code, so Validate presents Requires Re-upload.
- Read-only NORMAL_ORDER simulation found no second blocker: product count/structure, promotion evidence, required income fields, product totals, seller financial reconciliation, Final Amount/Order Income, and no-adjustment validation all passed. Read-only live MY Product Master lookup matched all three SKUs with nonblank NAV; complete revalidation returned no error.
- Implemented exactly one classifier exception: `signals == {return_refund_marker}` now returns `NORMAL_ORDER`. Signal extraction remains unchanged, so a non-zero Refund Amount plus marker or Reverse Shipping Fee/SST remains `RETURN_REFUND`; Refund Amount-only and Reverse Shipping-only remain guarded as `UNKNOWN_OR_MIXED`. No fourth layout enum was introduced.
- Fresh real-source verification: `260910M4M573RT.pdf` retains `{return_refund_marker}`, now classifies as `NORMAL_ORDER`, has no financial-layout review, parses three products, has no completed Adjustment event, and completes local full revalidation with no error. It no longer enters Manual Review or Requires Re-upload for financial layout.
- Regression coverage: the full signal matrix and marker-only revalidation are explicit. Completed Adjustment corpus/upload regressions now assert normal original layout while retaining source date/reason/amount/Final Amount checks; adjustment conflicts and missing evidence remain blocked. Focused parser/layout/Adjustment/Product Master revalidation coverage: 66 passed (5 historical-PDF tests deselected because their archived files are unavailable); focused Validate ownership/presentation and SG isolation coverage: 21 passed. The five missing historical refund PDFs are a known test-infrastructure gap, not a classifier failure. UAT2 writes: 0; SG writes: 0; Product Master writes: 0; schema migration delta: 0; Git: `760f35b` committed and pushed to `origin/feature/uat2-invoice-persistence-integration`.

### Shopee MY Cancelled Order V1 — IMPLEMENTED / TESTED / MANUALLY VERIFIED / PERSISTENCE VERIFIED / COMMITTED / PUSHED

- Product Owner rule: a genuine Shopee cancelled order is valid historical Invoice evidence. `Cancelled` and `Canceled` normalize to `order_status = Cancelled`; explicit cancellation status selects `invoice_financial_layout = CANCELLED_ORDER`. Cancellation is never inferred from zero/refund markers, and the known `260910M4M573RT` marker-only return remains `NORMAL_ORDER`.
- Cancellation V1 validates the approved current format fail closed: original item subtotals equal Product Price; explicit negative `Cancelled Amount` offsets Product Price to zero Merchandise Subtotal; source Order Income and Final Amount are zero. Missing, positive, or conflicting cancellation evidence remains Manual Review. `Cancelled Amount` is staging-only validation evidence, not a schema/fingerprint field.
- Real source `D:\download material\cancel pdf.pdf` / `260908G952UC0W` now parses as one Accepted `Cancelled` / `CANCELLED_ORDER` order with Product Price RM77.20, Cancelled Amount RM-77.20, zero Merchandise Subtotal/Estimated Order Income/Final Amount, and three original Invoice Items totaling RM77.20.
- Existing `Invoice_Orders` / `Invoice_Items` persistence is unchanged and preserves original quantities/amounts. Weekly and Monthly retain exact Statement Order-ID selection while excluding cancelled parent items before Product Summary aggregation; Cross Platform applies the same status-or-layout exclusion at its committed-data eligibility boundary.
- Integration re-verification: the actual Data Import AppTest path (Shopee MY -> Invoice Import -> upload `cancel pdf.pdf` -> Process files) reaches `process_pdf_file_with_outcome` then `append_batch_results_with_metadata` and returns 1 Accepted order / 3 products / 0 reviews. The only review-policy route for `CANCELLED_ORDER` is the cancelled-order validator; batch append performs identity/basic-row checks only and does not re-run monetary reconciliation. The exact generic normal-layout error is therefore unreachable from the current recognised cancellation route. Verification: 118 focused Data Import UI/layout/real-sample/Adjustment/Product Summary/Weekly/Monthly tests passed; changed modules compiled and `git diff --check` passed. Feature checkpoint `39ce8e6794d0fc99ce1198d0ef06d6be83783127` and status checkpoint `510f986c27446f37c2c945fc29015ef0cdd37c17` are **COMMITTED / PUSHED**. UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; schema delta: 0; deployment: NOT VERIFIED.

## Current Regression Controls

These Product Owner-accepted UAT2 comparisons are time-specific regression facts, not universal formulas. The earlier mismatch was a **consolidation-grain difference only**, not a source-population, payout-membership, promotion-allocation, or financial-arithmetic defect.

| Payout period | Weekly / Cross rows | Source items | Qty | Original Sales | Discount | Amount |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-08-03 to 2026-08-09 | 209 / 209 | 765 / 765 | 1,141 | RM30,482.70 | RM3,415.60 | RM27,067.10 |
| 2026-08-10 to 2026-08-16 | 234 / 234 | 1,038 / 1,038 | 1,575 | RM37,242.70 | RM1,733.39 | RM35,509.31 |
| 2026-08-31 to 2026-09-06 | 170 / 170 | 442 / 442 | 715 | RM17,848.10 | RM1,899.33 | RM15,948.77 |

Focused alignment evidence for `678cd06`: 82 tests, staged-import checks, `py_compile`, and `git diff --check` passed. UAT2 write delta and schema-migration delta were both zero. Current source tests protect the committed reader, payout-date filtering, missing-NAV block, shared identity, table/PDF rowset, and signed dashboard discount.

## Persistence / Schema State

- Cross/Weekly reporting work: **UAT2 write delta 0; schema migration delta 0**.
- Do not create a Cross reporting persistence table or mutate committed UAT2 data without Product Owner approval.
- The Statement writer remains a compact one-request logical atomic `values.batchUpdate` followed by readback; financial-component evidence must not be removed merely for storage convenience.

## Important Git Checkpoints

- `f989a8a` — restored Invoice validation and reconciliation workflow.
- `451ce09` — introduced the database-backed Cross Platform Product Summary.
- `d696ed5` — restored the shared barcode-table renderer dependency.
- `8d79888` — hid the legacy Weekly Barcode PDF button.
- `678cd06` — aligned Cross/Weekly canonical consolidation; committed and pushed.
- `69b2302` — synchronized project status and durable memory protocol; committed and pushed.
- `9f67f4e` — added the Shopee SG market-isolation foundation; committed and pushed.
- `e727258` / `3fe38c2` — added platform-aware Data Import entry and active-market switching; committed and pushed.
- `39ce8e6` / `510f986` — implemented, status-synchronized, and pushed Shopee MY Cancelled Order V1.
- `4eccc62` / `d4dd00c` — aligned current Data Import regression assertions and ignored generated report outputs.

## Current Priorities

### P0 - PostgreSQL migration foundation

1. **CLOSED after this local checkpoint / not pushed:** Shopee MY Statement
   PostgreSQL. Historical parity, runtime reader/writer, atomic transaction,
   rollback safety, idempotency, successful real Commit, repeat-source no-op,
   and migration lessons are verified.
2. **CLOSED LOCALLY / runtime authority:** Shopee SG Statement PostgreSQL
   runtime is implemented and replay-verified. A separately authorized,
   genuinely NEW live SG Statement Commit remains pending; that operational
   smoke is distinct from the completed runtime migration.
3. **Cross-domain prerequisite closed:** identical MY/SG Order IDs are now
   regression-proven to stay isolated through `MarketContext`; Statement keeps
   `platform` and Full Order keeps canonical `market`, with no raw-column join.
4. **CLOSED LOCALLY / Full Order runtime authority:** MY Full Order Current
   PostgreSQL parity, reconstruction, runtime routing, real-source idempotency,
   and consumer replays are complete with no Sheets fallback. The Product Owner
   has now verified a genuine SG Full Order local-app runtime write to
   PostgreSQL. Current read-only counts are MY 14,916 Orders / 22,466 Items and
   SG 10,563 Orders / 14,127 Items; the SG increase from its 10,297 / 13,701
   historical baseline is the verified runtime source. Exact second-source
   replay evidence was not recorded. Product Master remains Google Sheets only.
5. **Then:** inventory remaining application-owned Sheets-backed runtime/history
   data as `MIGRATE TO POSTGRESQL`, `SOURCE-ONLY / REMAIN GOOGLE SHEETS`, or
   `LEGACY / DECOMMISSION INSTEAD OF MIGRATE`. Do not migrate obsolete data for
   completeness. Product Master remains Google Sheets.

### P1 - MY functional migration/cleanup

1. Preserve MY-02B financial semantics until Product Owner decisions establish
   Original Sales/Discount meanings and safe order-level component allocation.
2. Continue separately authorized Analysis-reader and Payout Product Summary
   work only after its legacy Invoice dependency migration has evidence.
3. Preserve and verify stable daily Google Sheets operation while local
   PostgreSQL migration advances; no automatic backend fallback or dual-write.

## Approved / Pending

- **PRODUCT OWNER CONFIRMED / CLOSED / REAL LOCALHOST SMOKE VERIFIED:** Shopee MY
  and SG active order/settlement flow is `Full Order Current + Product Master`
  for operational/product facts and `Statement + Full Order Current + Product
  Master` for settlement facts and Payout Product Summary, joined exactly by
  `(market, order_id)`. PDF/`Invoice_Orders`/`Invoice_Items` are preserved
  legacy audit evidence only: missing PDF/Invoice coverage is not an active
  Statement or Product Summary blocker. Payout uses Statement for payout
  population/date and Full Order Current for all product facts; the same direct
  Full Order product calculation serves both date modes. MY and SG remain
  strictly market-isolated with no cross-market fallback.
- **APPROVED / PENDING IMPLEMENTATION:** billing readiness gate and later Billing Summary/Analysis layers beyond current committed-data reports.
- **APPROVED / IMPLEMENTED CURRENT:** `D:\download material\september all orderid.xlsx` is the Shopee MY Full Order Current source. The completed / escrow-paid September workbook is retained only as the 2,049-Order / 2,845-Item settlement golden/regression subset and must not redefine Current coverage. Current is a mutable MY operational snapshot with no mandatory upload cadence; it never overwrites the Statement-proved canonical Invoice or Statement tables.
- **DEFERRED:** Cross adapters beyond committed Shopee scope; Product ID-to-Seller-SKU mapping; bank receipt reconciliation; final underpayment classification; revised Statement replacement/versioning; PostgreSQL production cutover/storage redesign beyond the authorized local initial migration.
- **UNRESOLVED:** real-source contracts and Product Owner approval are required before new platform mappings, financial allocation, or schema changes.
- **SHOPEE SG DATA IMPORT V1 COMPLETE / PRODUCT OWNER ACCEPTED / COMMITTED / PUSHED:** checkpoint `71cf1949da11005c5a3113a8c86106373be3bed0` (`feat: complete shopee sg data import v1`) is pushed to `origin/feature/uat2-invoice-persistence-integration`. Real ordinary SG Invoice and Listing evidence are audited; PDF/ZIP test intake, market-aware SGD presentation, and live `shopee sg` Product Listing reconciliation are enabled through Manual Review / Accepted Candidate. A separately approved destination/persistence contract, SG Statement, and cancellation/refund/return evidence remain pending. Source-access/data-contract failures must fail closed. Deployment: **NOT VERIFIED**.

## Recent Execution History

- Date: 2026-10-30; Task: Shopee Analysis module navigation; State: LOCAL IMPLEMENTATION / LIVE PO REVIEW PENDING; Outcome: replaced separate module buttons with one native market-scoped segmented selector, preserving applied filters and shared MY/SG rendering. Verification: focused tests and local fixture browser check; Git: no stage, checkpoint or push; deployment: NOT VERIFIED; UAT2 write delta: 0; schema delta: 0; Decision required: NO; Next: review authenticated MY/SG live application.

- Date: 2026-10-30; Task: strict shared Shopee Analysis modular layout; State: LOCAL IMPLEMENTATION / BROWSER ACCEPTANCE PENDING; Outcome: removed numeric trend padding, added market-scoped module navigation and grouped Overview/filter actions; preserved shared MY/SG authorities and semantics. Verification: 60 focused tests passed; Git: no checkpoint or push; deployment: NOT VERIFIED; UAT2 write delta: 0; schema delta: 0; Decision required: NO; Next: complete live-browser visual and chart verification.

- Date: 2026-10-30; Task: shared Shopee Analysis modular UI refinement; State: LOCAL IMPLEMENTATION / PENDING PO REVIEW; Outcome: grouped applied filters and actions, styled module tabs, aligned product and financial analysis cards, preserving canonical report semantics and shared MY/SG renderer. Verification: 58 focused tests passed, py_compile and diff check passed; Git: no checkpoint or push; deployment: NOT VERIFIED; UAT2 write delta: 0; schema delta: 0; Decision required: NO; Next: visual review at desktop and laptop widths.

- Date: 2026-10-30; Task: shared Shopee Analysis apply filters and UI polish; State: LOCAL IMPLEMENTATION / PENDING PO REVIEW; Outcome: MY and SG share draft/applied filter state, explicit apply/reset, and applied-filter context with restrained native chart and section presentation; financial definitions and authorities unchanged. Verification: focused Analysis and market tests plus AppTest; Git: no checkpoint or push; deployment: NOT VERIFIED; UAT2 write delta: 0; schema delta: 0; Decision required: NO; Next: Product Owner localhost review.

- Date: 2026-10-29; Task: Phase 1 application-surface regression triage; State: LOCAL TESTED / PENDING CLOSURE; Outcome: Product Owner reviewed the Phase 1 routes and source surface. The original focused rerun had 176 passed and 37 failed: 27 obsolete removed-surface expectations and 10 stale test setup/label expectations. Updated applicable tests, explicitly retired 18 legacy Invoice UI cases, and preserved MY/SG Statement ownership and partial-state guards. No production logic changed during triage. Verification: expanded final suite 477 passed, 47 skipped (including 18 retired legacy UI cases); Git: no checkpoint, commit or push; deployment: not verified; UAT2 write delta: 0; schema delta: 0; Decision required: NO; Next: Product Owner closure review and separate Phase 2 dependency/dead-code review.

- Date: 2026-10-29; Task: MY Full Order identity-first Product Master resolver
  and Validate classification; State: `MY_FULL_ORDER_IDENTITY_FIRST_PM_RESOLVER`
  **CLOSED / REAL APRIL VERIFIED**. Product
  Owner confirmed exact identity-first precedence and complete canonical
  consensus; separate SKU and Parent SKU fallbacks retain fail-closed conflicts.
  The actual April source and live MY Product Master resolve both Muesli, both
  Barley Grass, 13 Cactus and the Chilli examples. Read-only PostgreSQL
  comparison: 2,036 = 2,036 NEW + 0 UPDATED + 0 UNCHANGED + 0 NEED REVIEW.
  Verification: 249 focused/market/reporting regressions and clean-index smoke
  passed (empty test secrets file); no business,
  Product Master, workbook or schema writes. Deployment: NOT VERIFIED.
  Git implementation checkpoint: `94d7101`; normal feature-branch push of
  `94d7101` succeeded. Documentation checkpoint: `933c50e`; normal push of
  the documentation checkpoint succeeded.

- Date: 2026-10-29; Task: MY Full Order PM exact-name resolution and Validate
  classification; State: IMPLEMENTED / ACCEPTANCE BLOCKED. The actual April
  workbook and live MY Product Master resolve both Barley Grass orders by exact
  name (Listing row 430, NAV 3000686, USOLF 49.90) and both Muesli orders by
  consensus. Read-only PostgreSQL comparison: 2,036 orders = 2,022 NEW +
  0 UPDATED + 0 UNCHANGED + 14 NEED REVIEW. The prior resolver replay had two
  review orders; the requested candidate-union/fail-closed rule exposes 13
  Cactus orders with conflicting canonical SKUs and one Chilli order with
  conflicting SKU/NAV/USOLF, all previously accepted. Exact SKU precedence
  versus the new fail-closed rule requires Product Owner clarification before
  claiming no new review cases or checkpointing. Regression: 234 passed;
  py_compile and diff check passed. UAT2 business writes: 0; schema delta: 0;
  Git stage/commit/push: 0; deployment: NOT VERIFIED. Next: resolve the
  candidate-precedence decision and repeat real-source acceptance.

- Date: 2026-10-29; Task: MY Full Order Product Master canonical consensus;
  State: IMPLEMENTED / REVIEW PENDING. Multiple exact candidates may agree on
  canonical Seller SKU, NAV, positive USOLF and resolve_sku without a unique
  physical variation row; conflicting or incomplete tuples remain blocked.
  SG exact-row resolution is unchanged. Synthetic regression covers the reported
  SKU; the April Full Order source and live Product Master candidate evidence
  were not supplied, so the two reported orders remain unverified. Final focused
  and regression run: 202 passed; an earlier broader run had 201 passed and two
  legacy Cross Platform session-view UI tests failed against
  the current committed-data UI contract. Git: no stage/commit/push; deployment:
  not verified; UAT2 business writes: 0; schema delta: 0. Next: obtain the
  April source and matching Product Master evidence for read-only verification.

- Date: 2026-10-29; Task: inspect shared Shopee Analysis trend aggregation
  selector/report binding; State: VERIFIED / NO CODE CHANGE. Outcome: Product
  and Financial aggregation widgets use distinct Streamlit keys
  (`product_trend_aggregation` / `financial_trend_aggregation`); each title and
  aggregation frame use its own selected value. AppTest changed Product from
  Daily to Weekly while Financial remained Daily, with matching rendered titles.
  No authority, cohort, query, or chart styling change was required. Business,
  schema, and data writes: 0; Git stage/checkpoint/push: 0; deployment:
  **NOT VERIFIED**. Next: refresh the active browser page and retry.

- Date: 2026-10-29; Task: cut Shopee SG Analysis to the shared Shopee Analysis
  V2 provider/view-model/renderer; State:
  `SG_ANALYSIS_SHARED_TEMPLATE_CUTOVER - CLOSED / PRODUCT OWNER ACCEPTED`.
  Outcome: replaced the separate SG Phase A service/dashboard with an explicit
  `SHOPEE_SG` provider and the existing market-neutral renderer. Product facts
  remain Full Order-only; settlement, signed native components, refunds, and
  linked adjustments remain `OrderFinancialProjection` facts; missing Statement
  remains pending evidence. Verification: 58 focused SG/MY/shared-renderer,
  projection, and MarketContext tests passed; `py_compile` and `git diff --check`
  passed. Both local Streamlit endpoints returned HTTP 200. This shell lacks the
  external PostgreSQL credentials, so no live query or business-data fallback
  was attempted. Business/schema/data writes: 0; Git stage/checkpoint/push: 0;
  deployment: **NOT VERIFIED**. Product Owner localhost acceptance: **PASS**.

- Date: 2026-10-29; Task: close Shopee MY Analysis V2 canonical PostgreSQL and
  final shared UI/UX refinement; State:
  `MY_ANALYSIS_V2_CANONICAL_POSTGRESQL - CLOSED / FINAL UIUX ACCEPTED` and
  `SHOPEE_ANALYSIS_FINAL_UIUX_REFINEMENT - CLOSED / PRODUCT OWNER ACCEPTED`.
  Outcome: converted clipping filter/KPI rows to deliberate responsive groups;
  removed duplicate Overview trends; replaced reconciliation donut presentation
  with labelled 100% stacked coverage; retained signed settlement/component
  analysis while excluding base Product Price from impact ranking; made chart
  date basis/aggregation titles explicit; preserved full product names outside
  compact ranking labels; and moved search-first order inspection above its
  evidence table. Verification: 59 focused MY Analysis, shared renderer,
  projection, SG Analysis, and market-context tests passed; `py_compile` and
  both diff checks passed. Legacy Invoice runtime reads: 0. Business/schema/data
  writes: 0. Git stage/checkpoint/push: 0. Deployment: **NOT VERIFIED**. Next:
  Product Owner final UI/UX acceptance is recorded. Deployment: **NOT VERIFIED**.

- Date: 2026-10-29; Task: final Shopee MY Analysis V2 UI/UX polish; State:
  `MY_ANALYSIS_V2_FINAL_UI_POLISH - IMPLEMENTED / LOCALHOST PRODUCT OWNER REVIEW PENDING`.
  Outcome: retained the shared canonical MY/SG renderer and changed only
  presentation. Product, Settlement, and Financial Adjustment KPI hierarchy is
  explicit; settlement evidence uses lifecycle-safe wording and a compact
  reconciliation donut; signed settlement/component and ranked-product bars
  complement the detailed tables. Product and financial trends support bounded
  Daily/Weekly/Monthly display aggregation, and order inspection is now
  search-first beside reconciliation. Focused MY V2, projection, SG Analysis,
  and market-context tests passed (55 tests); compilation and diff checks
  passed. Legacy Invoice runtime reads: 0. Business/schema/data writes: 0. No
  stage, checkpoint, or push. Product Owner localhost visual/data review and
  deployment verification remain pending.

- Date: 2026-10-29; Task: polish Shopee MY Analysis V2 settlement scope and
  Streamlit UX; State:
  `MY_ANALYSIS_V2_UIUX_AND_SETTLEMENT_SCOPE_POLISHED - LOCALHOST SMOKE PENDING`.
  Outcome: corrected the dashboard-level settlement cohort so Total Product
  Sales stays Full Order-wide while Settled Product Sales, release, components,
  and settlement difference use the exact committed Statement ORDER population.
  Added settlement coverage, explicit missing-Statement lifecycle messaging,
  date-domain-clipped trends, progressive Financial/Reconciliation disclosure,
  and explicit order selection. The shared MY/SG renderer and canonical
  authorities are unchanged. Verification: focused MY V2 service/UI tests
  passed. Business-data writes: 0; schema delta: 0; no stage, checkpoint, or
  push. Product Owner localhost UI/data smoke remains required; deployment:
  **NOT VERIFIED**.

- Date: 2026-10-29; Task: implement Shopee MY Analysis V2 from canonical
  PostgreSQL authorities; State:
  `MY_ANALYSIS_V2_CANONICAL_POSTGRESQL - IMPLEMENTED / LOCALHOST SMOKE PENDING`.
  Outcome: replaced the MY Invoice-shaped service/page consumer with an
  explicit `SHOPEE_MY` provider over the shared Shopee Analysis view model and
  renderer. Full Order items now define product metrics; the canonical
  projection defines status/date, payout/released/settlement, native signed
  components, and linked adjustments. The shared renderer is contract-tested
  for MY and SG currency/presentation without changing SG Analysis. Focused
  canonical service/UI tests and `py_compile` passed. No persistence, schema,
  Product Summary, SG Analysis, Billing, Invoice infrastructure, business-data
  write, stage, checkpoint, or push occurred. Product Owner localhost UI/data
  smoke remains required; deployment: **NOT VERIFIED**.

- Date: 2026-10-29; Task: audit Shopee MY Live Analysis legacy financial
  semantics; State: `MY_LIVE_ANALYSIS_SEMANTIC_MAPPING_AUDIT_COMPLETE`.
  Outcome: verified the current mixed-authority path and mapped remaining
  Invoice-derived KPIs/classification against Full Order and
  OrderFinancialProjection. No MY Analysis migration, formula/UI change,
  business-data write, test run, stage, commit, or push occurred. Decision
  required: approve explicit canonical meanings for Total Sales, Refund Amount,
  and the legacy layout classifier before any cutover. Deployment: **NOT
  VERIFIED**.

- Date: 2026-10-28; Task: close Shopee SG Analysis OrderFinancialProjection
  cutover; State: `SG_ANALYSIS_ORDER_FINANCIAL_PROJECTION_CUTOVER - CLOSED /
  VERIFIED`. Outcome: SG order-row date/status, payout/released/settlement, and
  native financial/refund components now all consume the canonical projection;
  Full Order items remain the sole quantity/subtotal source. The independent
  market-locked adjustment timeline retains linked and unlinked events
  separately and never changes released amount. Verification: 11 focused SG
  Analysis/projection tests passed; `py_compile`, diff check, and staged secret
  scan passed. Code checkpoint: `72855d2` (`feat: complete sg analysis
  projection cutover`). Business write delta: 0; schema delta: 0; push: **NOT
  PUSHED**; deployment: **NOT VERIFIED**.

- Date: 2026-10-28; Task: audit and complete Shopee SG Analysis canonical
  projection wiring; State: `SG_ANALYSIS_ORDER_FINANCIAL_PROJECTION_CUTOVER_IMPLEMENTED`.
  Outcome: SG Analysis already used the canonical projection for settlement,
  released amount, and component detail, but read status/date from a parallel
  Full Order map. The minimal wiring completion now obtains its order-row
  status/date and Order-Created date filtering from `OrderFinancialProjection`;
  the retained Full Order snapshot is item-only for quantity/subtotal, while
  the independent market-locked adjustment timeline preserves unlinked events.
  No formula, MY Analysis, Billing, Invoice, or Google Sheets Invoice path was
  changed. Verification: 11 focused SG Analysis/projection tests passed and
  `py_compile` plus `git diff --check` passed. Git: no stage, commit, or push.
  UAT2 business write delta: 0; schema delta: 0; Decision required: none; Next:
  selective checkpoint only when authorized.

- Date: 2026-10-28; Task: close OrderFinancialProjection authority boundary;
  State: `ORDER_FINANCIAL_PROJECTION - CLOSED / AUTHORITY BOUNDARY VERIFIED`.
  Outcome: the Statement reader is semantically Statement-only and has no Full
  Order access. `OrderFinancialProjection` is the only Full Order/Statement
  composition boundary; it returns exactly one projection per `(market,
  order_id)`, preserves native component multiplicity without item allocation,
  keeps adjustments separate, and has no Net Income or Invoice/Google Sheets
  fallback. SG Analysis consumes that projection rather than joining the two
  authorities itself. Verification: 14 focused projection/Statement-reader/
  SG-Analysis tests passed; `py_compile` and staged diff check passed; staged
  secret scan found no credential material. Code checkpoint: `cf80bd1`
  (`feat: add order financial projection`). Business write delta: 0; schema
  delta: 0; push: **NOT PUSHED**; deployment: **NOT VERIFIED**.

- Date: 2026-10-28; Task: verify OrderFinancialProjection authority boundary;
  State: VERIFIED / CORRECTED / UNCHECKPOINTED. Outcome: removed Full Order
  snapshot access and the convenience `order_status` field from the
  Statement-only financial reader. The canonical OrderFinancialProjection now
  exclusively reads the market-bound Full Order snapshot and composes status,
  order-created date, and explicit missing-Statement state with native
  Statement and Adjustment facts. SG Analysis internal wiring was adapted with
  no UI/output change. Verification: 13 focused projection/Statement-financial/
  SG-Analysis tests passed, changed modules compiled, and diff check passed.
  Git: no stage, commit, or push. UAT2 business write delta: 0; schema delta:
  0; Decision required: none; Next: reviewer approval before checkpoint.

- Date: 2026-10-28; Task: implement shared read-only canonical
  `OrderFinancialProjection`; State: IMPLEMENTED / TESTED / LOCAL SMOKE
  BLOCKED. Outcome: added the market-bound order-level Full Order + committed
  Statement + native component + independent adjustment composition service for
  future consumers only; MY Analysis, SG Analysis UI, Weekly Billing, Monthly
  Billing, Full Order/Statement ingestion, Product Summary, Product Master,
  and schemas are unchanged. Full Order coverage is fail-closed; unpayout Full
  Orders remain explicit; components and adjustments are never item-allocated
  or combined into a derived net-income field. Verification: 13 focused
  projection/Statement-financial/SG-Analysis tests passed, changed modules
  compiled, and diff check passed. PostgreSQL smoke was blocked before a query
  by absent local runtime credentials (`INVOICEGATHER_PG_USER` /
  `INVOICEGATHER_PG_PASSWORD`); no Sheets fallback. Git: no stage, commit, or
  push. UAT2 business write delta: 0; schema delta: 0; Decision required:
  none; Next: Product Owner/reviewer approval before staging.

- Date: 2026-10-28; Task: close Consolidated Product Summary PostgreSQL
  cutover; State: `CONSOLIDATED_PRODUCT_SUMMARY_POSTGRESQL_CUTOVER - CLOSED /
  REAL LOCALHOST SMOKE VERIFIED`. Outcome: both Order-Created and Payout
  Product Summary migrations are closed. MY and SG Order-Created PostgreSQL
  runtime smoke passed; MY's retained reference is 429 products / 31,768
  quantity. MY and SG Payout PostgreSQL runtime smoke passed and displayed
  report rows matched direct PostgreSQL inspection. Order Created selects Full
  Order PostgreSQL orders; Payout selects committed market-scoped Statement
  ORDER IDs; Full Order PostgreSQL Items supply product facts; both use the
  same aggregation engine. Observed legacy Product Summary runtime reads are
  `Invoice_Orders=0`, `Invoice_Items=0`, and Google Sheets Invoice tabs=0.
  Product Master remains unchanged. Verification: 84 focused Product
  Summary/market-isolation/forbidden-legacy-reader/barcode/cutover tests
  passed with 8 expected guarded skips; `py_compile` and staged diff check
  passed; staged secret scan found no literal secret. Git feature checkpoint:
  `2afb18b455df439f7feb71dbe4400b5ea53060a1`
  (`feat: cut over consolidated product summary to postgres`); no push;
  PostgreSQL, Google Sheets, schema, and business-data write delta: 0;
  deployment: NOT VERIFIED.

- Date: 2026-10-28; Task: migrate Payout Product Summary to canonical
  PostgreSQL Statement-order selection plus Full Order items; State:
  IMPLEMENTED / TESTED / LOCAL SMOKE PENDING. Outcome: Payout mode now reads
  only committed market-bound Statement `ORDER` rows to select payout-date
  order IDs, then applies the same Full Order item/subtotal calculation as
  Order-Created mode. It has no Invoice/PDF, Google Sheets Invoice, Statement
  SKU, Financial Component, adjustment, or product-level fee-allocation path.
  Missing Full Order coverage and cross-market evidence fail closed. Verification:
  MY/SG isolated reader, coverage, common-calculation, UI, barcode, and cutover
  tests; no real PostgreSQL query was possible because external credentials are
  unavailable in this execution. Git stage/commit/push: 0; PostgreSQL, Google
  Sheets, schema, and business-data write delta: 0; deployment: NOT VERIFIED.
  Next: Product Owner localhost smoke with external PostgreSQL credentials.

- Date: 2026-10-28; Task: migrate Order-Created Product Summary to the
  PostgreSQL Full Order-only reader; State: IMPLEMENTED / TESTED / REAL-DATA
  PARITY BLOCKED. Outcome: selecting `Order Created Date` now bypasses Invoice
  tables, Invoice Google Sheets reads, legacy Invoice repository construction,
  and Statement reads; market-bound Full Order input is fail-closed against
  cross-market leakage. Payout mode stays on the prior legacy Invoice path.
  Verification: focused MY/SG reader, Payout-selection, UI, factory, and
  cutover tests `50 passed, 8 skipped`; `py_compile` passed. The required
  local PostgreSQL parity read could not start because external runtime
  credentials were unavailable; Google Sheets fallback was not attempted. Git
  stage/commit/push: 0; PostgreSQL, Google Sheets, schema, and business-data
  write delta: 0; deployment: NOT VERIFIED. Next: provide approved external
  PostgreSQL credentials and run the read-only MY parity comparison.

- Date: 2026-10-28; Task: audit upper-layer PostgreSQL consumers and canonical
  projection migration path; State: AUDITED / NO IMPLEMENTATION. Outcome: SG
  Analysis already uses the market-locked Full Order + Statement financial and
  adjustment projections. MY Analysis and Consolidated Payout Summary retain
  legacy Invoice readers for financial/product semantics, MY Weekly Billing
  retains legacy Invoice product data while reading Statement from PostgreSQL,
  and Monthly Billing remains entirely Google Sheets/legacy-Monthly-bound.
  No unsafe raw Full-Order-item-to-component join was found in active report
  paths. Verification: targeted source/repository inventory only. Git stage,
  commit, and push: 0; application, PostgreSQL, Google Sheets, schema, and
  business-data write delta: 0. Decision required: canonical Payout/Billing
  product-financial semantics before their migration. Next: implement only a
  shared order-level projection and migrate Analysis readers when authorized.

- Date: 2026-10-28; Task: close SG dynamic-zero `SERVICE_FEE_DETAIL` overlap
  handling; State: CLOSED / REAL SMOKE VERIFIED / LOCAL CHECKPOINT PENDING.
  Outcome: real mixed Statement smoke passed with 113 NEW and 314 exact
  duplicates out of 427 orders, 0 conflicts/unsafe, Full Order coverage for
  all 113 NEW orders, Total Released S$4,496.13, and Adjustment Total S$12.96.
  The prior 709-order / 910-SKU overlap source is now recognized as already
  imported with zero unnecessary writes; the 192 `INCOMPLETE_UNSAFE` failures
  are no longer reproduced. Historical PostgreSQL components were not deleted
  or rewritten. Focused MY/SG Statement regressions: 102 passed, 14 skipped;
  `py_compile` and `git diff --check` passed. Google Sheets, PostgreSQL
  business-data, and migration writes: 0. Git push: 0; deployment: NOT
  VERIFIED. Next: selective local checkpoint only; no further business Commit.

- Date: 2026-10-28; Task: continue SG dynamic-zero overlap read-only replay;
  State: RUNTIME REPLAY BLOCKED / ZERO WRITE. Outcome: the approved local
  PostgreSQL Windows service is running and `127.0.0.1:5433` is reachable, so
  Docker availability is not the runtime gate. The external
  `INVOICEGATHER_PG_USER` / `INVOICEGATHER_PG_PASSWORD` variables are absent,
  so no database connection, baseline count query, 709-order classification,
  adjustment replay, or row-delta verification was attempted. Focused MY/SG
  Statement regressions passed `102 passed, 14 skipped`; Google Sheets,
  PostgreSQL, schema, and business-data writes: 0; Git stage/commit/push: 0;
  deployment: NOT VERIFIED. Next: provide the approved external PostgreSQL
  credentials, then run the read-only 709-order replay before checkpointing.

- Date: 2026-10-28; Task: implement the PO-approved narrow dynamic zero-valued
  `SERVICE_FEE_DETAIL` overlap comparison; State: IMPLEMENTED / RUNTIME REPLAY
  BLOCKED. Outcome: parser now retains the complete dynamic Service Fee header
  set and the shared MY/SG classifier treats only a zero-valued `SERVICE_FEE_DETAIL`
  from `Service Fee Details` as equivalent to an absent counterpart when the
  opposite parsed source proves the exact header is absent. Nonzero differences,
  a present header with a missing row, ordinary component absence, ORDER/SKU
  differences, and unknown committed header provenance remain fail-closed.
  Focused regressions passed (121 passed, 29 skipped); the real 709-order source
  parsed as 709 ORDER / 910 SKU rows with the two retired PromoXtra headers
  absent. The required current PostgreSQL replay could not run: Docker reports
  no running PostgreSQL container and runtime credentials are unavailable. No
  Google Sheets, PostgreSQL, schema, or business-data write occurred; Git
  stage/commit/push: 0; deployment: NOT VERIFIED. Decision required: none.
  Next: start the approved local PostgreSQL target, supply its external runtime
  credentials, then perform the read-only 709-order replay before checkpointing.

- Date: 2026-10-28; Task: read-only diagnosis of the 192 Shopee SG Statement
  overlap bundles in `Income.released.sg.20260929_20261006.xlsx`; State:
  ROOT CAUSE IDENTIFIED / NO WRITE. Outcome: all 192 are
  `INCOMPLETE_UNSAFE` (zero `CONFLICT`) and are exactly the 100
  2026-09-29 plus 92 2026-09-30 overlap orders; the remaining 516 October
  orders are NEW and the sole exact duplicate is `2609275QXSN3VC`. Each unsafe
  bundle has equal ORDER and SKU multisets and no monetary, Decimal, NULL/date,
  sequence, or provenance-equality difference, but PostgreSQL faithfully retains
  two additional zero-valued `SERVICE_FEE_DETAIL` facts from the historical
  September export: `PromoXtra 5.45% cap $30` and `PromoXtra 6.54% cap $40`.
  The later overlapping export omits both columns. The affected historical batch
  has one ORDER fact/candidate per order and zero orphan components; this is a
  dynamic-source-header overlap-semantics gap, not incomplete PostgreSQL data or
  a migration/reconstruction defect. No duplicate rule was changed. Verification:
  real source parse, active PostgreSQL preflight/reconstruction, and read-only
  SQL; PostgreSQL/Google Sheets/schema/business/Git writes: 0; deployment:
  NOT VERIFIED. Decision required: whether structurally absent, zero-valued
  dynamic Service Fee columns are semantically equivalent during overlap dedupe.
  Next: implement only a PO-approved, narrow comparison rule with regression
  coverage; do not retry the Statement Commit first.

- Date: 2026-10-28; Task: checkpoint Product Owner-verified Shopee SG Full
  Order PostgreSQL runtime smoke; State: VERIFIED / CLOSED LOCALLY / LOCAL
  CHECKPOINTED. Outcome: Product Owner completed a genuine local-app SG
  Full Order upload and confirmed PostgreSQL as the actual write target with no
  Google Sheets fallback. Read-only local PostgreSQL verification found SG
  Full Order 10,563 Orders / 14,127 Items, including 274 Orders from latest
  source `Order.all.order_creation_date.20261020_20261025.xlsx`; MY remains
  14,916 / 22,466 and Statement remains 26,722 / 578,653 / 548 / 46. Exact
  second-source replay/idempotency evidence is not recorded. The first core
  local PostgreSQL runtime-authority milestone is therefore closed for MY/SG
  Statement and MY/SG Full Order; Product Master intentionally remains
  read-only Google Sheets. Verification: PostgreSQL read-only counts and
  provenance; business/schema/Google Sheets writes: 0; deployment: NOT
  VERIFIED; local checkpoint: `15516deb2a66b716273998b8034b85ad93ed7942`
  (`docs: record sg full order postgres verification`); Git push: 0. Next: inventory the remaining active runtime and
  legacy dependencies before authorizing another migration domain.

- Date: 2026-10-28; Task: finalize MY/SG Full Order PostgreSQL default
  authority; State: IMPLEMENTED / TESTED / LOCAL CHECKPOINTED / NO
  BUSINESS WRITE. Outcome: both MY and SG now resolve an absent Full Order
  backend selector to PostgreSQL, matching Statement. Explicit `postgresql`
  remains PostgreSQL; explicit `google_sheets` is the only legacy Full Order
  route; blank/unknown selectors fail closed. PostgreSQL configuration is
  validated before a Full Order repository or Product Master is constructed;
  missing user/password and invalid port produce a safe blocking error with no
  Google Full Order fallback. Product Master remains its approved read-only
  Google Sheets source. Statement behavior is unchanged. Verification: 70
  focused tests passed / 8 guarded skips; `py_compile` and `git diff --check`
  passed. Google Sheets, PostgreSQL business, Statement, Product Master,
  schema, and deployment writes: 0. Git push: 0. Next: Product Owner may run
  the separately authorized SG Full Order runtime smoke.

- Date: 2026-10-27; Task: Shopee SG Full Order Current PostgreSQL historical
  migration and runtime-read cutover; State: HISTORICAL PARITY VERIFIED /
  RUNTIME WRITE SMOKE PENDING. Outcome: the configured dedicated SG Full Order
  Google Sheets source (`Shopee_FullOrder_Current_Orders` / `_Items`) was
  unambiguous at 10,297 Orders / 13,701 Items (`shopee_sg`). The initially
  empty local PostgreSQL SG scope now has exactly those counts. Key parity,
  all persisted-field parity (including Decimal, blank/NULL representation,
  provenance, NAV, USOLF/unit price, and resolve_sku), and keyed
  reconstruction parity are zero-difference. The shared repository now
  accepts only explicit MY/SG MarketContext; SG PostgreSQL mode has no Full
  Order Sheets fallback while the explicit Google backend remains available.
  SG Statement PostgreSQL has 2,417 committed ORDER identities, all covered;
  MY Full Order stayed 14,916 / 22,466 and Statement tables stayed
  26,722 / 578,653 / 548 / 46. Direct Order-Created Product Summary parity is
  13,033 items / Qty 18,417 / S$168,327.81 / 331 groups. Product Master remains
  Google Sheets only. A locally present genuine September SG source was invoked
  for preflight, but the live Product Master request did not complete within
  the bounded local runner, so no source-driven PostgreSQL write was attempted.
  Google Sheets, Statement, Product Master, schema, and deployment writes: 0;
  PostgreSQL Full Order historical write: 10,297 Orders / 13,701 Items; push:
  0. Next: retry only the read-only SG Product Master preflight, then perform
  the separately verified source runtime replay if it is clean.

- Date: 2026-10-27; Task: authorize and complete MY Full Order PostgreSQL
  `resolve_sku` parity correction; State: LOCALLY VERIFIED / CHECKPOINT PENDING.
  Outcome: Product Owner approved current Google Sheets Full Order as the
  authority for exactly four historical MY keys. A serializable, locked,
  PostgreSQL-only transaction changed only their `resolve_sku` values from
  `8717496900999` to `5004182` and verified every unrelated field before
  commit. Final Sheets/PostgreSQL key and business-field parity is 14,916
  Orders / 22,466 Items with zero differences. Reconstruction, Statement Full
  Order coverage (8,557 / 0 missing), Order Created Product Summary (20,950
  items / Qty 31,768 / 429 groups), and Analysis date authority passed through
  the selected PostgreSQL backend. The supplied real source replayed as 0 NEW
  / 0 UPDATED / 2,629 UNCHANGED. Statement stayed 26,722 / 578,653 / 548 / 46;
  SG stayed 0 / 0. Google Sheets writes: 0; PostgreSQL Full Order writes: 4
  approved item-column corrections only; Statement/SG/schema writes: 0. Git
  stage/commit/push: pending / 0; deployment: NOT VERIFIED. Next: selective
  local checkpoint of the completed MY Full Order PostgreSQL runtime work.
  Local feature checkpoint: `c7be1e0b6900e4f8cccdefb8d47391328d437fb5`
  (`feat: verify my full order postgres runtime`); push/deployment: NOT PUSHED / NOT VERIFIED.

- Date: 2026-10-27; Task: MY Full Order PostgreSQL real-delta runtime-write
  smoke; State: IMPLEMENTED / TESTED / PARTIAL ACCEPTANCE. Outcome: the
  supplied real MY source `Order.all.order_creation_date.20260927_20261027.xlsx`
  parsed as 2,629 Orders / 3,711 Items with zero Product Master issues and was
  processed by the local application's explicit MY PostgreSQL backend. The
  atomic first run committed 460 NEW, 771 UPDATED, and 1,398 UNCHANGED Orders;
  661 Item identities were inserted and 1,953 existing Item rows updated, with
  no deletion path. Transaction readback passed; PostgreSQL reached 14,916
  Orders / 22,466 Items. A second identical run returned 0 NEW / 0 UPDATED /
  2,629 UNCHANGED. Sheets/PG keys, Full Order business fields except
  `resolve_sku`, Statement gate coverage (8,557 committed MY ORDER identities),
  Analysis dates, and Product Summary quantity/amount/NAV/Seller-SKU grouping
  matched. Four pre-existing `resolve_sku` values remain different and are not
  in the supplied source, so no Sheets fallback or unrelated Barcode update was
  applied; final cutover acceptance is BLOCKED pending their separately
  authorized authority. Verification: 59 focused tests passed, 8 intentional
  integration skips, `py_compile`, and `git diff --check`. Google Sheets
  writes: 0; PostgreSQL Full Order business-data writes: 460 Orders / 661 new
  Items plus 771 Orders / 1,953 Items updated; Statement/SG/schema writes: 0;
  Git stage/commit/push: 0; deployment: NOT VERIFIED.

- Date: 2026-10-27; Task: close PostgreSQL Statement / Full Order canonical
  MY/SG market boundary; State: IMPLEMENTED / TESTED / NO RUNTIME OR SCHEMA
  CHANGE. Outcome: added focused non-writing regression coverage for the same
  Order ID in both markets. `MarketContext` maps canonical `shopee_my` /
  `shopee_sg` to Statement `Shopee` / `Shopee SG`; the financial projection
  returns only that market's Current order and Statement facts, while the
  Full Order existence gate rejects the identical ID when it appears only in
  the other market in either direction. Existing active cross-domain paths
  were already normalized; no production correction was required. Read-only
  PostgreSQL check: MY Full Order Current is 14,456 Orders / 21,805 Items;
  SG is absent. Verification: 71 focused Statement/Full Order tests passed,
  14 intentional archived skips; changed tests passed `py_compile` and
  `git diff --check`. Google Sheets/PostgreSQL business-data writes and schema
  changes: 0. Git stage/commit/push: 0; deployment: NOT VERIFIED. Decision
  required: none. Next: MY Full Order PostgreSQL parity/reconstruction and
  runtime-cutover readiness, followed separately by SG historical migration.

- Date: 2026-10-27; Task: Shopee SG Statement PostgreSQL runtime cutover;
  State: IMPLEMENTED / TESTED / HISTORICAL REPLAY VERIFIED / LOCAL CHECKPOINTED
  / NOT PUSHED. Outcome: `56e9e8982b472fe663229bdb6d7c2de6988ef6e3`
  routes the explicit SG PostgreSQL Statement backend through the shared
  market-scoped repository for active import/reconcile/review/Commit/readback,
  financial components, adjustments, and SG Analysis Phase A. Full Order
  Current and Product Master remain Google Sheets; explicit stable
  `google_sheets` is preserved but never used as fallback. Verification: 114
  focused active-path tests passed (29 intentional guarded skips); 10 local SG
  PostgreSQL transaction/rollback/concurrency tests passed; SG counts restored
  to 5,606 / 123,033 / 68 / 17 and MY remains isolated. Temporary local test
  facts were removed; Google Sheets writes, PostgreSQL business-data writes,
  schema changes, and deployment: 0 / NOT VERIFIED. A real NEW SG Statement
  Commit was not performed and requires separate Product Owner authorization.
- Date: 2026-10-27; Task: Product Owner PostgreSQL migration strategy refresh
  and MY Statement local checkpoint; State: MY Statement PostgreSQL acceptance
  VERIFIED / LOCAL CHECKPOINT AUTHORIZED / NOT PUSHED. Outcome: Product Owner
  confirmed progressive PostgreSQL migration for MY and SG application-owned
  runtime/history data, while Product Master remains staff-editable Google
  Sheets. The completed MY Statement runtime is the first checkpointed domain:
  parity, atomic transaction/rollback, Numeric readback parity, successful real
  Commit, and repeat-source idempotency are accepted. Stable Google Sheets code
  remains available by explicit configuration; no dual authority or automatic
  fallback is allowed. Next migration is SG Statement, then market-isolated
  Full Order Current. Deployment/company-server work remains deferred. No
  Google Sheets write, PostgreSQL business-data write, or remote push is part
  of this documentation/checkpoint task.

- Date: 2026-10-27; Task: narrow PostgreSQL Statement transactional readback comparison fix; State: IMPLEMENTED / TESTED / UNCHECKPOINTED / READY FOR PO RUNTIME AUTHORIZATION; Outcome: reused the active comparison-only Decimal canonicalizer only at the PostgreSQL full-field verification boundary for declared `Numeric` columns. PostgreSQL fixed-scale values (`12.90`) now compare semantically with source values (`12.9`); identifiers, dates, provenance, status, batch/source fields, blanks, and all other non-Numeric values retain exact equality. Full stable-key lookup and pre-COMMIT verification remain mandatory. Verification: 46 focused unit/Statement tests passed with 8 expected guarded skips; local PostgreSQL controls passed for four-domain scale-only readback rollback, genuine numeric mismatch rollback, and a generated all-four-domain transaction that reached COMMIT then targeted cleanup; all four injected insert-failure rollback cases passed. Four-table post-test counts are restored to 18,381 / 397,647 / 448 / 20. Google Sheets/live MY Statement commit/schema write delta: 0; temporary generated local test rows were removed; Git stage/commit/push: 0; deployment: NOT VERIFIED. Next: Product Owner authorization is required before any new real MY Statement Commit attempt.

- Date: 2026-10-27; Task: first real MY Statement PostgreSQL Commit failure audit; State: READ-ONLY / FULL_ROLLBACK_CONFIRMED / FIX REQUIRED BEFORE RETRY; Outcome: the failed batch `20261027022841-76496ff6` has zero rows in `Statement_Data`, `Statement_Financial_Components`, `Statement_Summary`, and `Order_Adjustments`; the four baseline counts remain 18,381 / 397,647 / 448 / 20. The transaction reached the fail-closed full-field readback before commit, so its temporary inserts rolled back. The first verified identity existed (`ORDER`, sequence `1`) but PostgreSQL `Numeric(12,2)` readback rendered source `12.9` / `0` as `12.90` / `0.00`; the verifier compared lexical cells rather than the established comparison-only Decimal canonical form. The same representation-only defect covers 2,725 of 2,735 planned Statement Data rows, 43,981 of 57,973 Components, and 12 of 32 Summary rows; 9 planned Adjustments are unaffected. This audit made no Google Sheets or PostgreSQL business-data/schema writes; Git stage/commit/push: 0. Required next action: add one type-aware canonical readback representation boundary while retaining full-field verification, then run the existing rollback control and obtain separate PO authorization before any new live Commit attempt.

- Date: 2026-10-27; Task: narrow shared Statement classifier monetary canonicalization; State: IMPLEMENTED / TESTED / UNCHECKPOINTED; Outcome: added one backend-neutral, comparison-only Decimal canonicalizer for the four Statement ORDER/SKU monetary bundle fields and Financial Component amount. It removes insignificant scale, renders signed zero as `0`, preserves NULL/blank/invalid text behavior, and leaves all non-monetary provenance and genuine monetary differences under exact complete-bundle comparison. PostgreSQL storage remains `Numeric(12,2)`; no Google Sheets fallback, writer, calculation, display, schema, or business data changed. Verification: 132 focused MY/SG Statement, PostgreSQL-cutover, and persistence/import tests passed (20 intentional guarded/retired skips); protected MY source replay against 198 relevant PostgreSQL Statement rows and 4,203 components returned 1,119 NEW / 76 EXACT_DUPLICATE / 0 CONFLICT / 0 INCOMPLETE_UNSAFE, with 9 new / 1 skipped / 0 conflicting adjustments. Git stage/commit/push: 0; UAT2/Google Sheets/PostgreSQL business-data and schema write delta: 0; deployment: NOT VERIFIED. Decision required: none; Next: selective checkpoint only after Product Owner authorization.

- Date: 2026-10-27; Task: PostgreSQL migration knowledge update; State: DOCUMENTATION SYNCHRONIZED / NO CODE OR BUSINESS-DATA WRITE; Outcome: added the reusable PostgreSQL Migration Playbook and Statement incident index to scope, plus the migration acceptance checklist and confirmed parity/canonicalization lessons to Project Status. Verification: documentation diff review and `git diff --check`; Git stage/commit/push: 0; UAT2/Google Sheets/PostgreSQL business-data and schema write delta: 0; deployment: NOT VERIFIED; Decision required: none; Next: apply the playbook to future backend migration proposals.

- Date: 2026-10-27; Task: Python dependency declaration audit and completion; State: VERIFIED / UNCHECKPOINTED; Outcome: scanned active source, scripts, and tests and added direct runtime declarations for Altair, Google Auth, SQLAlchemy, and the existing psycopg2 PostgreSQL dialect driver. The README now uses the project `.venv` interpreter for installation and Streamlit startup. Verification: `.venv` requirements install resolved without changes, `pip check` passed, direct runtime imports passed, and PostgreSQL engine construction loaded `postgresql+psycopg2` without a connection or business write. UAT2/Google Sheets/PostgreSQL business-data and schema write delta: 0; Git stage/commit/push: 0; deployment: NOT VERIFIED. Next: selective checkpoint only after Product Owner authorization.

- Date: 2026-10-26; Task: real MY mixed-Statement Google Sheets commit verification; State: VERIFIED WRITE CAPACITY BLOCKER / ZERO WRITE / NO RETRY; Outcome: fresh configured-MY preflight of `Income.released.my.20261011_20261023.xlsx` is eligible `MIXED`: 1,195 source orders, 1,119 NEW, 76 exact duplicates, zero conflict/incomplete/NEW Full Order gaps, 2,735 Statement_Data rows, 57,973 component rows, 32 Summary rows, 9 new and 1 skipped Adjustment, RM33,126.14 new released amount. The one authorized batch attempt reached `spreadsheets.values.batchUpdate`, which returned `HttpError` HTTP 400 after 28,317 ms: `Invalid data[1]: This action would increase the number of cells in the workbook above the limit of 20000000 cells.` The actual request was 18,030,712 bytes / 17.1954 MiB, 4 ranges, 1,095,578 cells: Statement_Data 2,735; Components 57,973; Summary 32; Adjustments 9. Google returned no success response; immediate writer verification and an independent post-attempt active snapshot found zero added rows in every target (18,381 / 397,647 / 448 / 20 unchanged), so `WRITE_NOT_APPLIED` / **ZERO_WRITE_CONFIRMED**. This confirms a Google Sheets workbook cell-capacity failure; serialized payload size alone is not the proven cause. No retry, repair, split, schema, code, UAT2 business, Git, or deployment action followed. Decision required: separately approve a storage-capacity remediation (not batch splitting) before another live attempt.

- Date: 2026-10-26; Task: Statement Google Sheets failed-write diagnostic preservation; State: IMPLEMENTED / TESTED / UNCHECKPOINTED / ZERO BUSINESS WRITE; Outcome: the shared active Statement writer now retains a payload-free root gateway diagnostic when its one Google Sheets values batch throws and immediate authoritative readback confirms `NONE_APPLIED`. The diagnostic contains only exception type, HTTP status/API reason where available, redacted short message, request method/operation, serialized payload size, range/row/cell shape, target range names, and elapsed time; it flows through the locked commit attempt to opt-in session trace diagnostics. `WRITE_NOT_APPLIED`, the one-request/no-retry policy, immediate readback, partial-write recovery guard, NEW-only plan, adjustment filtering, and Step 5 UI state are unchanged. Verification: six synthetic HTTP/timeout/transport gateway cases plus no-write readback coverage, locked-service propagation, and Step 5 diagnostic-trace coverage passed; changed modules passed `py_compile` and `git diff --check`. The protected 1,119-order source is unavailable locally, so its exact serialized request size remains pending; a blank-value shape lower bound for 2,735 / 57,973 / 32 / 9 target rows is 3,408,467 bytes (3.25 MiB). Google Sheets/UAT2/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Next: replay the hosted failure with diagnostics enabled or locally stage the protected source to record the exact sanitized gateway reason and payload size.

- Date: 2026-10-26; Task: shared Statement refresh-to-reconcile dead-state fix; State: IMPLEMENTED / TESTED / UNCHECKPOINTED / ZERO BUSINESS WRITE; Outcome: fixed the refresh lifecycle where an unavailable rebuilt writer preflight could reach Step 4 with only generic `Reconciliation is not available` text and a disabled forward action. The order-existence adapter now retains the authoritative blocker reason; Step 4 supplies an explicit safe rebuild action using the retained source; stale Review and verified `WRITE_NOT_APPLIED` refreshes intentionally return to Reconcile before a new Review plan can be reached. A session review missing its staged source bytes fails closed and requests re-upload. No Statement business classification, complete-bundle identity, Full Order gate, partial-commit, adjustment, or writer behavior changed. Verification: 30 focused UI tests passed; 8 new MY/SG AppTest/adapter cases passed; `py_compile` and `git diff --check` passed. A broader already-dirty Invoice workflow stabilization fixture still fails before this Statement path (`test_resolved_upload_with_current_new_classification_allows_downstream_happy_path[4]`: missing its expected Invoice continuation control) and is not counted as passing. Google Sheets/UAT2/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Next: hosted MY 1,195-order refresh/reconcile replay and SG smoke with diagnostics enabled.

- Date: 2026-10-26; Task: shared Statement Commit outcome navigation fix; State: IMPLEMENTED / TESTED / UNCHECKPOINTED / ZERO BUSINESS WRITE; Outcome: removed the unconditional `committed=False` refresh-and-Validate transition. The locked shared service now returns `COMMITTED`, `ALREADY_IMPORTED`, `STALE_REVIEW`, `CONFLICT`, `INCOMPLETE_UNSAFE`, `WRITE_FAILED`, or `BLOCKED` from existing authoritative preflight/reason evidence. UI consumes that outcome without duplicate classification: no-new-facts becomes an idempotent Review completion, stale/conflict/unsafe/blocked remain at Review with exact refresh blocker, and verified no-write writer failure remains at Review with an error. The separate session/import instability is explicitly unresolved. Verification: 84 Statement UI/service tests passed with 14 archived skips; 77 Statement persistence/writer/SG-pilot tests passed; one unrelated pre-existing Invoice AppTest fixture failed in the broader dirty-worktree group; `py_compile` and `git diff --check` passed. Google Sheets/UAT2/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Decision required: none. Next: authenticated MY/SG replay with diagnostics enabled, then selective checkpoint only after runtime confirmation.

- Date: 2026-10-26; Task: exact Statement Commit-path audit; State: MIXED COMMIT RESET / RUNTIME TRACE READY / ZERO BUSINESS WRITE; Outcome: proved that a `committed=False` result from locked Commit always refreshes the review and intentionally calls `_set_step(3)`, while the handler itself never invokes staged-source cleanup, recovery removal, or global reset. Success marks `weekly_statement_commit_completed`, requests rerun, and now returns immediately as a defensive no-fallthrough guard. Added opt-in redacted A-G commit transition tracing and focused fake-writer success, writer-not-applied, and no-new-facts coverage. The real protected MY runtime trace is still required to identify why the source disappears. Verification: 6 focused tests passed; changed Python modules compiled; `git diff --check` passed. Google Sheets/UAT2/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Decision required: none. Next: enable `INVOICEGATHER_STATEMENT_STATE_DIAGNOSTICS=1`, replay the hosted MY Commit action, and inspect the first post-rerun trace before changing the uncommitted-result navigation policy.

- Date: 2026-10-26; Task: Statement source-selection runtime crash guard; State: IMPLEMENTED / APPTEST VERIFIED / REAL RUNTIME REPLAY PENDING; Outcome: fixed `_render_source_selection` so a `None` or stale `weekly_statement_selected_source` cannot index the source map and abort a rerun. The selector persists/restores only a known source identity (explicit source type or source-specific staged review); a batch ID alone no longer guesses `Platform Orders`. An absent/unknown value renders an explicit source-selection state, and the keyed control uses session persistence. This is a confirmed crash-path fix but not yet proof that it was the Step 5-to-Step 3 root cause. Verification: 4 focused Streamlit AppTests passed and `app.py`/changed modules passed `py_compile` plus `git diff --check`; the local `streamlit run app.py` server reached HTTP 200 and clean parser imports, but its protected workflow could not be entered without configured credentials, so no authenticated Step 5 replay was performed. Google Sheets/UAT2/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Decision required: provide an authenticated hosted/local session for the required MY/SG runtime replay. Next: replay Review & Commit with diagnostics enabled, then determine whether any remaining state writer still regresses the stage.

- Date: 2026-10-25; Task: shared MY/SG Statement Review workflow state regression; State: IMPLEMENTED / TESTED / UNCHECKPOINTED; Outcome: fixed a reproducible presentation-state regression where `data_import_step` could be reset to Validate while a coherent Statement review and current commit plan remained in session. A shared session-local completed-step checkpoint now restores the highest valid intentional navigation destination across reruns/hydration for both MY and SG. Genuine currency/evidence drift remains at Review with an explicit refresh blocker, hidden old-plan details, and a disabled Commit control; successful Refresh validation atomically replaces the pair and clears the stale marker. No Statement business classification, NEW/EXACT_DUPLICATE/CONFLICT/INCOMPLETE_UNSAFE semantics, Full Order gate, partial commit, adjustment logic, parser, or writer changed. Verification: 5 new focused state/AppTest regressions passed; focused Statement suite 53 passed / 14 archived skips; `py_compile` and `git diff --check` passed. A broader already-dirty workflow-stabilization grouping reports 11 failures outside this checkpoint path and is not counted as passing. The local 1,195-order MY source was unavailable for a new runtime replay; prior verified 1,119 NEW / 76 duplicate / 0 conflict / 0 Full Order gap evidence remains applicable because business calculation code is unchanged. Google Sheets/UAT2/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Decision required: none. Next: hosted MY 1,195-order Review rerun and normal SG review smoke before selective checkpoint.

- Date: 2026-10-27; Task: MY Statement PostgreSQL runtime cutover Phase 2; State: IMPLEMENTED / TESTED / UNCHECKPOINTED; Outcome: added a MY-only fail-closed PostgreSQL Statement repository and wired the existing MY Data Import/financial/adjustment contracts, reporting date authority, and Weekly Billing Statement reads to it. The repository reuses the active Full Order and complete-bundle rules, uses serializable advisory-lock transactions with fresh preflight, and verifies every inserted fact by readback. Phase 2A widened only local Statement varchar schema; four-table counts remained 18,381 / 397,647 / 448 / 20. Verification: 204 focused tests passed; 14 explicitly retired-contract tests and 6 credential-guarded local-control tests were skipped in the normal suite, while all 6 local-control tests (four staged-domain rollback, full-field readback rollback, advisory-lock serialization) passed under the explicit local target; compile and diff checks passed. MY factory smoke rebuilt 18,381 Statement rows, 397,647 components, and 20 adjustments without Google Statement-tab access. Google Sheets write delta: 0; PostgreSQL business-data write delta: 0; local schema write: widening only; Git stage/commit/push: 0; deployment/live business commit: NOT VERIFIED / NOT AUTHORIZED. Next: selective review/checkpoint only after PO authorization.

- Date: 2026-10-25; Task: shared MY/SG complete-bundle Statement partial commit; State: IMPLEMENTED / TESTED / UNCHECKPOINTED; Outcome: replaced weak order-level overlap handling with complete per-order evidence classification across ORDER, native SKU, component, refund, and provenance facts. Mixed sources now skip only exact complete duplicates, gate only new orders against Full Order Current, and construct a child-complete new-facts write set; missing/extra evidence is `INCOMPLETE_UNSAFE`, changed facts are `CONFLICT`, and a fully duplicate source with no new adjustments is `ALREADY_IMPORTED / NO_NEW_FACTS`. Adjustments are classified independently and unlinked events remain preserved. Reconcile and locked Commit use the same fresh-snapshot classifier/write-plan builder, while source control totals remain separate from filtered commit totals. Fresh read-only MY replay: 1,119 new / 76 duplicate / 0 conflict / 0 unsafe; 0 new-order Full Order gaps; RM35,759.06 source total versus RM33,126.14 proposed new released amount; 9 new / 1 skipped / 0 conflicting adjustments; eligible `MIXED`. Verification: focused shared Statement, writer, SG pilot, MY parity, and Streamlit workflow regressions passed; py_compile and diff check passed. Google Sheets/database/schema/business write delta: 0. Git stage/commit/push: 0; Deployment: NOT VERIFIED. Next: Product Owner runtime verification before selective checkpoint.

- Date: 2026-10-25; Task: Shopee SG Analysis Phase A; State: IMPLEMENTED / TESTED / UNCHECKPOINTED; Outcome: enabled the Shopee SG Analysis route with a separate market-aware Full Order / Statement / component / adjustment report model, read-only adjustment lookup, explicit Order Created versus Payout populations, native component drill-down, and separate immutable adjustment timeline. No Invoice/PDF, Product Master, MY financial-layout, Original Sales, Discount, product-allocation, profit, net-result, Billing, or PostgreSQL calculation was introduced. Verification: 49 focused SG financial-read, SG Analysis, and MY Live Analysis service/UI regressions passed; py_compile and diff check passed. Live SG read-only result: 8,173 Current Orders / 10,886 Items; 2,417 settled / 5,756 without Statement; 123,033 components; 17 adjustment events; no MY fallback. UAT2/business/schema write delta: 0. Git: checkpoint pending selective staging and clean-checkout review. Deployment: NOT VERIFIED. Decision required: none. Next: selective checkpoint of SG Analysis Phase A only.

- Date: 2026-10-25; Task: align Shopee SG Consolidated Product Summary status rules; State: IMPLEMENTED / TESTED / COMMITTED / PUSHED; Outcome: removed the SG-only Order Created status branch and routed MY and SG through one exact existing normalized predicate. `To Ship` / `To ship` and `Cancelled` are excluded for both markets; `Canceled` and unknown statuses preserve MY's existing inclusion behavior. The date picker and Order Created Summary now consume the same shared eligible rowset for both markets. Fresh SG read-only verification remains 8,173 Current orders / 10,886 items, 89 dates (2026-07-01 through 2026-09-30), 10,395 eligible items, 317 unique NAV/SKU/USOLF groups, zero duplicate groups, and zero non-SG source rows; live SG has no affected `To Ship` / `To ship` / `Canceled` orders, so totals are unchanged. Verification: 30 focused Summary service/UI tests and `py_compile` passed. UAT2/schema/business write delta: 0. Git: isolated `fix: align shopee sg product summary status rules` checkpoint pushed to `feature/uat2-invoice-persistence-integration`. Deployment: NOT VERIFIED. Next: continue the separately scoped SG reporting work.

- Date: 2026-10-24; Task: Shopee MY any-period Statement integration and active test-contract cleanup; State: IMPLEMENTED / TESTED / COMMITTED / PUSHED; Outcome: MY adopted the SG-style market-scoped Full Order Current existence gate and semantic overlap dedupe, with a shared committed Statement financial read boundary; active tests no longer require Invoice/PDF, Product Master, SKU allocation, or calendar-period gates. 14 explicitly retired V2/Product Master/Invoice-PDF tests remain archived. Verification: 118 focused MY/SG Statement tests passed and 14 archived tests skipped; py_compile and staged diff check passed. Git: `c7908deaaa520794e78e40b4f765c3d5ed2a83b1` pushed to `feature/uat2-invoice-persistence-integration`; UAT2 write delta 0; schema delta 0; deployment NOT VERIFIED. Next: migrate a report surface only after separate Product Owner authorization.

- Date: 2026-10-24; Task: SG Full Order same-row SKU identity persistence for Consolidated Product Summary; State: IMPLEMENTED / TESTED / NO BACKFILL REQUIRED / COMMITTED / PUSHED; Outcome: SG Full Order now requires one deterministic SG PM row to supply nonblank canonical Seller SKU (same-row Seller SKU, or the same-row Parent SKU only when Seller SKU is blank), NAV, `UNIT PRICE USOLF`, and `resolve_sku`; it never derives Seller SKU from `resolve_sku`. SG snapshot hashes now include `resolve_sku` and SG re-sync replaces it from that proven row, while MY preserves its stored resolve-SKU behavior. Live read-only audit of the dedicated SG Current found 8,173 orders / 10,886 items and zero blank persisted `resolved_seller_sku` or `resolve_sku`, so no repair write was authorized. The latest PM has six exact Name+Variation rows (12 existing items, including Papaya row 142 / `260705UFDDASVX`) with both Seller and Parent SKU blank; they are now correctly future-upload blockers rather than candidates for inferred identity. Order Created Date Consolidated Product Summary read 10,395 eligible SG items over 89 dates into 317 unique NAV/SKU/USOLF groups with no duplicate identity groups. Verification: 70 focused Full Order/Summary tests passed; no Google Sheets, MY, schema, or business write. Git: checkpoint `1c1d0fe` (`fix: preserve shopee sg sku identity`) pushed to `feature/uat2-invoice-persistence-integration`; deployment remains NOT VERIFIED. Next: PO/PM owner must populate a same-row canonical Seller/Parent SKU for those six Listing rows before future SG revalidation of those products.

- Date: 2026-10-24; Task: SG Statement Streamlit stale-review fingerprint lifecycle; State: REPRODUCIBLE UI BUG FIXED / UNCHECKPOINTED; Outcome: confirmed this was not a preserved browser session. `_statement_review_stale` compared every market to the legacy MY `FULL_ORDER_STATEMENT_PROOF_RULE_VERSION`; the refreshed SG Current-existence review correctly emits `FULL_ORDER_EXISTENCE_V1`, causing the UI to immediately show Refresh required again. The shared market-aware proof-version helper now expects the configured gate's version, preserving old review staleness and MY V2 behavior while allowing the newly refreshed SG review to remain current. Focused regression proves old review → refresh replacement → no repeated stale state; Streamlit AppTest proves current SG review enables Validate → Reconcile → Review & Commit. The local hosted Streamlit runtime returned HTTP 200. The previously committed native source now returns `ALREADY_IMPORTED`, so no second commit/readback write was attempted; its prior verified SG commit/readback remains authoritative. Verification: 84 focused Statement/market/SG-pilot tests passed; changed modules pass `py_compile`; `git diff --check` passed. UAT2, MY, PostgreSQL, and schema write delta: 0. Git stage/commit/push: 0; deployment: **NOT VERIFIED**. Decision required: none. Next: refresh the existing hosted browser session and use the normal duplicate-success terminal state for the already committed source, or upload a new valid non-duplicate SG Statement to exercise a new live Commit.

- Date: 2026-10-24; Task: Product Owner-authorized Shopee SG Statement settlement pilot; State: `SHOPEE_SG_STATEMENT_SETTLEMENT_PILOT` VERIFIED / LIVE SMOKE COMMITTED / UNCHECKPOINTED; Outcome: introduced a market-aware native Statement source contract and selected it only through the explicit MarketContext route. SG accepts the native `Total Released Amount (S$)` header, GST/BCRS component names, no Shipping Fee Discrepancy worksheet, blank adjustment payout dates where the source leaves them blank, and any valid source period. The active SG gate is exact `(shopee_sg, order_id)` Full Order Current existence plus Statement-internal controls; Invoice/PDF/Product Master/SKU allocation/EXACT-EXPLAINED-NONE checks are not evaluated. Raw Statement order/SKU/component/summary/adjustment facts retain immutable source hash/provenance. The source `Income.released.sg.20260830_20260905.xlsx` parsed cleanly: 427 ORDER rows, 554 SKU rows, 4 adjustments, S$4,496.13 released, S$12.96 adjustment control. Read-only UAT2 preflight confirmed explicit SG destination, writer platform `Shopee SG`, 427/427 exact SG Full Order Current matches, no blockers/duplicates, 985 Statement_Data rows, 21,176 component rows, 34 Summary rows, 4 immutable adjustment events, and zero Invoice updates. One authorized atomic SG commit batch `20261024073616-543e2eca` passed writer exact readback and independent tab readback: all persisted platforms are `Shopee SG`; ORDER payout dates span 2026-08-30 through 2026-09-05; stored released total is S$4,496.13; component amount aggregate is S$8,664.18; and adjustment total is S$12.96. Immediate read-only repeat returned `ALREADY_IMPORTED`. Verification: 3 new SG pilot tests, 30 persistence tests, 56 Statement-import tests, and 24 market-routing tests passed; changed modules pass `py_compile` and `git diff --check`. One broad legacy parser aggregate was stopped after its runner exceeded the bounded tool window and is not counted as passed. MY/Full Order/PM/PDF/schema/PostgreSQL writes: 0; SG Statement business write: 1 verified batch; Git stage/commit/push: 0; deployment: **NOT VERIFIED**. Decision required: none for the pilot; multi-file monthly assembly and downstream dashboard projection remain deferred.

- Date: 2026-10-24; Task: Shopee MY Closing Phase 1 (MY-01 date-source alignment and MY-02 legacy PDF dependency audit); State: MY-01 IMPLEMENTED / TESTED / PUSHED; MY-02 AUDITED; Outcome: Live Analysis and Consolidated Product Summary date provenance is now Full Order Current for Order Created and committed Statement_Data ORDER rows for Payout, joined by normalized `(market, order_id)` with no Invoice/PDF date fallback. Existing legacy Invoice financial calculations, status/refund semantics, Resolve SKU, Compact Barcode PDF, and MY/SG isolation are unchanged. The 808 excluded Live Analysis records are `FULL_ORDER_STATEMENT_PROVED` compatibility layouts produced by `shopee_full_order_reconciliation.py`, not a date-source defect; they remain safely excluded pending a separately approved financial-layout migration. September 2026 before/after results are identical: Order Created 2,168 selected / RM60,468.12 sales and Payout 2,049 selected / RM59,552.65 sales. Verification: 90 focused report/UI/Compact Barcode tests passed; real read-only Google Sheets comparison; no Google Sheets/UAT2/schema/business write. Git: `71d0135` (`feat: align Shopee MY reporting dates with Full Order and Statement`) pushed to `origin/feature/uat2-invoice-persistence-integration`; deployment: **NOT VERIFIED**. Decision required: Product Owner approval before changing legacy financial-layout classification or Payout product/financial authority. Next: stop after MY-01/MY-02; do not begin MY-03.

- Date: 2026-10-24; Task: read-only SG Full Order candidate income source-contract audit; State: CANDIDATES REJECTED / ZERO BUSINESS WRITE; Outcome: compared all 427 unique Order-View orders in the available native SG Statement `Income.released.sg.20260830_20260905.xlsx` against exact `(shopee_sg, order_id)` matches in the July/August/September Full Order exports (424 August and 3 September; no statement order was missing). `Total Amount` equals Statement `Total Released Amount (S$)` for only two zero-value orders and mismatches 425/427: Full Order aggregate 7,441.41 versus Statement 4,496.13, a +2,945.28 difference. `Grand Total` has the same two zero-only matches and 425/427 mismatches: 6,780.30 versus 4,496.13, +2,284.17. Although Grand Total is arithmetically closer in 216 orders, that does not establish source semantics; all 427 are Completed/Normal Order, and the ordinary non-refund population still mismatches. Six Full Order return/refund-marked orders have Statement refund evidence; one order has a separate adjustment event, preserving the lifecycle distinction, but these do not explain the broad pattern. Full Order labels provide no `Order Income`/seller-settlement meaning, while Statement explicitly labels `Total Released Amount (S$)`. Decision: neither Full Order candidate is a viable independent authoritative `order_income` source. Verification: read-only native workbook extraction, exact-ID comparison, and source-header review; no test suite run. Google Sheets, schema, Product Master, Current, Statement, PostgreSQL, cache, and business writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Decision required: provide or approve a distinct source field with explicit seller Order Income semantics before implementing the proposed equality gate.

- Date: 2026-10-24; Task: audit proposed market-neutral monthly Statement settlement architecture with SG as pilot; State: MODERATE COMPLEXITY / BLOCKED ON SOURCE SEMANTICS / READ-ONLY; Outcome: the generic Statement parser, source controls, hashes, immutable Adjustment fingerprints, market context, shared commit lock, fresh-state review, and readback verification are reusable, and the current parser imposes no seven-day duration rule. The active UI/review/plan nevertheless models one weekly workbook and one file hash, while the separate Monthly path accepts one full-calendar-month workbook and is MY-only. More importantly, the Full Order parser and seven-column Current Orders schema contain no `order_income`, and inspected SG July-August-September Full Order exports have no `Order Income` header; they expose `Total Amount` and `Grand Total`, which must not be reinterpreted without a Product Owner source contract. The native SG Statement sample is also not accepted by the current MY-shaped parser because it has no `Shipping Fee Discrepancy` tab and uses `Total Released Amount (S$)` plus GST/SG-specific component names. Active Statement persistence still contains hard-coded `Shopee` row serialization/cross-period filters despite an SG-bound writer, so SG commit is not pilot-safe yet. Recommended design is a market-neutral multi-file monthly batch assembler, exact `(market, order_id)` settlement reconciler, immutable Adjustment event ledger, and controlled settlement projection into Full Order Current after the independent Full Order income authority is resolved. Verification: read-only code and local workbook contract inspection plus native SG parse probe; no test suite run. Google Sheets, schema, cache, Product Master, Statement, Current, PostgreSQL, and business writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Decision required: identify the authoritative Full Order `order_income` source/field and approve SG Statement header/component mapping plus monthly overlap/coverage rules before implementation.

- Date: 2026-10-24; Task: audit hosted Shopee SG Full Order/Product Master/Statement secrets contract; State: READ-ONLY / NO DURABLE RUNTIME CHANGE; Outcome: verified the hosted SG minimum: shared `[product_master.google_service_account]` may read the fixed SG Listing workbook/tab, while `[markets.shopee_sg.uat2_data]` must contain the explicit SG Current/Statement spreadsheet ID and its own named `google_service_account` mapping. The same service-account identity may be represented in both blocks if it has access, but SG UAT2 never falls back to MY UAT2 configuration; SG Statement adds no secret names beyond SG Full Order because it uses the same SG Current/Statement destination plus market-scoped PM. Verification: code/config-template inspection only; Google Sheets, schema, Product Master, and business writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Decision required: none. Next: provision hosted secrets with least-privilege access to the isolated SG Listing and SG UAT2 workbooks.

- Date: 2026-10-24; Task: checkpoint verified Shopee SG Full Order persistence; State: `SHOPEE_SG_FULL_ORDER_PERSISTENCE` VERIFIED; Outcome: Product Owner confirmed the native SG Full Order parse/import and dedicated SG Google Sheet persistence end to end. Exact normalized Product Name + Variation selects one SG Product Master row, with Seller SKU only as deterministic exact-candidate disambiguation; NAV, `UNIT PRICE USOLF`, and `resolve_sku` persist from that same row. No `Price SGD`, MY, or cross-market fallback occurs. The completed-result UI renders before a cleared review, keeps success and `Upload another Full Order` available, avoids duplicate Sync, and cannot report success after failure/partial write. Verification: focused SG Full Order regression, `py_compile`, cached diff check, and staged secret scan. Git: `feat: complete shopee sg full order persistence`; deployment: **NOT VERIFIED**. UAT2/schema/business write delta in this checkpoint: 0. Decision required: none. Next: first controlled SG Statement smoke after populated SG Current.

- Date: 2026-10-24; Task: freeze verified local PostgreSQL foundation; State: `POSTGRESQL_FOUNDATION_READY`; Outcome: Product Owner confirmed the completed local Docker PostgreSQL / SQLAlchemy foundation: eight Sheets-to-PostgreSQL tables, 467,329 rows, source count and business-key equality, 51 exact financial aggregates, 21,805 `resolve_sku` values, DBeaver connection, externalized credentials, and a real rerun with `affected=0`. Production architecture remains `InvoiceGather -> Google Sheets`; PostgreSQL repository integration, application backend, shadow/dual write, hybrid/full cutoff, production deployment, Google Sheets replacement, and hosting choice are frozen/deferred pending new authorization. Verification: focused PostgreSQL tests, `py_compile`, cached diff check, and staged secret scan before checkpoint. Git: `feat: establish verified postgres foundation` on the feature branch; deployment: **NOT AUTHORIZED**. Google Sheets/business write delta: 0; no additional local PostgreSQL migration/data write in this freeze/checkpoint task. Decision required: Product Owner authorization before any PostgreSQL expansion. Next: preserve the local foundation only.

- Date: 2026-10-23; Task: synchronize scope/status with Product Owner Payout, SG Full Order, and PostgreSQL decisions; State: DOCUMENTATION SYNCHRONIZED / NO CODE OR BUSINESS-DATA WRITE; Outcome: recorded that Payout Product Summary still reads canonical `Invoice_Orders` + `Invoice_Items` while its Statement + Full Order Current + Product Master target migration remains pending; superseded stale SG Full Order `Price SGD` wording with same-row deterministic SG Product Master NAV/USOLF/resolve-SKU rules; and recorded the bounded local Docker PostgreSQL/SQLAlchemy one-way migration authorization plus its audit gates. Verification: `git diff --check`; Git stage/commit/push: 0; Google Sheets/UAT2/business write delta: 0; local PostgreSQL write delta: 0. Deployment: **NOT VERIFIED**.

- Date: 2026-10-23; Task: correct SG Full Order post-sync review state; State: IMPLEMENTED / TESTED / ZERO BUSINESS WRITE; Outcome: the Review & Commit renderer previously checked for a staged review before its verified `full_order_sync_result`, so a rerun or cleanup that removed the review displayed a misleading validation warning after a completed sync. The result now takes precedence, preserving the explicit success state and next-upload action. Failed or partial writes still cannot display success because the result is assigned only after `commit_shopee_full_order_review` returns successfully. Verification: focused workflow tests, `py_compile`, and `git diff --check`; UAT2/schema/business write delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**.

- Date: 2026-10-23; Task: fix SG Full Order repeated-upload session-state lifecycle; State: IMPLEMENTED / TESTED / ZERO BUSINESS WRITE; Outcome: root cause was a presentation-only `data_import_step` advancing independently of `full_order_review`, plus no terminal transition from a completed Full Order sync to a fresh workbook. That state could show Upload completed / Validate current while `_full_order_review()` returned `None`, yielding the misleading upload-first warning; July's retained review and batch also blocked a clear August replacement path. Added one atomic next-upload reset that preserves the selected SG scope but removes the prior review, stored bytes, sync result, mismatch/refresh presentation, batch ID, and uploader widget value; the post-sync UI now exposes it. Full Order step normalization fails closed to Upload if no typed review exists. Refresh PM remains limited to the currently retained review and its source bytes. Verification: 6 focused Full Order workflow tests plus 47 state/service/sync tests pass; targeted modules compile; diff check passes. UAT2/Product Master/schema/business write delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Decision required: none. Next: consecutive SG July-to-August UAT verification in the running app.

- Date: 2026-10-23; Task: diagnose SG Full Order Current Orders schema validation blocker; State: SCHEMA DRIFT CONFIRMED / ZERO BUSINESS WRITE; Outcome: a direct read-only fetch from the explicitly configured SG Full Order repository found `Shopee_FullOrder_Current_Orders` with exactly seven headers. B:G match the locked contract, while A1 is `\u00B7` (U+00B7) instead of `market`; the first and only mismatch is position 1. SG configuration resolves to a non-MY destination, so wrong-target routing is ruled out. Current working changes alter only the 14-column Items contract for `resolve_sku`; the seven-column Orders contract is unchanged in code and regression assertions. The exact required remediation is a guarded, Product Owner-approved live edit of `Shopee_FullOrder_Current_Orders!A1` from `\u00B7` to `market`, followed by exact-header readback; no tab recreation, data clearing, or schema-validation weakening is permitted. Verification: 46 focused Full Order schema tests passed; relevant modules compile; `git diff --check` passed. UAT2/Product Master/business write delta: 0; schema write delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Decision required: approve the one-cell SG schema correction. Next: do not retry July validation until the guarded migration is approved and verified.

- Date: 2026-10-23; Task: minimal Full Order PM refresh and SG blank-SKU admission implementation; State: IMPLEMENTED / TESTED / UNSTAGED; Outcome: added Full Order `Refresh PM & Revalidate`, which clears the configured PM LRU, fresh-loads the source market, revalidates retained bytes without a sync, and swaps session review only on success; failure retains the prior review and shows an error. SG Google PM source alone now admits blank-SKU/Parent-SKU Listing rows, with exact Name+Variation uniqueness still required. SG Full Order Current now persists/reads a verified blank `resolved_seller_sku` without deriving it from `Resolved SKU`; MY and PDF retain their existing nonblank requirements. Live isolated July Papaya revalidation: Listing row 142 resolves uniquely with NAV `2000842`, USOLF `9.90`, `resolve_sku` `2000842`, and blank resolved seller SKU. Verification: 69 focused Product Master/Full Order tests pass, py_compile and diff check pass. Business writes/schema migrations: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**.

- Date: 2026-10-23; Task: read-only MY/SG Refresh PM and SG Full Order integration audit; State: DEFECT CONFIRMED / ZERO BUSINESS WRITE; Outcome: `load_configured_product_price_master` is a four-entry process-local LRU cache keyed by explicit MarketContext/settings. Invoice's `Revalidate with latest Product Master` clears that cache, refetches its market source, and revalidates Invoice staging; failure is shown as an error and retains staging. Full Order's Validate path loads the cache without clearing it, stores `ShopeeFullOrderImportReview` in session state, and offers only Continue/Clear, so it neither refreshes nor automatically retries failed Full Order records. SG routes to exact `shopee sg`; MY uses its own settings, with no cross-market cache key. A direct isolated read confirms July Order `260705UFDDASVX` Papaya source row 451 exactly matches raw SG Listing row 142, but row 142 is omitted from the 658 loaded records because both SKU fields are blank; resolver result is `PRICE_NOT_FOUND`. The root cause is loader admission, not stale PM. Verification: source parse plus isolated read-only SG Listing evidence; no tests run; no cache clear/UI refresh/repository write. Business writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**.

- Date: 2026-10-23; Task: SG Full Order minimal PM resolver fix and September read-only verification; State: IMPLEMENTED / PREFLIGHT PASS / UNSTAGED; Outcome: live `shopee sg` Listing refresh has 658 rows, including four Product Owner-maintained BRAGG `FULL_ORDER_MATCH_ONLY` historical identities. The SG resolver previously excluded that subset despite its exact Name+Variation contract; it now considers the complete SG Listing set and still requires exactly one row, with Seller SKU only as deterministic disambiguation. Formerly unmatched BRAGG Order `2609046PD51JB6` (source row 25) resolves to Listing row 379 with NAV `3003901`, USOLF `15.00`, and stored `resolve_sku` `74305001321`. Read-only September result: 2,543 Orders / 3,336 Items; 3,336 matched; 0 unmatched; 0 ambiguous; 0 NAV-incomplete; 0 invalid/missing USOLF; 1 blank optional `resolve_sku`; 0 SKU disambiguations. The former six missing-NAV rows are now complete; no incomplete Current persistence was added because the schema has no explicit safe incomplete status. Verification: 99 focused PM/source/Full Order tests pass; py_compile and diff check pass. Business writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**.

- Date: 2026-10-23; Task: finalize global Shopee MY/SG Statement reconciliation migration; State: `SHOPEE_STATEMENT_FULL_ORDER_ARCHITECTURE_MIGRATION_CLOSED` / PERSISTENCE VERIFIED / CHECKPOINT AUTHORIZED; Outcome: Validate, Reconcile, Review, Ready, fresh pre-commit, and the active writer share one Full Order Current + market-scoped Product Master + Statement result, joined exactly by `(market, order_id)`. Active readiness has `ACTIVE LEGACY INVOICE DEPENDENCIES = 0`; it neither reads nor writes Invoice/PDF evidence, while the legacy Invoice path remains archive-only. Read-only MY replays: committed 2026-10-05 to 2026-10-11 (463 orders) and 2026-09-14 to 2026-09-20 (543 orders) return ALREADY_IMPORTED; 2026-07-20 to 2026-07-26 returns 439/439 Full Order coverage, 439 exact settlements, 0 unexplained, 6 refund SKU rows, 3 adjustments, and Ready. SG writer resolves only `Shopee SG`; automated isolation proves no MY fallback. Deployment/persistence: **VERIFIED** by two Product Owner-confirmed separate real MY weekly Statement smoke commits through the new active flow, including writer/readback. Verification: 181 focused Statement/Full Order/writer/persistence/UI tests pass, py_compile and diff check pass; `NEW_REGRESSION = 0`. Business writes by this checkpoint task: 0; Git checkpoint/push: authorized.

- Date: 2026-10-23; Task: finalize Shopee SG Full Order Product Master enrichment and verify SG persistence configuration; State: IMPLEMENTED / LIVE READ-ONLY PREFLIGHT / ZERO BUSINESS WRITE / UNSTAGED; Outcome: corrected the live SG Listing USOLF header alias (`UNIT PRICE BASED USOLF`) and made SG Full Order resolution name+variation-primary, with Seller SKU only a deterministic exact-candidate disambiguator. The path rejects Parent SKU, Price SGD, aliases, compatibility rows, fuzzy/first-row selection, and duplicate exact Listing rows; resolved Seller SKU, NAV, USOLF unit price, and resolve SKU are carried from one row. Live September source preflight: 2,543 orders / 3,336 items; 3,184 exact name+variation one-row matches; 0 SKU disambiguations; 152 unresolved; 0 ambiguous; 6 missing NAV; 0 missing/invalid USOLF; 1 missing resolve SKU. SG UAT2 setting resolves only to its distinct destination; canonical and Current factories target it; all eight tabs read successfully and Current remains header-only with the 14-column resolve_sku schema. Verification: 126 focused Product Master/Full Order/market/workflow tests passed; changed modules compiled; `git diff --check` passed. UAT2/Product Master/Current/canonical/Statement/schema business write delta: 0. Git stage/commit/push: 0. Deployment: **NOT VERIFIED**. Decision required: resolve the 152 exact-name/variation PM gaps and six missing-NAV Listing rows before any SG Current write. Next: Product Owner review of PM gap remediation; do not write this source until the remaining blockers are resolved.

- Date: 2026-10-23; Task: Compact Barcode PDF POS Price & Discount Allocation; State: IMPLEMENTED / TESTED / VISUALLY VERIFIED / UNSTAGED / ZERO BUSINESS WRITE; Outcome: added Compact-PDF-only per-component POS Price, signed Discount, and Net display beside each Resolve SKU barcode/manual component. The approved allocation divides source Original Sales and Discount equally across every physical scan unit, assigns remainder cents deterministically to earliest Listing-order units, then aggregates back to the component; component Net is allocated Price minus signed Discount. The original source financial grid and all persisted/reporting calculations are unchanged. Baby Noodles (Qty 1, RM49.00 / RM5.88 / RM43.12, four components) displays RM12.25 / RM1.47 / RM10.78 on every component. Verification: focused Compact PDF tests cover the example, signed non-divisible cents, component multipliers, report quantities above one, exact reconciliation, and PDF text; the affected portrait page was rendered and visually checked. UAT2/Product Master/Listing/Current/canonical/Statement/schema/business write delta: 0. Git stage/commit/push: 0. Deployment: **NOT VERIFIED**. Decision required: none. Next: Product Owner operational POS review of the refreshed Compact export.

- Date: 2026-10-23; Task: fix Resolve SKU propagation into Consolidated Product Summary Compact Barcode PDF; State: IMPLEMENTED / LIVE READ-ONLY VERIFIED / ZERO BUSINESS WRITE / UNSTAGED; Outcome: inspected the actual MY UAT2 Current row, September Consolidated Product Summary, Compact adapter, parser, and renderer. The stored Baby Noodles Listing instruction reaches the fresh Compact contract as four valid EAN-13 components (`9555208010555`, `9555208010678`, `9555208010562`, `9555208010586`) with POS Qty 1 each and a single RM43.12 source amount; the report controls remain 293 products / Qty 4,336 / RM92,736.30 Original Sales / RM4,210.45 Discount / RM88,525.85 Amount. Cause was the missing post-success Full Order cache invalidation, not persistence, aggregation, legacy SKU fallback, parser, or renderer. Added success-only invalidation of the 45-second Cross Platform snapshot cache and end-to-end Current-to-rendered-PDF regression tests, including a same-EAN `-N` Seller SKU proving stored `resolve_sku` overrides legacy suffix multiplication. Verification: 22 focused Compact/Full Order workflow tests passed; changed modules compiled; `git diff --check` passed. UAT2/Product Master/Current/canonical/Statement/schema/business write delta: 0. Git stage/commit/push: 0. Deployment: **NOT VERIFIED**. Decision required: none. Next: Product Owner can refresh the Streamlit page after a successful Full Order sync and export the same September Compact PDF.

- Date: 2026-10-22; Task: migrate the first legacy Full Order return/refund gate; State: IMPLEMENTED / TESTED / ZERO BUSINESS WRITE / UNSTAGED; Outcome: a nonblank Full Order Current `return_refund_status` is now informational only and cannot create `Need Additional Evidence` or require Invoice/PDF proof. Statement remains the exclusive financial authority: incomplete or conflicting Statement financial bridge evidence blocks as `Statement Evidence Review`, while ordinary no-refund and reconciled explicit-refund cases proceed on their Statement evidence. Read-only MY inspection of `260722BVJQ6RRF` found a completed 2026-07-22 Full Order with five products and one `Request Approved` marker, but no committed Statement, financial-component, or Adjustment row; therefore no Statement Order Income reconciliation exists yet. Verification: 93 focused Full Order/Statement tests passed; changed services compiled; diff check passed. Git: stage/commit/push 0. UAT2/Product Master/Current/canonical/Statement/business write delta: 0. Schema delta: 0. Decision required: none. Next: refresh/reconcile when an authoritative Statement row for the order is supplied.

- Date: 2026-10-22; Task: Resolve SKU V1 UAT2 schema compatibility and end-to-end verification; State: PASS / MINOR SOURCE-FIDELITY FIX / ZERO BUSINESS WRITE / UNSTAGED; Outcome: read-only UAT2 inspection verified `Shopee_FullOrder_Current_Items` has the exact 14-field header with `resolve_sku` in N1 and 21,767 readable historical rows whose N values are blank. MY `shopee` and SG `shopee sg` Listing loads both resolve the `Resolved SKU` header through their isolated source paths. Fixed only `resolve_sku` repository serialization/readback to preserve leading/trailing whitespace and newlines during updated reuploads; financial fields remain unchanged. Fixed the unconfigured-SG test fixture so it cannot leak configured `streamlit.secrets`. Verification: 316 relevant Full Order/Product Master/Resolve SKU/Compact+legacy Barcode/Cross/Order Created/Statement-Payout tests passed; `py_compile`, `git diff --check`, isolated MY/SG persistence-to-PDF traces, and visual review of 12 MY cases across two pages plus SG all pass. Git: stage/commit/push 0. UAT2/Product Master/Current/canonical/Statement business write delta: 0. Schema write delta: 0. Decision required: none. Next: normal review/checkpoint only if separately authorized.

- Date: 2026-10-22; Task: Resolve SKU V1 Product Owner-approved implementation; State: IMPLEMENTED / TESTED / VISUALLY VERIFIED / UNSTAGED / ZERO BUSINESS WRITE; Outcome: added the nullable Full Order Current `resolve_sku` field only, sourced from the Listing `Resolved SKU` header for MY and SG, while retaining financial `resolved_seller_sku` unchanged. The value preserves full raw Listing text; Current reuploads retain the stored value including blank and Listing-only changes do not trigger historical backfill. The legacy exact 13-column item schema now has an additive migration request plan, but no migration was invoked. Shared parsing powers Compact Barcode PDF display of EAN-13/NAV/manual/multi-component instructions without duplicating source money. Verification: 164 focused regression tests passed; one independent unconfigured-SG settings test was environment-inapplicable because local SG persistence is configured; two representative MY/SG PDFs were rendered and visually checked; no UAT2/production/schema/business write was made. Git: stage/commit/push 0. Deployment: **NOT VERIFIED**. Next: separately authorize a live Current schema migration before any real destination upgrade.

- Date: 2026-10-22; Task: confirm Full Order Current + Statement active Shopee MY/SG architecture; State: PRODUCT OWNER DECISION DOCUMENTED / NO IMPLEMENTATION; Outcome: Scope now makes Full Order Current the operational/order-product authority, Product Master the market-isolated NAV/UNIT PRICE USOLF authority, and Statement the settlement/financial authority, joined exactly by `(market, order_id)`. It supersedes old PDF/Invoice coverage, Missing Invoice Source, mandatory Invoice-to-Statement, and 100% Invoice-source payout/export blockers; PDF/Invoice tables remain legacy audit evidence and are not deleted. Verification: documentation-only review; no tests run. Git: no stage/commit/push. UAT2/Product Master/Current/canonical/Statement/business write delta: 0. Schema delta: 0. Decision required: separately scope implementation migration of remaining legacy gates. Next: implement only after Product Owner authorizes a focused migration.

- Date: 2026-10-22; Task: Resolve SKU V1 baseline reconciliation; State: SCHEMA DECISION REQUIRED / NO IMPLEMENTATION; Outcome: confirmed the live Full Order Current item schema is an exact existing 13-column contract, where `source_seller_sku` and `resolved_seller_sku` have different established meanings and cannot safely be repurposed for the Listing `Resolved SKU` POS instruction. A durable POS mapping needs its own additive field(s) plus a migration for existing Current tabs. The current replacement-style Full Order sync would otherwise recompute and overwrite a stored mapping after a Listing edit, contrary to the Product Owner rule; exact immutable-update behavior therefore needs approval. Verification: branch `feature/uat2-invoice-persistence-integration`, HEAD `26c7a1e` (with `4f7062a` an ancestor), working tree inspected; no tests or business operations run. Git: stage/commit/push 0. UAT2/Product Master/Current/canonical/Statement write delta: 0. Schema delta: 0. Decision required: approve the additive Full Order Current POS mapping schema and its immutable-update semantics, or authorize an explicitly non-persistent current-batch-only implementation. Next: do not implement persistence or render a potentially stale/ambiguous POS mapping until that decision is made.

- Date: 2026-10-22; Task: Reusable Compact Barcode PDF V1; State: IMPLEMENTED / TESTED / VISUALLY VERIFIED / UNSTAGED; Outcome: added a UI-independent compact report contract, Cross Platform adapter, and portrait ReportLab renderer, then added the second Consolidated Product Summary download button while preserving the legacy table-PDF path. Numeric terminal bundle suffixes now produce approved multipliers and Scan Qty; barcode display retains the approved valid-EAN-13 full/prefix convention; unavailable identifiers require manual entry; normal negative discounts yield an override only on exact Decimal cent reconciliation; bundle physical-unit pricing remains review-required. Verification: 71 focused Compact/Cross/legacy-Barcode/UI tests passed, changed Python modules compiled, `git diff --check` passed, and a representative nine-case PDF was rendered and visually inspected across two pages. Git: stage/commit/push 0. UAT2/Product Master/canonical/Statement/business write delta: 0. Schema delta: 0. Decision required: real POS scanner validation remains operationally required. Next: Product Owner review of the new Compact PDF in the local UI.

- Date: 2026-10-20; Task: configure local Shopee SG UAT2 database access; State: SG_CONFIG_READY / READ-ONLY VERIFIED / ZERO BUSINESS WRITE; Outcome: added only the ignored local `markets.shopee_sg.uat2_data` secrets block with SG spreadsheet `19DV6ubFyIrjJ-8lbNNqjn8P69yjSaCS0cUoNjEHZWGo` and the existing MY UAT2 service-account file path, without exposing credential contents or changing application routing. Fresh settings resolution proves MY remains distinct, SG resolves to its approved destination, and SG cannot resolve to MY. The shared service account read all eight required SG tabs, each with header-only data. An in-memory one-row SG Full Order validation reached Current review successfully; no source commit, schema action, or Google Sheets write was attempted. Verification: explicit destination assertions, eight-tab read-only smoke, and SG Full Order review PASS; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Next: `READY_FOR_FIRST_REAL_SG_FULL_ORDER_PREFLIGHT`.

- Date: 2026-10-20; Task: complete Shopee SG database routing; State: IMPLEMENTED / UNSTAGED / ZERO BUSINESS WRITE; Outcome: centralized explicit MY/SG UAT2 settings now route canonical `Invoice_Orders` / `Invoice_Items`, Full Order Current, and the shared Statement writer to their selected destination, and reject `MISSING_MARKET_CONTEXT`, unsupported market, missing SG configuration, and SG-to-default-MY destination reuse before Google Sheets I/O. UI persistence construction passes the active market to Product Master and persistence together; Full Order source-market protection remains unchanged. SG Statement writer uses persisted platform `Shopee SG` but no SG Statement upload/commit UI was enabled; Monthly Statement and Billing remain MY-only. SG database schema was not migrated and no real MY/SG business row, Product Master row, canonical row, Statement row, or Current row was written. Verification: 49 focused market/Full Order/workflow tests passed under a repository-local pytest base temp; changed modules compiled and diff check passed; `NEW_REGRESSION = 0`. Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Next: `READY_FOR_FIRST_REAL_SG_FULL_ORDER_PREFLIGHT` only after a real SG Full Order source and live SG configuration are supplied.

- Date: 2026-10-20; Task: add Shopee SG Full Order source-market detection and cross-market guard; State: IMPLEMENTED / UNSTAGED / ZERO BUSINESS WRITE; Outcome: native Full Order `Country` is the sole market authority, with deterministic MY/SG detection, mixed/missing/unsupported Country rejection, optional impossible Country/Currency evidence rejection, and no dependence on UI selection. A `MARKET_MISMATCH` carries the original bytes/hash through explicit Switch-or-Cancel UI confirmation; target Product Master and Current repository are not read until source market matches. Current identity already isolates `(market, order_id[, item_index])`, and snapshot hash includes market, so no schema change was required. Local SG persistence readiness is blocked by missing market-specific destination configuration; therefore no live SG preflight or write occurred. Focused Full Order/market-context/workflow regression passed before the UI-platform suite exceeded the local 30-second command window: 46 tests passed; changed Python modules compile and diff check pass. Git stage/commit/push: 0; deployment: **NOT VERIFIED**. Remaining requirement: configure the independent Shopee SG UAT2 destination, then run the first real source preflight before any write.

- Date: 2026-10-20; Task: finalize Consolidated Product Summary / Full Order Current migration checkpoint; State: `CONSOLIDATED_PRODUCT_SUMMARY_MIGRATION_CLOSED` / PASS / COMMITTED / PUSHED; Outcome: Product Owner closed Full Order integration, Current-only Order Created reporting, canonical-only Payout reporting, gross/original refund semantics, the September RM263.70 gross-vs-net explanation, and May genuine-All-Order missing-only backfill. Fresh live read confirms the 05-11 Weekly baseline for committed batch `20261020035550-46d32fdd` (463 orders / 190 products / Qty 1,007 / RM21,588.80 original sales / RM21,037.53 amount / RM551.27 discount); target Current refund item `26100849V7RC9P` remains Qty 2 / RM23.22. Focused Summary/UI/Full Order/Weekly/Statement regression suite: 133 passed; changed Python modules compile; diff check passed. This closing task made 0 UAT2 business, Product Master, Statement, canonical, or schema writes. `NEW_REGRESSION = 0`. Product Owner-authorized checkpoint `63e912450bb093e86f3b540955fb8875a8e3b7b6` was pushed normally to `origin/feature/uat2-invoice-persistence-integration`; deployment remains **NOT VERIFIED**.

- Date: 2026-10-20; Task: final Shopee MY Statement 2026-10-05 to 2026-10-11 production verification and complete-worktree checkpoint authorization; State: PASS / REAL STATEMENT ALREADY COMMITTED / CHECKPOINT PENDING; Outcome: Product Owner confirmed the real UI at 463 Statement Orders / 77 Already Verified / 386 Ready from Full Order / 0 additional evidence. The exact `Income.released.my.20261005_20261011.xlsx` hash `67389593053e9da9b1f457e44730ac79bc05581aced6df1caf4c5bd4f86e9f06` is already uniquely COMMITTED as batch `20261020035550-46d32fdd`: 463 ORDER + 625 SKU + 2 ADJUSTMENT `Statement_Data` rows, 32 Summary rows, and 23,201 Financial Component rows. Direct idempotency re-review returns `ALREADY_IMPORTED`; no duplicate write was attempted. Canonical readback has 463 unique orders / 625 unique items, 386 Full Order-derived orders / 524 items, no missing parents or duplicate keys. Refund target `26100849V7RC9P` retains quantity 2 and subtotal RM23.22 with Statement Product RM23.22, Refund -RM11.61, Net RM11.61; no returned quantity exists or is inferred, and Current marker remains `Request Approved`. Former PM blocker `260930DEN3F8AY` is resolved through Parent SKU `9551031011158` / NAV `5004755` with deterministic Statement Product ID `42933357946` mapping; the PM seller-SKU cell is source-blank, not ambiguous. Current snapshot hash stayed `e640405022231dcf6005feec9ad427fe2e261c6fe8b97ded026cef913828f08d`; Product Master remained read-only. Weekly Billing is selectable and export-ready: 463 orders, 190 products, 1,007 quantity, RM21,588.80 original sales, RM21,037.53 amount, RM551.27 discount; Product Price RM21,037.53, Refund -RM107.52, Voucher/Rebates -RM405.22, Shipping -RM239.82, Fees -RM6,165.03, Released RM14,119.94, Adjustment -RM130.96, all four financial controls pass at RM0.00 difference; Excel and barcode-PDF preparation passed. Verification: 214 focused Full Order/Statement/UI/writer/Billing/PM tests passed; 152 further persistence/export/Product Summary tests passed. Two unchanged legacy Cross Platform exclusion-panel tests fail because they assert UI panels removed by the approved committed-data Cross Platform contract; classification `BASELINE_EXISTING`, not a new regression. `py_compile` and `git diff --check` passed. UAT2/Current/Statement/canonical/Product Master/schema writes by this verification task: 0; Deployment: **NOT VERIFIED**. Git checkpoint/push is the authorized next operation.

- Date: 2026-10-20; Task: Shopee MY Statement Validate UI/backend parity only; State: PASS / READY_FOR_PRODUCT_OWNER_UI_CONFIRMATION / ZERO BUSINESS WRITE / UNSTAGED; Root cause: the active Streamlit `weekly_statement_review` had been built before the narrow Full Order refund-proof change and remained in session, so Validate rendered its stored `statement_evidence_coverage` (385 Ready / 1 additional) without rebuilding it. It was not a different XLSX, PM cache, Current snapshot, or a second UI blanket return/refund block. Fix: version the Full Order Statement proof contract in review evidence; an existing review lacking that current version is now marked stale, its obsolete blocker table is hidden, and the existing `Refresh validation` action clears PM cache and rebuilds from the live UAT2/Current/PM services. Fresh read-only validation of `Income.released.my.20261005_20261011.xlsx` (hash `67389593053e9da9b1f457e44730ac79bc05581aced6df1caf4c5bd4f86e9f06`, 2026-10-05 to 2026-10-11, 463 order rows, 625 SKU rows) gives 463 Statement Orders / 77 Already Verified / 386 Ready from Full Order / 0 Need Additional Evidence; `26100849V7RC9P` is Ready and `260930DEN3F8AY` has no additional-evidence entry. The manual browser confirmation remains pending: reload the local app, click `Refresh validation`, verify Continue is enabled, and do not proceed to reconcile or commit. Verification: 86 focused UI/Statement/Full Order tests and 95 adjacent reconciliation/persistence/PM/workflow tests passed; `py_compile` and `git diff --check` passed. UAT2/Current/Statement/canonical/Product Master/schema writes: 0; real Statement commit: NO; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**.

- Date: 2026-10-20; Task: read-only post-Product-Master recheck for Weekly Statement 2026-10-05 to 2026-10-11; State: PASS / READY_FOR_REAL_05_11_STATEMENT_COMMIT / ZERO BUSINESS WRITE; Outcome: fresh Google Sheets Product Master, UAT2 canonical, Full Order Current, and Statement reads now give 463 Statement Orders / 77 Already Verified / 386 Ready from Full Order / 0 Need Additional Evidence. `26100849V7RC9P` remains Ready from Full Order; no unresolved identity results remain, including previously blocking `260930DEN3F8AY`. Review and commit readiness are both true. UAT2/Current/Statement/canonical/Product Master/schema writes: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Next: explicitly authorized real Statement commit only.

- Date: 2026-10-20; Task: implement approved narrow Full Order + Statement refund-proof rule for `26100849V7RC9P`; State: IMPLEMENTED / TARGET PASS / ZERO BUSINESS WRITE / UNSTAGED; Outcome: a nonblank Current `return_refund_status` now triggers strict proof rather than an automatic PDF exception. Marked Current orders pass only with explicit non-zero Statement refund evidence, exact settlement bridge, deterministic ITEM allocation, and a one-to-one match between every refunded Statement member and the marked Current item(s); missing/zero refund, GROUP or unresolved allocation, identity failure, merchandise mismatch, non-exact settlement, and canonical collision remain fail-closed. No returned quantity field was added or inferred; marker, original quantity and `line_subtotal` are preserved. Synthetic preparation proves item Statement Product Price / Refund / Net fields are RM10.00 / -RM2.00 / RM8.00. Real read-only `Income.released.my.20261005_20261011.xlsx` coverage is 463 Statement Orders / 77 Already Verified / 386 Ready from Full Order / 0 Need Additional Evidence: target marker `Request Approved`, Current quantity 2, subtotal RM23.22, SKU `9555208104421` / NAV `5000139`, Statement refund -RM11.61, ITEM allocation source row 218 / sequence 215, and EXACT bridge now pass; Statement quantity remains unavailable / not stored. The batch remains unable to continue because unrelated `260930DEN3F8AY` has an unresolved Statement Product Master family/name conflict (Product ID `42933357946`), so real commit and deployment are **NOT VERIFIED**. Verification: 83 focused Full Order / Statement / UI tests and 148 adjacent reconciliation, persistence, Weekly, Product Summary and workflow tests passed; source-PDF refund fixture tests are MISSING_FIXTURE because archived PDFs are absent; `py_compile` and `git diff --check` passed. UAT2/Current/Statement/canonical/Product Master/schema writes: 0; Git stage/commit/push: 0. Next: resolve the unrelated `260930DEN3F8AY` identity evidence before `READY_FOR_REAL_05_11_STATEMENT_COMMIT`.

- Date: 2026-10-19; Task: read-only refund-canonical requirement audit for `26100849V7RC9P`; State: RETURNED_QUANTITY_NOT_REQUIRED / ZERO BUSINESS WRITE; Outcome: the 44-column Order / 23-column Item canonical schemas and domain models have no returned/refund/net quantity field. Source-only `source_return_refund_quantity` is deliberately neither persisted nor fingerprinted. Statement V2 explicitly records Statement quantity as unavailable; its safe ITEM refund allocation requires deterministic item attribution and a signed Statement amount, not returned quantity. An in-memory marker-suppressed proof of the target (no write) gives ITEM / merchandise PASS / settlement EXACT / refund allocation resolved with original Qty 2, line subtotal RM23.22, Order refund -RM11.61 and final RM124.65. Existing Weekly and Consolidated Product Summary quantities remain original purchased quantity and their product-row amounts currently derive original `line_subtotal`, not Statement net selling amount; Live Analysis recognizes only NORMAL_ORDER / RETURN_REFUND, so FULL_ORDER_STATEMENT_PROVED is an existing separate layout/reporting limitation. Barcode and export consume those existing summary rows. The Current marker gate is an uncommitted fail-closed condition in `shopee_full_order_reconciliation.prove_full_order_current_against_statement()` and `shopee_statement_import._statement_inputs_with_full_order()`; it is broader than current canonical/downstream quantity requirements, but remains unchanged pending a narrow approved rule design. UAT2/Current/Statement/canonical/Product Master writes: 0; schema delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Next: `READY_FOR_NARROW_REFUND_PROOF_RULE_DESIGN`.

- Date: 2026-10-19; Task: read-only single return/refund exception assessment for Weekly Statement 2026-10-05 to 2026-10-11 / `26100849V7RC9P`; State: PDF_EXCEPTION_REQUIRED / ZERO BUSINESS WRITE; Outcome: Current has 10 deterministic product rows and one `Request Approved` marker on two-unit 5 Color Beans SKU `9555208104421` / NAV `5000139`. The uncommitted source Statement gives complete financial proof: Product Price RM195.32 equals Current subtotals, SKU and Order refund evidence is -RM11.61, and the exact financial bridge totals RM124.65 released on 2026-10-10. The refund SKU is uniquely proven, but no Current, Statement, canonical, Adjustment, or local exact-ID PDF evidence proves returned/refunded quantity, post-refund line amount, or an independently dated refund event. Existing canonical and committed Statement/Adjustment rows are absent; adjustments: none. Therefore do not infer one returned unit or canonicalize without the exact Order PDF. Gate remains 77 Already Verified / 385 Ready from Full Order / 1 Need Additional Evidence. UAT2/Current/Statement/canonical/Product Master writes: 0; schema delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Decision required: provide `26100849V7RC9P` PDF only. Next: `WAITING_FOR_ORDER_26100849V7RC9P_PDF`.

- Date: 2026-10-19; Task: Consolidated Product Summary sidebar-width follow-up; State: IMPLEMENTED / TESTED / READY FOR ISOLATED COMMIT; Outcome: expanded navigation width is 18rem so `Consolidated Product Summary` is fully visible. The width constraint applies only to the expanded sidebar; collapsed sidebar and child width are explicitly 0, allowing the main view to regain the full available width. No reporting, data, UAT2, Product Master, schema, or business-data behavior changed. Verification: 2 navigation UI tests, syntax, and diff checks passed. Deployment: **NOT VERIFIED**.

- Date: 2026-10-19; Task: finalize Consolidated Product Summary UI, Shopee SG platform filter, and barcode prefix fallback; State: IMPLEMENTED / TESTED / READY FOR ISOLATED COMMIT; Outcome: renamed the reachable Cross Platform Summary UX to Consolidated Product Summary; the concrete platform filter is now Shopee MY / Shopee SG with no All, MY fallback, or currency merge. SG uses its own persisted identity, configured source, and SGD presentation; no matching SG rows is an ordinary empty result. Date Type and Date range behavior is preserved. The PDF keeps the full visible SKU but may barcode only a valid EAN-13 prefix before the first hyphen (for example `9555208105145-1 Lter` -> `9555208105145`); invalid candidates remain unavailable. UAT2/Product Master/schema/business-data write delta: 0. Verification: Cross/UI, market/navigation, barcode/export tests passed; two known obsolete legacy session-based Cross exclusion tests remain baseline failures. Git: isolated commit and push pending. Deployment: **NOT VERIFIED**. Decision required: none.

- Date: 2026-10-19; Task: narrow Weekly Statement validation gate fix for Full Order Current evidence; State: PASS / ZERO BUSINESS WRITE / UNSTAGED; Outcome: visible coverage is now Already Verified / Ready from Full Order / Need Additional Evidence. Strict Current proof isolates missing, cancelled, return/refund, and incomplete-financial evidence before one strict proof of remaining candidates, with per-order fallback only for an exact-proof failure; a blocker no longer discards clean bundles. Read-only `Income.released.my.20261005_20261011.xlsx` result: 463 / 77 / 385 / 1; `26100849V7RC9P` remains Completed / `Return/refund request detected.` and correctly blocks forward navigation. Verification: 100 Statement/writer/UI, 15 Current/workflow, 2 Statement-navigation, and 66 Weekly/Product Summary tests passed; NEW_REGRESSION = 0. UAT2/Current/Statement/canonical/Product Master writes: 0; schema delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Next: `READY_FOR_SINGLE_EXCEPTION_RESOLUTION_AND_REAL_STATEMENT_COMMIT`.

- Date: 2026-10-19; Task: Cross Platform Summary Date Type filter; State: IMPLEMENTED / TESTED / COMMITTED; Outcome: added visible `Date Type` with **Order Created Date** as the default and `Payout Date` as the alternative, plus a `Current view` label. Both modes share the evidence-constrained inclusive From/To calendar and use the identical filtered rowset for dashboard, Product Summary, and Barcode PDF. `Date range` is aligned after To Date, defaults to `Selected dates`, and its `All dates` option visibly locks the first/last valid endpoints for the complete valid interval. In selected-date mode, To Date only permits valid dates later than From Date. Order Created Date mode includes committed non-cancelled Shopee rows with `order_created_date`, even before payout evidence exists; Payout Date remains payout-evidence-only. Platform/date-type switches clear stale calendar values. UAT2/Product Master/schema/business-data write delta: 0. Verification: 24 Cross Platform service/UI/calendar tests passed; changed modules compiled; `git diff --check` passed. Git: `54bbabd` (`feat: add Cross Platform date type filter`), not pushed. Deployment: **NOT VERIFIED**. Decision required: none.

- Date: 2026-10-19; Task: read-only Weekly Statement validation-gate versus Full Order Current audit for `Income.released.my.20261005_20261011.xlsx`; State: UI_GATE_OBSOLETE / READ-ONLY / ZERO BUSINESS WRITE; Outcome: 463 Statement Order IDs resolve to 77 canonical Orders plus 386 canonical-missing Current Orders. Individual strict Current-to-Statement proof passes for 385, while `26100849V7RC9P` is correctly fail-closed because one Current item has `return_refund_status = Request Approved`; no Statement Order is missing from both sources. Current MY snapshot has 3,906 Orders / 5,439 Items with order-created range 2026-09-01 to 2026-10-18; all 463 target Orders are present (relevant range 2026-09-10 to 2026-10-10). The UI result of 77 Found / 386 Missing is reproduced, but is misleading: `review_statement_upload()` invokes `_statement_inputs_with_full_order()` before coverage; its all-or-nothing proof discards all provisional Current bundles when the single marker blocker exists, then legacy canonical-only `invoice_coverage` blocks navigation and labels every canonical-missing Order as an invoice requirement. No Product Master, UAT2, Current, Statement, canonical, schema, or code write; no Git stage/commit/push. Decision required: approve a narrow validation-summary/routing change that reports canonical verified, individually Current-provable, and additional-evidence orders separately while retaining every fail-closed proof rule. Next: `READY_FOR_NARROW_STATEMENT_VALIDATION_GATE_FIX` after approval.

- Date: 2026-10-19; Task: finalize Shopee Weekly Statement -> Full Order Current -> canonical wiring; State: PASS / READY_FOR_CHECKPOINT / ZERO UAT2 BUSINESS WRITE; Outcome: existing canonical orders remain primary; only clean canonical-missing MY Statement IDs can use Full Order Current with Product Master and exact Statement proof to append canonical Order/Items plus Statement evidence, without PDF. Cancelled, return/refund, incomplete PM, incomplete Statement, mismatch, and collision cases remain fail-closed. Fixed a false `STALE_REVIEW:INVOICE_SNAPSHOT_CHANGED` result by carrying the original review `uploaded_at` into Full Order provisional bundles so their audit fingerprint remains stable from Review to Commit. End-to-end regression proves clean Current-only order -> canonical insertion -> Statement commit -> Weekly summary (RM10.00 `line_subtotal`, RM12.00 persisted PM `unit_price`), Current unchanged, collision zero-write, and duplicate re-upload `ALREADY_IMPORTED`. `actual_selling_unit_price=None` is safe because current Weekly/Cross reporting uses `line_subtotal` and `unit_price`; barcode/export consumes ProductSummary rows. Full Order filename/snapshot provenance persists through legacy `source_pdf`/`source_hash`; no consumer requires a PDF suffix or PDF-byte hash. Read-only UAT2 planning found zero clean canonical-missing candidates among 2,154 Current/Statement intersections, so no live test import occurred. Verification: 187 core and 5 workflow UI tests passed; two old Cross session-exclusion assertions remain baseline failures under the locked committed-data-only UI. UAT2/Product Master/canonical writes: 0; schema delta: 0; Git stage/commit/push: 0; Deployment: **NOT VERIFIED**. Next: selective production checkpoint only.

- Date: 2026-10-18; Task: read-only Shopee MY All-Order Current-to-Statement settlement reconciliation audit; State: STATEMENT_COVERAGE_INSUFFICIENT / READ-ONLY / ZERO BUSINESS WRITE; Outcome: fresh UAT2 reads confirm 2,293 unique MY Current Orders / 3,155 Current Items and nine committed MY Statement periods from 2026-08-03 through 2026-10-04 (4,928 unique Statement Order IDs). Exact Order-ID lookup finds 2,112 Current Orders with unique complete Statement evidence and 181 **NOT FOUND IN LOADED STATEMENTS**; there are zero conflicting or incomplete Statement proofs. For all 2,112 evidenced Orders, Current Product Subtotal equals Statement Product Price exactly (RM87,017.48), and all 2,112 existing Statement financial-component bridges pass within the established RM0.02 tolerance; no Product short/over or bridge failure remains. Coverage is partial, not an unpaid/overdue conclusion: all 181 not-found Current Orders comprise 138 Cancelled, 37 still in the return window, 4 Shipping, and 2 Completed (`260924SJKPRQFT`, `260924TVAY7YBW`), while Statement history ends on 2026-10-04. Financial refund evidence covers 21 Orders (RM-646.05 signed); 27 Orders have Current operational Return/Refund markers, but no independently dated refund event proves a post-payout refund. Two linked Current adjustments are both post-payout (RM-3.27 for `260909J5ST4Y03`, RM-19.29 for `260910MBG29DWF`); six other linked adjustments are outside the Current population and none is structurally unlinked. Golden source malformed-dimension recovery confirms 2,049 Orders / 2,845 Items: all 2,049 exist in loaded Statement history, 1,891 intersect Current and reproduce exact product amounts; remaining 158 are outside Current. No UAT2/Current/Statement/Canonical/Product Master/schema/code/Git write; local audit artifact not created. Decision required: load later Statement periods, then refresh coverage before interpreting any not-found Order as a settlement exception or defining overdue logic. Next: read-only coverage refresh after the next Statement import.
- Date: 2026-10-18; Task: execute first Shopee MY All-Order Current Store shadow sync; State: PASS / LIVE CURRENT WRITE + READBACK VERIFIED; Outcome: Product Owner decision recorded: **FULL ORDER CURRENT SOURCE = ALL ORDER** and **COMPLETED / ESCROW-PAID = SETTLEMENT GOLDEN SUBSET ONLY**. Source `D:\download material\september all orderid.xlsx`, sheet `orders`, uses `Country = MY` for all 3,155 rows and native-dimension fallback; it parsed 2,293 Orders / 3,155 Items. Fresh Google Sheets MY Product Master resolution was 3,155 / 3,155, with 0 PM enrichment gaps and 0 ambiguities. Preflight confirmed exact 7/13 Current headers and 0 / 0 business rows. First sync: 2,293 NEW / 0 UPDATED / 0 UNCHANGED / 0 Need Review; readback: 2,293 Current Orders / 3,155 Current Items, unique keys, every Item parented, every row MY, and populated SHA-256 snapshot hashes. The identical re-upload gave 0 NEW / 0 UPDATED / 2,293 UNCHANGED / 0 Need Review, zero duplicates, and no Current-content change. The initial API attempt was rejected before application because the default 1,000-row Current grids were too small; a narrow writer fix now fresh-reads row capacity and appends only the required empty Current grid rows before its two controlled data replacements. Retry preflight reconfirmed Current 0 / 0. Full pre/post content hashes and counts prove unchanged canonical tables: Invoice_Orders 5,189; Invoice_Items 7,344; Statement_Data 11,901; Statement_Financial_Components 256,511; Statement_Summary 288; Order_Adjustments 8. No Product Master, SG, Monthly, Invoice, Statement, or Order_Adjustments business write; Current header/field schema delta 0. Verification: 63 Full Order / PM / Statement reconciliation / Weekly financial-summary / Product Summary / workflow tests passed; NEW_REGRESSION = 0. Git stage/commit/push: none; deployment: **NOT VERIFIED**. Next: `READY_FOR_SETTLEMENT_RECONCILIATION`; do not start it automatically.
- Date: 2026-10-18; Task: locate and confirm the approved Shopee MY September Full Order source before shadow sync; State: MY_SOURCE_CONFIRMED / READ-ONLY / ZERO WRITE; Outcome: this read-only task identified `D:\download material\Order.completed.escrow_paid_date.20260901_20260930\Order.completed.escrow_paid_date.20260901_20260930_part_1_of_1.xlsx`, sheet `orders`, as the MY 2,049-Order / 2,845-Item settlement corpus. It uses MY evidence (`Country = MY`, MY-specific shipping/tracking evidence), parses with the required native-dimension fallback, and a fresh Google Sheets MY Product Master read resolves 2,845 / 2,845 with 0 issues, including 0 Garbanzo and 0 Cranberry issues. `Order.all.order_creation_date.20260901_20260930.xlsx` is SG (`Country = SG`, 2,543 / 3,336); `september all orderid.xlsx` is MY but a different 3,155-item / 2,293-order corpus. No UAT2, Current Store, Product Master, schema, canonical/Statement, or source-data write was attempted. Git stage/commit/push: none. Deployment: **NOT VERIFIED**. Supersession: the later Product Owner decision assigns this completed / escrow-paid workbook to settlement golden/regression only; `september all orderid.xlsx` is the operational Current source.
- Date: 2026-10-17; Task: approved Shopee MY September Full Order Current Store shadow sync; State: BLOCKED BEFORE BUSINESS WRITE / ZERO WRITE; Outcome: the available `Order.all.order_creation_date.20260901_20260930.xlsx` source and fresh Google Sheets MY Product Master read do not meet the Product Owner-approved sync baseline. Actual source parsing found 2,543 orders and 3,336 items (expected 2,049 / 2,845), while PM resolution produced only 714 complete orders / 830 Current items with 2,246 `PM_ENRICHMENT_REQUIRED` issues (expected 2,845 / 2,845 and 0). Per the approved stop rule, Current sync, canonical/Statement reads-for-write, UI commit, replacement, and re-upload were not attempted. Fresh UAT2 Current read confirms exact 7/13 headers and 0 Current Orders / 0 Current Items; no current, canonical, Statement, Product Master, schema, or source-data write occurred. Local contract/regression verification: 62 Full Order, workflow-activity, Product Master, Statement reconciliation, Weekly financial-summary, and Product Summary tests passed; NEW_REGRESSION = 0. Deployment: **NOT VERIFIED**. Git stage/commit/push: none. Decision required: provide or identify the intended September Full Order workbook / approved new source baseline, then rerun full validation before any UAT2 business write.
- Date: 2026-10-17; Task: Full Order Review/Sync unsupported workflow activity fix; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: the Full Order sync button now reuses the existing `Processing` workflow activity instead of the unsupported unregistered `Syncing` value. The existing `try`/`finally` lifecycle clears activity state after both successful sync and a surfaced sync failure; unknown activity names remain rejected. Verification: 15 focused Full Order workflow, workflow-navigation, Statement activity, and Current sync regressions passed; syntax and diff checks passed; NEW_REGRESSION = 0. UAT2 schema/business, Product Master, and canonical writes: 0. Deployment: **NOT VERIFIED**. Git stage/commit/push: none. Decision required: none; Next: Product Owner may retry the Full Order Review/Sync path, which will now expose any real underlying sync error.
- Date: 2026-10-17; Task: Product Owner-approved minimal Shopee Full Order Current store migration; State: IMPLEMENTED / TESTED / UAT2 SCHEMA VERIFIED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: created exactly `Shopee_FullOrder_Current_Orders` (7 fields) and `Shopee_FullOrder_Current_Items` (13 fields) in UAT2, each with its header row only and 0 business rows. Current is the mutable operational snapshot; Statement remains settlement authority; `Invoice_*` remains Statement-proved canonical history. Current persistence no longer stores `platform`, `source_type`, item-level provenance, or workbook-wide `source_hash`; `return_refund_status` remains persisted. The concrete adapter now computes one deterministic order-level SHA-256 `snapshot_hash` from market/order facts and numerically ordered persisted items, excluding file provenance and sync time. Pre/post UAT2 readback proves exact Current headers, 0 Current business rows, and unchanged canonical schema/count/content snapshots: Invoice_Orders 5,189; Invoice_Items 7,344; Statement_Data 11,901; Statement_Financial_Components 256,511; Statement_Summary 288. Read-only Current repository smoke passed (0 Orders / 0 Items), resolving the missing-Current-tab read failure without a business sync. Verification: 103 Full Order/Product Master/Statement tests, 4 Weekly representatives, and 9 Product Summary tests passed; NEW_REGRESSION = 0. SG Product Master remains blocked by 100 blank-NAV source rows across 23 identities plus 1 Parsnip gap. UAT2 writes: 2 new tabs / 2 header rows / 0 business rows; Product Master writes: 0; canonical business writes: 0; deployment: **NOT VERIFIED**. Git stage/commit/push: none. Next: `READY_FOR_FULL_ORDER_SHADOW_SYNC` pending Product Owner/reviewer approval; do not import September Full Order rows automatically.
- Date: 2026-10-16; Task: Product Owner-approved Shopee Full Order Sync V1 minimal implementation; State: IMPLEMENTED / TESTED / RUNTIME DATA BLOCKED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: added one concrete MY/SG Full Order XLSX parser with malformed-dimension recovery, one direct Product Master adapter, two mutable-current Google Sheets tabs/repository with complete Order-ID snapshot replacement and idempotent NEW/UPDATED/UNCHANGED classification, a validate-before-write Streamlit route, and exact ITEM/GROUP/ORDER plus Statement-financial-bridge proof before atomically inserting Statement-proved rows into the existing canonical Invoice/Statement batch. Full Order current sync cannot overwrite `Invoice_Orders`/`Invoice_Items`; NORMAL_ORDER PDF is exception-only after proof, while PM, mismatch, return/refund, and cancellation cases fail closed to existing exception evidence. Product Summary, Weekly Billing, price-sensitive `NAV + resolved SKU + historical PM Unit Price` identity, Product Master writes, and PDF promotion allocation were unchanged. Read-only September parity: 2026-09-07..13 = 611 Orders / 902 rows / RM29,407.42 vs RM29,407.42; 2026-09-14..20 = 543 / 733 / RM22,143.44 vs RM22,143.44; 2026-09-23..30 = 542 / 721 / RM20,125.99 vs RM20,125.99; all three differences RM0.00, total 1,696 Orders / 2,356 rows. Current live MY Product Master still leaves 83 source rows unresolved (80 legacy `9555208103523`, 3 `9555208101499`), so the real file correctly remains zero-write blocked; SG parser handled 3,336 rows / 2,543 Orders but SG runtime remains NOT PROVEN because the configured Product Master rejects one invalid/missing Price SGD row and SG Statement/persistence evidence is unavailable. Verification: 42 new/atomic focused tests passed; 344 persistence/Statement/Weekly/Product Summary/SG/Live Analysis regressions passed; 89 Statement/UI focused tests passed with one pre-existing invalid source-scan assertion deselected. Full suite observed 1,164 passed / 26 failed / 4 errors; representative failures reproduce at starting HEAD or depend on changed live PM, and four errors are missing archived PDF fixtures. UAT2/Product Master/other business write delta: 0; schema migration was planned but not executed; Git checkpoint: none; Deployment: **NOT VERIFIED**. Product Owner decisions recorded: Full Order Product Subtotal is the NORMAL_ORDER actual amount only under exact proof, PDF is exception-only, mutable current plus Statement-proved immutable history, shared MY/SG concrete pipeline, and minimum abstraction. Next: correct/alias the 83 live PM source identities and administratively apply/verify the two-tab schema before a UAT2 runtime sync; no commit/push before review.
- Date: 2026-10-15; Task: Shopee MY Full Order List minimum-change source audit; State: AUDIT COMPLETE / VERDICT C / ZERO BUSINESS WRITE; Outcome: real September Full Order evidence is structurally strong (3,155 item rows / 2,293 Orders / 2,960 non-cancelled rows / 2 missing Seller SKU), and three fully covered Statement periods prove Full Order Product Subtotal equals Statement Product Price to the cent. However, current Product Master resolution leaves 80 source rows unresolved (78 historical Garbanzo SKU `9555208103523`, 2 identity-ambiguous cranberry rows), 117 of 2,356 fully covered Statement SKU rows remain aggregate-valid but physically unallocated across 45 multi-product groups, the current Statement commit gate blocks every Full Order-shaped Order without Invoice `product_price` / `order_income` evidence, and the 2026-08-31 to 2026-09-06 golden source file omits 68 payout-day Orders while the hybrid comparison changes four per-product allocations despite preserving all total controls. Verification: inspected the current PDF-to-canonical-to-UAT2-to-Statement-to-Weekly path; read the real Full Order and Statement XLSX files; read live Product Master and UAT2; recalculated source, parity, join, Statement, and current Weekly Billing controls. Git baseline remains synchronized at `d32fe3e2e557b7822f9ecdd8a26198206b32e5a3`; no application/test file was changed, staged, committed, or pushed. UAT2/Product Master/other business write delta: 0; schema delta: 0; Deployment: **NOT VERIFIED**. Decision required: before replacing normal-order PDFs, approve either PDF exceptions for aggregate-ambiguous groups and source/PM exceptions, or a separate Full Order allocation plus Statement-only reconciliation contract. Next: if approved, implement only a read-only Full Order parser/parity test first; do not alter current Statement or Weekly financial semantics in the audit task.
- Date: 2026-10-15; Task: Product Owner-authorized Garbanzo SKU fingerprint recovery; State: LIVE DATABASE CORRECTION / FULL READBACK VERIFIED; Outcome: updated only `Invoice_Orders.source_fingerprint` for the 86 remaining legacy mismatch Orders whose current Item evidence includes Product Owner-corrected Garbanzo SKU `9555208014881`. One controlled Google Sheets values batch targeted exactly 86 `AQ` cells; no Item, Product Master, Statement, source, schema, or application-code field was written. Verification: fresh preflight confirmed all 86 expected old digests, exact target identities, legacy contract recomputation, and Garbanzo membership; a rollback snapshot records each cell, Order ID, old digest, new digest, and affected Item rows. Fresh full scan reports 0 remaining fingerprint mismatches across 5,189 Orders / 7,344 Items; the normal repository reads 5,189 Shopee Orders; and actual Shopee MY Live Analysis snapshot/build succeeds for 5,189 Orders / 7,344 Items. UAT2 write delta: 86 cells / 86 Orders; schema/code/Product Master/Git/deployment delta: 0. Decision required: none. Next: normal Live Analysis use may resume.
- Date: 2026-10-15; Task: Product Owner-authorized UAT2 source-fingerprint repair for `260729UTCVYJBY`; State: PARTIAL REPAIR / LIVE ANALYSIS STILL BLOCKED; Outcome: after a fresh exact-row preflight, changed only `Invoice_Orders!AQ165` from `2f7018509f32f1b114649089ba40f264e6e9544a083e35fdea3018794b3a6346` to the Product Owner-approved current legacy digest `fd7c0e88d0de12739c64c4acb064fdff4bac0a68c72a690675bc36c65885f62a`. Readback confirmed that exact new cell value and that all other fields of the target Order row remained unchanged; a rollback snapshot was retained. Verification: the target defect is repaired, but a fresh complete repository read next fails at distinct legacy mismatch `2607314HCAF9B4`. A full source-fingerprint scan found 86 remaining mismatched Shopee legacy orders, so Shopee MY Live Analysis remains unavailable until separately audited and approved. UAT2 write delta: 1 cell / 1 Order; schema/code/Product Master/Git/deployment delta: 0. Decision required: approve a bounded audit/repair plan for the remaining 86 records; no further records were modified. Next: do not perform further fingerprint writes without that approval.
- Date: 2026-10-15; Task: Shopee MY Live Analysis source-fingerprint repair audit for `260729UTCVYJBY`; State: BLOCKED / ZERO WRITE; Outcome: live UAT2 contains exactly one `Invoice_Orders` row (165) and one `Invoice_Items` row (1437, item index 0) for `(Shopee, 260729UTCVYJBY)`. The stored legacy fingerprint `2f7018509f32f1b114649089ba40f264e6e9544a083e35fdea3018794b3a6346` differs from the current repository recomputation `fd7c0e88d0de12739c64c4acb064fdff4bac0a68c72a690675bc36c65885f62a`. The pre-2026-09-11 legacy algorithm also does not reproduce the stored digest, so an algorithm-only explanation is ruled out. Verification: exact headers, repository deserializers, all current material Order/Item facts, legacy calculation, and Git contract history were checked; source PDF/archive payload was unavailable locally, so it cannot be proven whether material source facts changed after import or the original write was incorrect. UAT2 write delta: 0; schema/code/Product Master/Git/deployment delta: 0. Decision required: Product Owner must explicitly approve the current material source facts and the one-cell replacement with the recomputed legacy digest before any UAT2 write. Next: perform that guarded write and deterministic repository/Live Analysis readback only after approval.
- Date: 2026-10-15; Task: Product Owner-approved UAT2 `Invoice_Items` NAV correction; State: LIVE DATABASE CORRECTION / READBACK VERIFIED; Outcome: updated only 18 approved Shopee `nav` cells in the live UAT2 `Invoice_Items` sheet: fourteen `9555208014393-1` records from `5000407-2` to `5000407`, one `9555208010517-3` record from `5000000` to `5000308`, and three `btl 250g-9555208108993` records from `5000000` to `5000349`. Updated only the approved Muesli `unit_price` from RM57.99 to RM58.50. Verification: exact live-Sheet SKU searches returned 14 / 1 / 3 rows; each pre-write identity and old value matched, Product Master SKU/name/variation/NAV/Price records matched, one 19-request cell-only Sheets batch completed, all 18 NAVs and the one ERP price read back correctly, all other target-row fields were unchanged, no old target NAV remained, and Item row count stayed 7,344. A local rollback snapshot contains order ID, item index, SKU, old/new NAV, and old/new price. No Product Master, Invoice_Orders, Statement data, source code, schema, Product Summary, Staging Data, Git, or deployment change. Idempotency dry run: 0 additional changes required. Decision required: none.
- Date: 2026-10-14; Task: Shopee MY Live Analysis pre-shared-engine checkpoint; State: COMMITTED / PUSHED; Outcome: Product Owner accepted the current price-sensitive canonical product identity for this checkpoint, retaining separate RM39.90 and RM36.00 rows for `9555208106944-6` / `3000573`; no display merge or shared-engine migration was added. Checkpoint includes the committed-data Shopee MY dashboard, evidence-constrained date controls, KPI/chart/ranking presentation, sidebar-spacing isolation fix, service/UI/navigation regressions, and approved scope/status documentation. Verification: 109 focused Live Analysis service/UI/navigation/current-batch/Weekly identity tests passed; changed Python modules compiled; working/staged diff checks passed; live UAT2 KPI controls remained RM59,552.65 / 2,039 / 4,215 / RM29.21 / RM320.31. Git: `69d320cf66585929525b71fe38c0900290546fcb` (`feat: checkpoint shopee my live analysis`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2/Product Master/other business writes 0; schema delta 0; Deployment: **NOT VERIFIED**. Decision required: none for this checkpoint; future shared-engine work must not assume a price-history merge. Next: begin only separately approved shared-engine work.
- Date: 2026-10-14; Task: Shopee MY Live Analysis pre-checkpoint product-ranking regression diagnosis; State: BLOCKED / NO CODE CHANGE; Outcome: traced the September 2026 Payout Date / Released / NORMAL_ORDER rows for `9555208106944-6` / NAV `3000573`. The displayed RM1,037.40 is the complete 22-row `Bundle Set` group at persisted historical Unit Price RM39.90. The RM576.00 counterpart is the complete 13-row `Buy 5 free 1` group at persisted historical Unit Price RM36.00; together they equal RM1,613.40 with no unexplained remainder. Existing canonical grouping is `(NAV, resolved Seller SKU, persisted historical Product Master Unit Price)` and existing Weekly regression explicitly preserves different historical prices as separate rows. Merging those two price identities to force RM1,613.40 would contradict the approved identity contract; display Product Name/Variation is not the cause. Verification: live UAT2 read-only row trace and existing service pipeline inspection; UAT2/Product Master/other business writes 0; schema delta 0; Git stage/commit/push 0; Deployment: **NOT VERIFIED**. Decision required: confirm whether Live Analysis ranking must intentionally diverge from the approved price-sensitive identity and merge price histories for the same NAV/SKU, or correct the RM1,613.40 checkpoint anchor. Next: do not checkpoint until that Product Owner decision is made.
- Date: 2026-10-14; Task: Shopee MY Live Analysis sidebar spacing regression; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: removed the page-local global button CSS that added a 1.5rem top margin to every Streamlit button, including the shared sidebar navigation after entering Shopee MY. Added a UI regression asserting the page styles do not target shared button elements. No navigation structure, analytics logic, data source, UAT2, Product Master, or schema behavior changed. Verification: 47 Shopee MY UI/navigation/current-batch tests passed; changed module compiled and `git diff --check` passed. Git stage/commit/push 0; UAT2 write delta: 0; Product Master write delta: 0; other business writes: 0; schema delta: 0; Deployment: **NOT VERIFIED**. Decision required: none; Next: refresh the running Streamlit page.
- Date: 2026-10-14; Task: Shopee MY Live Analysis visual refresh (UI only); State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: refreshed only the Shopee MY Live Analysis presentation with a compact control/context hierarchy, calmer KPI/chart card styling, and equal-width Best Selling Products / Most Sold Products visual ranking cards. Rankings preserve the existing fields and business values, use a text-free normalized bar for visual comparison, and expose no DataFrame or normalized ratio/percentage. Service logic, filters, KPI/product aggregation formulas, canonical product identity, repository access, UAT2, and schema behavior were not changed. Verification: 38 focused Live Analysis service/UI tests and 78 related analytics/UI/navigation/current-batch/Cross-calendar tests passed; changed Python modules compiled and `git diff --check` passed. Git stage/commit/push 0; UAT2 write delta: 0; Product Master write delta: 0; other business writes: 0; schema delta: 0; Deployment: **NOT VERIFIED**. Decision required: Product Owner visual review of the refreshed page; Next: no further scope expansion without approval.
- Date: 2026-10-14; Task: Shopee MY Live Analysis product-table order, evidence-constrained date calendars, and ranking-bar correction; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: moved Most Sold Products below Best Selling Products, reused the Cross Platform Calendar component to disable unavailable basis/status dates, and normalized ranking bars internally to 0..1 while rendering them as text-free bar charts beside actual Sales (RM)/Quantity; no ratio or percentage is user-visible and all aggregation formulas remain unchanged; Verification: 133 affected tests passed before this presentational follow-up, the ranking UI/service tests pass with the new bar-chart assertion, changed Python modules compiled, and `git diff --check` passed; Git: stage/commit/push 0; UAT2 write delta: 0; Product Master write delta: 0; other business writes: 0; Schema delta: 0; Decision required: Product Owner visual acceptance; Next: refresh the running Streamlit page and review the revised layout.
- Date: 2026-10-14; Task: Shopee MY Live Analysis Product Owner dashboard-template refactor; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: preserved all committed-data analytics formulas, filters, canonical product identity, repository reads, and export behavior while simplifying the page to the approved filter/context/KPI/insight/two-chart/two-product-table structure; removed Sales Concentration/donut, Product Movers, and standalone Payout Coverage from reachable UI; Verification: 116 affected tests passed, changed Python modules compiled, `git diff --check` passed, and the live September Released/Week Top 10 plus Top 20 read-only smokes passed with unchanged KPI values and deltas; Git: stage/commit/push 0; UAT2 write delta: 0; Product Master write delta: 0; other business writes: 0; Schema delta: 0; Decision required: Product Owner visual acceptance; Next: manually review the real Streamlit dashboard before any selective checkpoint.
- Date: 2026-10-13; Task: Shopee MY Live Analysis V1 visualization refinement; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: preserved all approved KPI/product formulas and filter architecture while adding one analysis-context line, grouped KPI comparisons, real current-period date axes with true prior-range tooltips, readable/value-labelled dynamic product rankings, a Top 5/Others Item Sales concentration bar/detail list, and conditional NORMAL_ORDER payout coverage; Verification: 107 affected tests passed, changed Python modules compiled, `git diff --check` passed, and the requested live September Released/Week/Top 20 plus All-status UAT2 smokes passed; Git: baseline `78c32dc8f4527d0cf7d7a9833850107bae529f18`, stage/commit/push 0; UAT2 write delta: 0; Product Master write delta: 0; other business writes: 0; Schema delta: 0; Decision required: Product Owner visual acceptance; Next: review revised UI before any selective checkpoint.
- Date: 2026-10-13; Task: Shopee MY Live Analysis V1; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: replaced the Shopee MY session-batch analysis route with the approved read-only committed-data dashboard, including shared filters, five KPIs with same-day-count previous-period comparisons, fail-closed financial layouts, canonical product identity, and five requested charts; Verification: 103 affected tests passed under the app `.venv`, changed Python modules compiled, `git diff --check` passed, and seven live read-only UAT2 filter smokes plus independent full-range KPI recomputation passed; Git: baseline `78c32dc8f4527d0cf7d7a9833850107bae529f18`, stage/commit/push 0; UAT2 write delta: 0; Product Master write delta: 0; other business writes: 0; Schema delta: 0; Decision required: Product Owner visual acceptance; Next: visually review before any selective checkpoint.
- Date: 2026-10-13; Task: post-Shopee-SG-V1 working-tree cleanup; State: DOCUMENTATION SYNCHRONIZED; Outcome: removed only two commented-import lines and one blank-line local noise change, then synchronized the canonical Shopee SG V1 checkpoint/persistence boundary. Production code matches `71cf1949da11005c5a3113a8c86106373be3bed0`; no application test rerun was requested or performed. Documentation-only checkpoint: this commit. Deployment: **NOT VERIFIED**.
- Date: 2026-10-12; Task: Shopee SG Data Import V1 selective checkpoint; State: COMMITTED / PUSHED; Outcome: Product Owner-accepted SG PDF/ZIP test flow, source/market isolation, SGD presentation, live `shopee sg` Product Listing reconciliation, Manual Review/Accepted Candidate boundary, and required market-state fix were checkpointed as `71cf1949da11005c5a3113a8c86106373be3bed0` (`feat: complete shopee sg data import v1`) and pushed to `origin/feature/uat2-invoice-persistence-integration`. Verification: 153 focused SG/market-state/MY regressions passed; changed modules compiled; working and staged diff checks passed. UAT2/SG/Product Master writes 0; schema delta 0; remote HEAD verified equal; Deployment: **NOT VERIFIED**. Decision required: none for V1; SG persistence, Statement, and exception layouts remain separate work. Next: retain unrelated working-tree changes for their own scope.
- Date: 2026-10-12; Task: Shopee SG Data Import V1 complete Reconcile entry; State: LOCALLY COMPLETE / PRODUCT OWNER ACCEPTED / CHECKPOINT PENDING; Outcome: corrected Current Batch and Manual Review SGD presentation through MarketContext, configured the approved live Google spreadsheet/exact `shopee sg` tab with `Price SGD`, preserved fail-closed/no-MY-fallback behavior, and made the pre-reconcile Manual Review KPI explicitly Pending. Fresh Streamlit corpus run processed 157 PDFs / 157 accepted pre-listing orders / 199 products and live resolution yielded 69 exact SKU/Parent, 65 exact Name/Variation, 65 unresolved, 0 ambiguous, 10 incomplete NAV, 17 price-reference mismatches, 63 Manual Review orders, and 94 Accepted Candidates. Ordinary `26090462PGA4FE.pdf` reached test-only Review with 1 order / 1 product / 0 reviews and no commit action. Verification: 8 focused tests passed before the Product Owner requested no further reruns; the broader suite was interrupted and has no final result. Product Owner manual UI testing accepted the milestone. UAT2/SG/Product Master writes 0; schema delta 0; Git stage/commit/push 0; Deployment: **NOT VERIFIED**. Decision required: none for V1; permanent SG persistence remains separately unapproved. Next: selective milestone checkpoint and normal push.
- Date: 2026-10-12; Task: Data Import market switching fix and Shopee SG upload entry verification; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED; Outcome: made nonblank `batch_id` the shared active-batch fact for the sidebar and market guard, so stale `data_import.active_market`/platform selection alone no longer blocks MY/SG switching. Real cross-market batches remain protected by the controlled Continue/Discard lifecycle; the known market-isolation domain error is translated at the chooser boundary and no longer exposes a normal-user traceback. Real local SG UI smoke processed `26090462PGA4FE.pdf` from the supplied archive into 1 order / 1 product with 0 processing errors; no reconcile/commit path was entered. Verification: 145 focused tests passed, changed modules compiled, and `git diff --check` passed. UAT2/SG/Product Master writes 0; schema delta 0; Git stage/commit/push 0; Deployment: **NOT VERIFIED**. Decision required: none; Next: configure the approved SG Product Listing source separately before any live SG reconciliation; SG persistence remains unapproved.
- Date: 2026-10-12; Task: Shopee SG PDF Upload Test Entry V1; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED; Outcome: enabled SG PDF/ZIP intake with explicit SG host/currency isolation, deterministic SG-only Product Listing resolution, explainable identity/listing/price Manual Review, and a visible no-persistence Review boundary. Real corpus: 157/157 structural and financial passes, 199 product rows. A separate local-export listing diagnostic found 69 exact key matches, 39 name/variation matches, 91 unresolved identities, 0 ambiguity/conflict, 10 incomplete NAV rows, 98 fully resolved rows, 17 price-reference mismatches, 88 Manual Review orders, and 69 Accepted Candidate orders; live SG Google configuration remains unavailable and fail-closed. Verification: 141 focused tests passed, changed modules compiled, and `git diff --check` passed; UAT2/SG/Product Master writes 0; schema delta 0; Git stage/commit/push 0; Deployment: **NOT VERIFIED**. Decision required: configure the approved SG Google source for live UI reconciliation; permanent SG destination/persistence remains separately unapproved.
- Date: 2026-10-12; Task: Cross Platform Payout Calendar V2 remote integration and push; State: MERGED / PUSHED; Outcome: Product Owner-approved remote README cleanup `14c7ddf6caae6e58013246bbf92a2c6ab39758a0` was merged with preserved Calendar checkpoint `140ec85c02e0cde79eb71cd2c4cf5c3633f9a031` in merge commit `02cfef82a49870a14cdec9aa1c9a7e6a06bee363`, then pushed to `origin/feature/uat2-invoice-persistence-integration`. The approved remote README state was retained; existing local non-README dirty work was restored unstaged. Verification: 22 focused calendar/Cross service/UI tests passed, changed modules compiled, working-tree and commit diff checks passed, and both parent commits are reachable from the remote HEAD. UAT2 write delta: 0; schema delta: 0; Deployment: **NOT VERIFIED**. Decision required: none; Next: retain unrelated local dirty work for its separate scope.
- Date: 2026-10-12; Task: Cross Platform Payout Calendar V2 checkpoint; State: IMPLEMENTED / TESTED / COMMITTED / UNPUSHED; Outcome: includes the explicit date-selection message `No payout dates are available for the selected platform.` only when the selected scope has an empty valid payout-date domain. Both calendar components stay disabled with no allowed or selected date; filtering, platform behavior, layout, backend guards, and export semantics are unchanged. Verification: 22 focused calendar/Cross service/UI tests passed, changed modules compiled, and `git diff --check` plus `git diff --cached --check` passed. UAT2 write delta: 0; schema delta: 0; dependency delta: 0; Git: `140ec85c02e0cde79eb71cd2c4cf5c3633f9a031` (`feat: constrain cross-platform payout dates`), local only; Deployment: **NOT VERIFIED**. Decision required: none; Next: separate push review.
- Date: 2026-10-12; Task: Cross Platform payout-calendar final review; State: REVIEWED / FIXES REQUIRED BEFORE CHECKPOINT; Outcome: confirmed the three-column popup-calendar contract, real disabled-day buttons, committed payout-date source, backend endpoint guard, inclusive range behavior, state clearing, unsupported-platform fail-closed behavior, and summary/export shared rowset. The no-payout-date state is incomplete: it has disabled fields but no explicit `No payout dates are available` message. Verification: 21 focused calendar/Cross service/UI tests passed; a read-only empty-snapshot AppTest had no exception but only rendered the generic no-product message. UAT2 write delta: 0; schema delta: 0; dependency delta: 0; Git: no stage/commit/push; Deployment: **NOT VERIFIED** (local tests/process evidence only). Decision required: none; Next: add the approved explicit empty-state message and regression before checkpoint.
- Date: 2026-10-09; Task: Cross Platform payout-calendar display refinement; State: IMPLEMENTED / TESTED / UNCOMMITTED; Outcome: restored the native-style three-column `Platform` / `From Date` / `To Date` display. Each date field opens its own compact calendar, while preserving Product Owner-approved payout-evidence-only enabled dates and inclusive interval semantics. Verification: 21 focused calendar/Cross service/UI tests passed, changed modules compiled, and `git diff --check` passed. UAT2 write delta: 0; schema delta: 0; dependency delta: 0; Scope: updated with the durable calendar-domain rule; Git: no checkpoint; Deployment: **NOT VERIFIED** (local Streamlit process detected on port 8501; browser interaction was not independently captured). Decision required: none; Next: Product Owner visual review after refresh.
- Date: 2026-10-03; Task: Shopee SG End-to-End Parser Dry Run V1; State: READ-ONLY AUDIT / NO IMPLEMENTATION; Outcome: audited 157 supplied SG Order Detail PDFs and only the supplied `shopee sg` Listing tab (684 valid rows); no MY listing read or fallback. Original MY parser probes detect Shopee but fail `$` financial extraction or source-blank SKU review; a temporary in-memory SG currency-token adapter yielded 157/157 structural and financial passes. The Product Owner-specified deterministic hierarchy resolved 168/199 item rows; 31 need SG Product Master review and 15 resolved rows need price review. Return/refund/cancelled SG layouts remain not verified. Verification: all four per-order financial invariants passed in 157/157; representative current-MY parser probes and current SG Product Master configuration were read-only checked. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema delta: 0; production-code delta: 0; Git delta: 0; Deployment: NOT VERIFIED. Decision required: approve a separate SG-only configuration/adapter scope after Statement and exception-layout evidence is supplied; Next: do not enable SG or alter MY behavior from this audit alone.
- Date: 2026-10-02; Task: Platform Selector Refinement V1 final-blocker correction; State: IMPLEMENTED / TESTED / REVIEWED / COMMITTED / PUSHED; Outcome: moved Cross platform capability rejection before the MY snapshot read, hardened direct UAT2 repository creation against unsupported/default-cross-market destinations, and applied Product Owner-approved ordinary inclusive date-range Model B; Verification: 38 Cross/UAT2/PDF, 9 selector, 54 routing/isolation, and 114 Billing/reporting service tests passed; changed Python syntax and `git diff --check` passed; one fixed 20-second Weekly full-app AppTest timed out; the separate Data Import state suite had 45 passed and 9 observed failures without independently proven historical baseline; Git: `996817ec30de5895c093fdaa3c91fc08f74bf695` (`feat: refine platform selection and routing`) and `b64f7a3a23e1ecd266a3dd8600a3f3e4919494a6` (`docs: record platform selector checkpoint`) pushed normally; local, upstream, and remote were verified equal after push; UAT2 write delta: 0; Schema delta: 0; Decision required: none; Next: deployment verification is separate and remains unverified.
- Date: 2026-10-01; Task: Platform Selector Refinement V1; State: IMPLEMENTED / TESTED / UNSTAGED / UNCOMMITTED / UNPUSHED; Outcome: added exact Weekly/Monthly/Cross selector contracts, same-row platform/date controls, calendar-based Cross filters with `All dates` and ordinary inclusive date ranges, the ordered `LIVE ANALYSIS` navigation without Dashboard/ambiguous Shopee/ZENXIN, a preserved Shopee MY route, fail-closed SG/TikTok pages, and a fail-closed TikTok Data Import entry with no ZENXIN routing; mapped stored `Shopee` to visible `Shopee MY` without migration; Verification: 171 original affected tests passed plus one grouped 20-second AppTest timeout, 35 selector/market/UI/service tests passed, and an expanded run had 137 passed with 9 observed direct-session-state Data Import assertions failing; historical baseline for those 9 was not independently verified; UAT2, Product Master, schema, and business-data write deltas: 0; Deployment: NOT VERIFIED; Decision required: none; Next: Product Owner review before any selective checkpoint.
- Date: 2026-10-01; Task: Monetary Formatting V1; State: IMPLEMENTED / TESTED / REVIEWED / COMMITTED / PUSHED; Outcome: centralized MYR/SGD-capable presentation formatting and applied MYR grouping/two-decimal display to UI, numeric Excel cells, and monetary PDF/barcode output while preserving missing-value and machine-interchange semantics; Verification: 258 focused formatter/presentation/export regressions passed plus the one transient Weekly Billing sidebar AppTest timeout passed on isolated retry; all changed Python files passed syntax validation and `git diff --check` passed. The broader affected run had 408 passed and 11 failures, after which the one new Product Summary assertion was corrected and reverified. The 10 remaining failures are nine Data Import direct-session-state/platform-navigation assertions and one legacy Settlement Test Lab navigation/title assertion, all outside the monetary rendering path; clean-baseline reproduction was not independently verified, so they are recorded by failure location rather than as confirmed baseline failures. Git: `11bdf155b4adde530bb300c1c8e7892a4b72e580` (`feat: standardize monetary formatting`) and `40f0f30620110c04c81d899a76de5b1f75b7ece2` (`docs: record monetary formatting checkpoint`) pushed normally; local/upstream/remote matched. UAT2, SG, Product Master, and schema write deltas: 0; Deployment: NOT VERIFIED; Decision required: none.
- Date: 2026-10-01; Task: Shopee MY Cancelled Order V1; State: IMPLEMENTED / TESTED / MANUALLY VERIFIED / PERSISTENCE VERIFIED / COMMITTED / PUSHED; Outcome: `Cancelled`/`Canceled` normalize to `CANCELLED_ORDER`, valid source `260908G952UC0W` is accepted as 1 Order / 3 Items / 0 reviews, and cancelled parent items are excluded from Weekly/Monthly/Cross Product Summary aggregation; Verification: 118 focused tests, compile, and diff checks passed; Git: `39ce8e6` feature checkpoint and `510f986` status checkpoint pushed; UAT2, SG, Product Master, and schema write deltas: 0; Deployment: NOT VERIFIED.
- Date: 2026-09-30; Task: Product Owner-approved final Git checkpointing; State: COMMITTED / PUSHED; Outcome: checkpoint 1 `3fe38c2fa774ed6d95c5dbe94996e2e4520ace2e` (`fix: handle active data import platform switching`) and checkpoint 2 `2da7181566e3c5f5accf1ce10624dbcb48aea2b1` (`fix: reconcile cross-period order adjustments`) were selectively committed in that order and pushed to `origin/feature/uat2-invoice-persistence-integration`; Verification: 48 platform-entry/active-batch tests passed, 138 cross-period reconciliation/import/writer/adjustment tests passed (one unrelated dirty-UI source-scan assertion deselected), 65 Weekly service/persistence/item-matching regressions passed, changed production modules compiled, and staged diff checks passed; real Shopee MY 2026-08-24 to 2026-08-30 Statement UAT2 verification passed (batch `20260930020758-b7a5eb29`, 660 ORDER, 947 SKU, 1 ADJUSTMENT, RM18,600.88 released, RM-12.85 Adjustment, 34,816 committed financial-component rows); the `26082480BKAV7A` +137.24 Adjustment remains owned only by the later Statement batch; UAT2 writes during checkpointing: 0; schema delta: NONE; deployment: NOT VERIFIED; Decision required: none.
- Date: 2026-09-29; Task: Product Owner-approved Monthly Billing checkpoint; State: COMMITTED / PUSHED; Outcome: committed the exact nine-path Conservative Monthly Adapter candidate and pushed it to `origin/feature/uat2-invoice-persistence-integration`; Verification: final index/path check, clean-checkout smoke from `c980103` with 7 focused tests passed, changed-file `py_compile`, and `git diff --check`; Git: `ac33d6385f2a7ad20e4835c3e87826613d48b872` (`feat: add monthly billing adapter`) with local and remote HEAD matching; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Deployment: NOT VERIFIED; Decision required: Monthly Statement referential-integrity commit gate audit, Monthly revision/superseding policy, and SG Monthly architecture remain follow-ups; Next: Product Owner/deployment process.
- Date: 2026-09-29; Task: Monthly Billing live UAT2 repair verification; State: READ-ONLY VERIFIED / IMPLEMENTATION COMPLETE; Outcome: Product Owner's external repair makes `260823596770U3` resolve as exactly one Shopee Invoice Order and one canonical Invoice Item (source SKU absent, resolved SKU/NAV present), while committed August Monthly Statement retains ORDER and SKU evidence; the conservative adapter builds August successfully without weakening missing-evidence protection; Verification: one five-tab live snapshot built 2,775 Orders / 4,010 Items / 320 product rows and 4/4 financial controls, 8 focused Monthly/UI/Excel/Barcode/SG tests passed, changed-module `py_compile`, and `git diff --check`; Git: no stage/commit/push; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: Monthly Statement referential-integrity commit gate follow-up audit; Next: Product Owner review before any selective checkpoint.
- Date: 2026-09-29; Task: source-driven Shopee Statement Adjustment canonical typing; State: IMPLEMENTED / COMMITTED / PUSHED; Outcome: added immutable historical mapping plus deterministic ASCII canonicalization for any other usable nonblank Description, preserving raw Statement rows, fingerprint structure, 21-column Order_Adjustments serialization, and structural fail-closed checks; Verification: RED 16 expected failures, then 16 new tests and 75 Adjustment/persistence/writer tests passed; real `26082480BKAV7A` PDF regression 2 passed; parser/reconciliation adjustment subset 5 passed (one existing openpyxl style warning); SG isolation 9 passed; Product Summary/Cross non-impact group had 30 passed and 2 known stale Cross UI assertion failures; full pytest: 1015 passed, 5 known unrelated assertion failures, 4 missing historical-PDF fixture errors; Git: `c980103` (`feat: support source-driven order adjustment types`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Deployment: NOT VERIFIED; Decision required: none; Next: no automatic status-only checkpoint.
- Date: 2026-09-29; Task: Manual Review simultaneous count-visibility adjustment; State: IMPLEMENTED / COMMITTED / PUSHED; Outcome: retained stable tab labels and added one above-tab caption showing current Requires Re-upload and Online Resolution counts from the authoritative review partition; Verification: 122 focused Manual Review/ownership/duplicate/refund/SG tests passed, `py_compile` and cached/working `git diff --check` passed; Git: `e1e3823` (`fix: restore manual review category counts`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: none; Next: Ghost UI remains paused; no deployment verification recorded.
- Date: 2026-09-28; Task: Shopee MY marker-only pending Return/Refund classifier implementation; State: RUNTIME VERIFIED / COMMITTED / PUSHED; Outcome: exact `{return_refund_marker}` selects `NORMAL_ORDER` without rewriting completed-refund or Adjustment semantics; real `260910M4M573RT.pdf` has no layout review and passes local full revalidation; Verification: Product Owner runtime PASS, 66 focused parser/layout/Adjustment/Product Master revalidation tests passed (5 missing historical-PDF tests are a known infrastructure gap) plus 21 focused Validate ownership/presentation and SG isolation tests passed; Git: `760f35b` (`fix: classify pending return refund invoices correctly`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: none; Next: no automatic status-only checkpoint.
- Date: 2026-09-28; Task: Validate duplicate-presentation fix; State: RUNTIME VERIFIED / COMMITTED / PUSHED; Outcome: primary queue now admits only explicit `blocking=True` issues; duplicate/skipped evidence moves to `Skipped / Non-blocking information` with safe optional recovery retained, while readiness, Manual Review ownership, historical classifications, and SG behavior remain unchanged; Verification: reused 156 focused tests, changed-module `py_compile`, and `git diff --check`; Git: `256570f` (`fix: align invoice validation ownership and issue presentation`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: none; Next: continue separate runtime investigation of the global faded/ghost UI.
- Date: 2026-09-26; Task: Cross/Weekly canonical consolidation; State: COMMITTED / PUSHED; Outcome: shared `NAV + resolved SKU + historical PM Unit Price` grouping; Verification: 82 focused tests plus staged-import/compile/diff checks; Git: `678cd06`; UAT2 write delta: 0; Schema delta: 0.
- Date: 2026-09-26; Task: Weekly legacy barcode control; State: COMMITTED / PUSHED; Outcome: old `Export Barcode PDF` hidden while Product Summary Barcode PDF remains; Git: `8d79888`; UAT2 write delta: 0; Schema delta: 0.
- Date: 2026-09-28; Task: documentation reality reconstruction; State: COMMITTED / PUSHED; Outcome: rebuilt this handoff from reachable code, focused tests, Git, and approved scope; Verification: 15 focused regression tests, staged-diff check, and push confirmation; Git: `69b2302` (`docs: sync project status and memory protocol`); UAT2 write delta: 0; Schema delta: 0; Next: no automatic status-only follow-up commit.
- Date: 2026-09-28; Task: Shopee SG Phase 1 market isolation foundation; State: COMMITTED / PUSHED; Outcome: added immutable MY/SG market contexts, fail-closed market-bound UAT2/Product Master factories, market-bound Statement writer keys, scoped-state helpers, explicit Cross MY allowlist, and a Weekly provider seam; Verification: reused 132 focused tests and prior `py_compile`; Git: `9f67f4e` (`feat: add Shopee SG market isolation foundation`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; Schema migration delta: 0; Next: real SG source samples plus application and Product Master configuration before parser/UI/commit enablement.
- Date: 2026-09-28; Task: Platform Invoice Manual Review ownership implementation; State: RUNTIME VERIFIED / COMMITTED / PUSHED; Outcome: Reconcile stops immediately after surfacing a new Product Master identity review, routes to Validate, and focuses the online-resolution queue; ImportResult projects only actual Manual Review records, while latest-upload summary remains non-authoritative; Verification: reused 156 focused tests, changed-module `py_compile`, and `git diff --check`; Git: `256570f` (`fix: align invoice validation ownership and issue presentation`) pushed to `origin/feature/uat2-invoice-persistence-integration`; UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: none; Next: no further ownership change planned.
