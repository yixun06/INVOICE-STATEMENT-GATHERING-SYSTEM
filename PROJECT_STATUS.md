# InvoiceGather Project Status

## Current Baseline

- Date: 2026-09-28
- Branch: `feature/uat2-invoice-persistence-integration`
- HEAD: `69b230299ab79ef39abe6f80fb283ce89a8dc3f6` — `docs: sync project status and memory protocol`
- Remote: `origin/feature/uat2-invoice-persistence-integration` resolves to the same HEAD. **PUSHED**.
- Remote deployment: a prior Weekly UI update was manually verified remotely; later checkpoints, including `678cd06`, are **not recorded as REMOTE DEPLOYMENT VERIFIED**.
- Worktree: unrelated modified tests and untracked audit/asset/output artifacts were present before this documentation audit and remain untouched.

InvoiceGather is a reconciliation-and-evidence system. Committed Invoice-derived data is the operational Zenxin DB; source PDFs/XLSX remain provenance evidence.

## Current System State

### Data Import Platform Entry — IMPLEMENTED / UNCOMMITTED

- `Data Import` now opens a native 2×2 platform chooser for Shopee MY, Shopee SG, Lazada, and Zenxin Website, then renders one shared five-step Data Import shell. Shopee market labels are text-only with no flag icon or Unicode flag. Back is left-aligned above the platform title as secondary navigation.
- All platform contexts expose Invoice Import, Weekly Statement, and Monthly Statement. Shopee MY retains its existing three workflows; Lazada and Zenxin Website admit only Invoice Import; Shopee SG has no enabled source path. Unsupported combinations remain visible and fail closed before Upload.
- Invoice uploads now carry an explicit expected platform while retaining automatic detector validation. A detected mismatch becomes `PLATFORM MISMATCH`, reports selected and detected platforms, and contributes no order/product/review records to the active batch. New mixed-platform batches are not admitted.
- One active batch remains the contract. Back preserves it; choosing another platform presents Continue or the existing confirmed Discard lifecycle. The target context binds only after discard reset, which clears both `data_import.active_platform` and `data_import.active_market`.
- Shopee SG remains UI-visible but cannot reach MY parsing, persistence, Product Master, or Statement workflows. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema delta: 0; deployment: NOT VERIFIED.

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

### Shopee Statement Adjustment Source-Driven Canonical Typing - APPROVED / IMPLEMENTED / UNCOMMITTED

- `Adjustment Type | Description` is now the deterministic source of canonical `adjustment_type`. Published `Return Refund Adjustment After Order Completed` retains the immutable `RETURN_REFUND_AFTER_ORDER_COMPLETED`; every other usable nonblank source description uses mechanical ASCII normalization only, never AI/fuzzy/keyword/sign/reason semantics.
- Raw `adjustment_description` and `adjustment_reason` remain separate source facts. `Statement_Data` retains every raw ADJUSTMENT row, while a structurally valid linked Adjustment also creates a canonical independent `Order_Adjustments` event. Linked order, complete date, amount, and Statement provenance remain fail closed.
- Real `26082480BKAV7A` evidence is `Return Refund Adjustment/Compensation`, `+137.24`, linked order `26082480BKAV7A`; dry-run commit-plan regression creates `RETURN_REFUND_ADJUSTMENT_COMPENSATION` and preserves its raw row. It is **REAL SOURCE / NOT COMMITTED / PRE-COMMIT REGRESSION VERIFIED**; no historical backfill is required.
- `Order_Adjustments` schema: **UNCHANGED / 21 COLUMNS**. Invoice facts, Product Summary, reporting, settlement authority, UAT2, SG, and Product Master are unchanged; write delta is 0 for each. Ghost UI remains **PAUSED / ROOT CAUSE NOT PROVEN**.

### Weekly Billing — COMMITTED

- Committed-data Shopee Statement-period report with Product Summary, Financial Summary, and Excel export.
- It uses the shared final rowset; it does not reparse or write UAT2 data.
- **Visible:** Excel export and `Export Product Summary Barcode PDF`.
- **Hidden:** the old `Export Barcode PDF` button (`8d79888`).
- Barcode output preserves final-row order. Valid EAN-13 renders vector barcode plus digits; an invalid SKU remains verbatim with `Barcode unavailable` and is never guessed or omitted.

### Monthly Billing — APPROVED / IMPLEMENTED / UNCOMMITTED

- Architecture: **CONSERVATIVE MONTHLY ADAPTER**. Weekly Billing remains unchanged as the operational/debug path; Monthly is a distinct committed full-calendar-month projection. Cross Platform remains unchanged and payout-completed-date based.
- Source: reads exactly `Invoice_Orders`, `Invoice_Items`, `Monthly_Statement_Data`, `Monthly_Statement_Financial_Components`, and `Monthly_Statement_Summary`; Monthly schemas remain **34 / 17 / 14 / UNCHANGED**. No Weekly Statement tab is read for Monthly Billing.
- Shared stable business behavior: canonical Product Summary identity, promotion allocation, Shopee MY Staging Data, Financial Summary controls, three-sheet workbook, and final-row barcode rules. Weekly core/service/UI/domain/Staging/export entry points remain unchanged.
- UI: separate `Monthly Billing` navigation, `monthly_billing_*` widget/session namespace, isolated cache keyed by market, spreadsheet identity, and Monthly source contract; the on-page Staging Data section is intentionally hidden while the unchanged Excel workbook retains its Staging Data worksheet; Excel uses `Monthly_Billing_YYYY-MM.xlsx`; Barcode PDF names and source wording are Monthly-specific.
- August Monthly: **BUILD VERIFIED**. After the Product Owner's external UAT2 repair, a read-only 2026-09-29 snapshot finds exactly one Shopee `Invoice_Orders` row and one canonical `Invoice_Items` row for `260823596770U3`; the committed August Statement still has its `ORDER` and `SKU` rows. The adapter resolves the target and builds 2,775 Orders / 4,010 Items / 320 Product Summary rows with all four native financial controls passed. No application rule changed and no incomplete preview or inferred Invoice fact was used. **Monthly Statement referential-integrity commit gate = FOLLOW-UP AUDIT REQUIRED**; it is outside this Billing implementation.
- Monthly revision/superseding policy: **UNRESOLVED / FAIL CLOSED** for distinct committed sources in one month. SG Monthly: **OUT OF SCOPE / FAIL CLOSED**. Deployment: **NOT VERIFIED**.

### Cross Platform Summary — COMMITTED, LIVE, READ-ONLY

- Live report over one snapshot of committed `Invoice_Orders` and `Invoice_Items`; it creates no Cross reporting table and makes no UAT2/schema writes.
- Current adapter scope is committed payout-eligible Shopee rows. Platform and inclusive **Payout Completed Date** From/To filters apply before allocation and aggregation.
- One final rowset drives dashboard, ten-column Product Summary table, and `Cross Platform Barcode Table PDF`.
- Canonical grouping: **NAV + resolved Seller SKU + persisted historical Product Master Unit Price**. Product Name and Variation are display facts only and do not split rows. Same SKU/price with different NAV is separate. Missing NAV blocks the report; never show `N/A`.
- Visible page: filters, dashboard, Product Summary, Cross Platform Barcode Table PDF. Old All Products, All Manual Review, Missing SKU, Excluded, and session-detail panels are not reachable.
- Columns: No., SKU Code, NAV, Description, Qty, UOM, Unit Price, Original Sales, Disc Amt, Amount. UOM is `EA`; no columns are intentionally pinned.
- Dashboard uses that exact final rowset: count, quantity, original sales, signed discount, and amount. Discount is neither absolute-valued nor clamped.

### Shopee SG — Architecture Foundation IMPLEMENTED / READY FOR CHECKPOINT; Source Support APPROVED / PENDING REAL SOURCE + CONFIGURATION

- One InvoiceGather application will use shared workflow/reporting engines plus market-specific configuration, adapters, providers, and data sources. Data Import will gain an internal market/platform selector while retaining the five-step workflow.
- Existing persisted `platform = "Shopee"` remains Shopee MY; no rename migration is approved. New SG persistence uses a distinct `"Shopee SG"` identity.
- **SG database resource: PROVISIONED.** `InvoiceGather SG Data` exists, but the application is not yet connected to it.
- **SG core Phase 1 schema: READY.** `Invoice_Orders` has 44 columns; `Invoice_Items` 23; `Statement_Data` 40; `Statement_Financial_Components` 17; and `Statement_Summary` 14.
- **SG application configuration/wiring: PENDING.** No application path is configured to use the provisioned database.
- **SG Product Master tab/configuration: PENDING.** SG must fail closed and must never fall back to MY pricing/NAV.
- **SG real Invoice/Statement source support: PENDING REAL SAMPLE.** MY parser/Statement engines are only provisionally reusable until real SG samples are reviewed.
- Market context must bind repository/writer destination, commit lock, Statement population, Product Master source, currency, and reporting provider. MY↔SG reads, reconciliation, commits, and fallback are prohibited.
- Product Master infrastructure is shared but tabs/data scopes are separate. SG Unit Price and NAV must come from the same resolved SG Product Master row.
- Currency is MYR for MY and SGD for SG. No FX conversion, cross-currency aggregation, or combined financial totals are approved.
- Shopee SG is permanently excluded from the existing Cross Platform Summary at service/data eligibility, including its filter, population, totals, table, and PDF.
- SG Product Summary reuses `NAV + resolved Seller SKU + persisted historical Product Master Unit Price`; Product Name/Variation remain display-only, missing NAV blocks, and different NAV values remain separate.
- Weekly Billing will evolve to a shared shell with market-specific providers for committed data, Statement population, currency, Product Master, Product Summary, and exports.
- Current code audit found reusable platform-neutral repository/schema and Product Summary primitives, but MY coupling remains in parser output, historical bundle creation, Statement reconciliation/writer keys, Weekly Billing filters/UI currency, one UAT2 settings object, one Product Master source selection, and unscoped Streamlit workflow state.
- **Blockers:** approved real SG Invoice and Statement samples; SG Product Listing; approved SG Product Master tab/configuration; application configuration/wiring.
- **Next action after Product Owner review:** introduce a fail-closed immutable market registry/context and isolation tests before wiring any SG UI or parser path. Preserve MY behavior and data unchanged.
- **Phase 1 foundation implemented (uncommitted):** immutable Shopee MY/SG market contexts provide persisted platform identity, MYR/SGD display metadata, Cross eligibility, state namespace, and capability availability. MY remains `"Shopee"`; SG is `"Shopee SG"` and permanently Cross-ineligible.
- UAT2 and Product Master configuration factories now accept an explicit market context. MY no-argument callers retain the existing configuration path. SG requires an explicit market-specific configuration block and otherwise raises a clear unavailable error; it cannot inherit MY spreadsheet, credentials, Product Master, or cache identity.
- Statement writer now binds spreadsheet and persisted-platform targets together, rejects a plan/adjustment for another platform before I/O, and continues to use `"Shopee"` for MY. SG Statement commit and Weekly Billing remain deliberately unavailable even if a future SG spreadsheet is configured.
- Weekly Billing has a context/provider seam with the current MY path as its default; no SG selector or fake SG dataset exists. Cross source eligibility is now an explicit MY persisted-platform allowlist, not UI-only behavior.
- Verification: 132 focused tests passed across the market foundation, existing UAT2 repository, Product Master, Statement writer, Cross Platform, Weekly Billing service/UI; `py_compile` passed. UAT2 writes 0; schema delta 0.

### Platform Invoice Manual Review Ownership Bug — IMPLEMENTED / UNCOMMITTED / RUNTIME REVIEW INCOMPLETE

- This defect predates Shopee SG Phase 1. The SG market-isolation checkpoint did not change the affected Platform Invoice Validate/Reconcile code paths.
- A successful `Apply & Revalidate` removes the selected record from authoritative `session_state["reviews"]`, reinserts the corrected Accepted order/products through `apply_batch_rules()`, and invalidates the historical staging entries, refresh flag, and signature. Corrected Product Master identity evidence remains resolved in the existing focused service test.
- The ownership defect begins in Reconcile: `_surface_reconciliation_product_identity_reviews()` can discover a previously unchecked Product Master `PRICING_CONFLICT` or `PRICE_CONFIRMED_IDENTITY_AMBIGUOUS`, move the Accepted source into one new `PRODUCT_MASTER_IDENTITY_RESOLUTION_REQUIRED` review, and invalidate historical state. `_render_reconciliation_step()` then continues instead of returning the workflow to Validate.
- Historical staging consumes that newly created review. `build_current_batch_staging()` projects it as `NEEDS_REVIEW` with `Manual Review source remains in the current batch`, and the Reconcile exception queue displays it as a non-NEW blocker. This is a derived symptom, not a separate historical-classification rule.
- The `Requires Re-upload` / `Online Resolution` tab labels are emitted by one Validate renderer from the current authoritative reviews. The observed `6/0` to `6/1` change is consistent with Reconcile adding one new online-resolution review between renders. `upload_result_summary` is a separate stale action-time projection and must not be treated as current Manual Review authority. No second server-side renderer of the same split labels was found; a truly simultaneous duplicate block would still require a captured browser reproduction.
- Implemented invariant: one blocker, one authoritative owner, one visible resolution path. Reconcile now receives the explicit count from `_surface_reconciliation_product_identity_reviews()`. When it creates a Product Master identity review, the existing session-only invalidation is already complete; Reconcile selects the online-resolution presentation context, returns to Validate, and stops before historical staging or exception rendering can run. The next Reconcile pass recomputes from the newly revalidated authoritative state.
- `_current_import_result().source_specific_details["manual_review"]` now contains only actual Manual Review records. `upload_result_summary` remains a latest-upload audit projection only; it is not current readiness or current-review authority.
- Current status: IMPLEMENTED / UNCOMMITTED / RUNTIME REVIEW INCOMPLETE. Duplicate-presentation work did not change Reconcile-to-Validate ownership, historical staging/classification, or the Product Master identity transition. The ownership fix remains uncommitted pending its separate runtime review; its focused Product Master ownership regression passed in this execution.

### Platform Invoice Validate Duplicate Presentation â€” IMPLEMENTED / UNCOMMITTED

- Primary Validate `Needs Attention` now derives only from explicit blocking semantics. A current-batch Duplicate Order remains non-blocking, does not change forward readiness, and appears in `Skipped / Non-blocking information` with its original evidence and optional safe duplicate-removal action.
- The queue uses the existing authoritative duplicate projection and recovery service; it adds no duplicate store, does not create a historical status, and preserves the existing Weekly Statement non-blocking duplicate recovery path in its secondary notes.
- Verification: 156 focused tests passed across Validate recovery/presentation, ImportResult contract, Manual Review/Product Master ownership, workflow controls, and Shopee SG isolation/Statement writer/Cross/Weekly suites. Changed Python modules passed `py_compile`; `git diff --check` passed. UAT2 write delta: 0; SG business-data write delta: 0; Product Master write delta: 0; schema migration delta: 0. No Git operations were performed.

### Global Faded / Ghost Streamlit UI â€” AUDITED / ROOT CAUSE NOT YET PROVEN

- **Manual Review count visibility: RUNTIME/PRESENTATION ADJUSTMENT IMPLEMENTED.** Stable tab labels remain `Requires Re-upload` and `Online Resolution`; one compact caption immediately above the tabs shows both counts from the authoritative current `reviews` partition.
- **Ghost UI: PAUSED / ROOT CAUSE NOT PROVEN.** This count-visibility adjustment does not claim to remediate the ghost; the current root-cause classification remains **INSUFFICIENT EVIDENCE**.

### Shopee MY Pending Return/Refund Financial Layout — IMPLEMENTED / UNCOMMITTED

- Product Owner rule: a product-level `Return/Refund` marker alone is an operational pending-return fact, not completed refund financial evidence. With no explicit non-zero Refund Amount or Reverse Shipping Fee/SST, the original Invoice remains `NORMAL_ORDER` and normal validation must run. A completed post-order Adjustment remains a separately validated event.
- Real source `260910M4M573RT.pdf` reproduces the defect exactly: Delivered; three reliable product markers with return/refund quantities `1`, `1`, and `2`; Merchandise Subtotal/Product Price `55.45`; Estimated Order Income/Final Amount `42.44`; no Refund Amount; no Reverse Shipping Fee/SST; explicit `No adjustment has been made to this order yet`; no parsed Adjustment event. Current signals are exactly `{return_refund_marker}`.
- Root cause: `classify_invoice_financial_layout_from_signals()` treats every non-empty signal set lacking non-zero `refund_amount` plus another signal as `UNKNOWN_OR_MIXED`. Review policy then emits `FINANCIAL_LAYOUT_UNRESOLVED`; `resolution_plan()` has no safe plan for that code, so Validate presents Requires Re-upload.
- Read-only NORMAL_ORDER simulation found no second blocker: product count/structure, promotion evidence, required income fields, product totals, seller financial reconciliation, Final Amount/Order Income, and no-adjustment validation all passed. Read-only live MY Product Master lookup matched all three SKUs with nonblank NAV; complete revalidation returned no error.
- Implemented exactly one classifier exception: `signals == {return_refund_marker}` now returns `NORMAL_ORDER`. Signal extraction remains unchanged, so a non-zero Refund Amount plus marker or Reverse Shipping Fee/SST remains `RETURN_REFUND`; Refund Amount-only and Reverse Shipping-only remain guarded as `UNKNOWN_OR_MIXED`. No fourth layout enum was introduced.
- Fresh real-source verification: `260910M4M573RT.pdf` retains `{return_refund_marker}`, now classifies as `NORMAL_ORDER`, has no financial-layout review, parses three products, has no completed Adjustment event, and completes local full revalidation with no error. It no longer enters Manual Review or Requires Re-upload for financial layout.
- Regression coverage: the full signal matrix and marker-only revalidation are explicit. Completed Adjustment corpus/upload regressions now assert normal original layout while retaining source date/reason/amount/Final Amount checks; adjustment conflicts and missing evidence remain blocked. Focused parser/layout/Adjustment/Product Master revalidation coverage: 66 passed (5 historical-PDF tests deselected because their archived files are unavailable); focused Validate ownership/presentation and SG isolation coverage: 21 passed. UAT2 writes: 0; SG writes: 0; Product Master writes: 0; schema migration delta: 0; Git operations: no stage/commit/push.

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
- `678cd06` — aligned Cross/Weekly canonical consolidation; pushed and current HEAD.
- `69b2302` — synchronized project status and durable memory protocol; committed and pushed.

## Current Priorities

1. Preserve and verify real UAT2 persistence/readback and guarded Statement behavior.
2. Finance-test committed-period Weekly Billing outputs.
3. Inspect real source/data contracts before future platform work; reuse shared Product Summary policy only where evidence supports it. Shopee SG precedes Lazada where the confirmed priority requires it.
4. Keep Billing readiness, broader Analysis, storage optimization, and PostgreSQL separately scoped.

## Approved / Pending

- **APPROVED / PENDING IMPLEMENTATION:** billing readiness gate and later Billing Summary/Analysis layers beyond current committed-data reports.
- **DEFERRED:** Cross adapters beyond committed Shopee scope; Product ID-to-Seller-SKU mapping; bank receipt reconciliation; final underpayment classification; revised Statement replacement/versioning; PostgreSQL/storage redesign.
- **UNRESOLVED:** real-source contracts and Product Owner approval are required before new platform mappings, financial allocation, or schema changes.
- **SHOPEE SG PENDING EVIDENCE/CONFIGURATION:** the database resource is provisioned and the core Phase 1 schema is ready. Real SG Invoice/Statement samples, SG Product Listing, SG Product Master tab/configuration, and application wiring remain pending; the application is not connected to the SG spreadsheet.

## Recent Execution History

- Date: 2026-09-28; Task: Validate duplicate-presentation fix; State: IMPLEMENTED / UNCOMMITTED; Outcome: primary queue now admits only explicit `blocking=True` issues; duplicate/skipped evidence moves to `Skipped / Non-blocking information` with safe optional recovery retained, while readiness, Manual Review ownership, historical classifications, and SG behavior remain unchanged; Verification: 156 focused tests, changed-module `py_compile`, and `git diff --check`; Git: no stage/commit/push; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: none; Next: Product Owner runtime review and a later selective checkpoint if approved.
- Date: 2026-09-28; Task: Shopee MY marker-only pending Return/Refund classifier implementation; State: IMPLEMENTED / UNCOMMITTED; Outcome: exact `{return_refund_marker}` now selects `NORMAL_ORDER`, while completed-refund typed-evidence and Adjustment safeguards remain intact; real `260910M4M573RT.pdf` has no layout review and passes local full revalidation; Verification: 66 focused parser/layout/Adjustment/Product Master revalidation tests passed (5 missing historical-PDF tests intentionally deselected) plus 21 focused Validate ownership/presentation and SG isolation tests passed; Git: checkpoint remains `256570f`, no stage/commit/push; UAT2 write delta: 0; SG write delta: 0; Product Master write delta: 0; Schema delta: 0; Decision required: none; Next: Product Owner runtime review.
- Date: 2026-09-26; Task: Cross/Weekly canonical consolidation; State: COMMITTED / PUSHED; Outcome: shared `NAV + resolved SKU + historical PM Unit Price` grouping; Verification: 82 focused tests plus staged-import/compile/diff checks; Git: `678cd06`; UAT2 write delta: 0; Schema delta: 0.
- Date: 2026-09-26; Task: Weekly legacy barcode control; State: COMMITTED / PUSHED; Outcome: old `Export Barcode PDF` hidden while Product Summary Barcode PDF remains; Git: `8d79888`; UAT2 write delta: 0; Schema delta: 0.
- Date: 2026-09-28; Task: documentation reality reconstruction; State: COMMITTED / PUSHED; Outcome: rebuilt this handoff from reachable code, focused tests, Git, and approved scope; Verification: 15 focused regression tests, staged-diff check, and push confirmation; Git: `69b2302` (`docs: sync project status and memory protocol`); UAT2 write delta: 0; Schema delta: 0; Next: no automatic status-only follow-up commit.
- Date: 2026-09-28; Task: Shopee SG architecture/data-contract audit; State: APPROVED / PENDING IMPLEMENTATION; Outcome: recorded one-app market-adapter direction, hard MY/SG persistence/Product Master/currency/session/report isolation, permanent Cross exclusion, and reusable shared boundaries; Verification: targeted current-code inspection and documentation diff checks only; Git operations: 0; application/test/data/schema changes: 0; Blockers: real SG Invoice/Statement/Product Listing, SG Product Master configuration, and application wiring; Next: Product Owner review, then market-context foundation and isolation tests.
- Date: 2026-09-28; Task: Shopee SG Phase 1 market isolation foundation; State: IMPLEMENTED / UNCOMMITTED; Outcome: added immutable MY/SG market contexts, fail-closed market-bound UAT2/Product Master factories, market-bound Statement writer keys, scoped-state helpers, explicit Cross MY allowlist, and a Weekly provider seam; Verification: 132 focused tests and `py_compile` passed; Git: no stage/commit/push; UAT2 write delta: 0; Schema delta: 0; Decision required: none; Next: Product Owner review and real SG source/configuration before parser/UI/commit enablement.
