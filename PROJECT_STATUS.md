# InvoiceGather Project Status

## Current Baseline

- Date: 2026-09-28
- Branch: `feature/uat2-invoice-persistence-integration`
- HEAD: `678cd06452d27b1ddf91af5c42b7cfdbe51379aa` — `fix: align cross platform product consolidation`
- Remote: `origin/feature/uat2-invoice-persistence-integration` resolves to the same HEAD. **PUSHED**.
- Remote deployment: a prior Weekly UI update was manually verified remotely; later checkpoints, including `678cd06`, are **not recorded as REMOTE DEPLOYMENT VERIFIED**.
- Worktree: unrelated modified tests and untracked audit/asset/output artifacts were present before this documentation audit and remain untouched.

InvoiceGather is a reconciliation-and-evidence system. Committed Invoice-derived data is the operational Zenxin DB; source PDFs/XLSX remain provenance evidence.

## Current System State

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
- Current schema: `Invoice_Orders` 44 columns; `Invoice_Items` 22; `Statement_Data` 40; `Statement_Financial_Components` 17.

The only complete end-to-end Statement acceptance corpus remains 2026-08-31 to 2026-09-06: 296 orders / 442 SKU rows, 138 `EXACT`, 158 `EXPLAINED`, 0 `NONE`, RM0.00 unexplained residual. It is not cross-week production proof.

### Weekly Billing — COMMITTED

- Committed-data Shopee Statement-period report with Product Summary, Financial Summary, and Excel export.
- It uses the shared final rowset; it does not reparse or write UAT2 data.
- **Visible:** Excel export and `Export Product Summary Barcode PDF`.
- **Hidden:** the old `Export Barcode PDF` button (`8d79888`).
- Barcode output preserves final-row order. Valid EAN-13 renders vector barcode plus digits; an invalid SKU remains verbatim with `Barcode unavailable` and is never guessed or omitted.

### Cross Platform Summary — COMMITTED, LIVE, READ-ONLY

- Live report over one snapshot of committed `Invoice_Orders` and `Invoice_Items`; it creates no Cross reporting table and makes no UAT2/schema writes.
- Current adapter scope is committed payout-eligible Shopee rows. Platform and inclusive **Payout Completed Date** From/To filters apply before allocation and aggregation.
- One final rowset drives dashboard, ten-column Product Summary table, and `Cross Platform Barcode Table PDF`.
- Canonical grouping: **NAV + resolved Seller SKU + persisted historical Product Master Unit Price**. Product Name and Variation are display facts only and do not split rows. Same SKU/price with different NAV is separate. Missing NAV blocks the report; never show `N/A`.
- Visible page: filters, dashboard, Product Summary, Cross Platform Barcode Table PDF. Old All Products, All Manual Review, Missing SKU, Excluded, and session-detail panels are not reachable.
- Columns: No., SKU Code, NAV, Description, Qty, UOM, Unit Price, Original Sales, Disc Amt, Amount. UOM is `EA`; no columns are intentionally pinned.
- Dashboard uses that exact final rowset: count, quantity, original sales, signed discount, and amount. Discount is neither absolute-valued nor clamped.

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

## Current Priorities

1. Preserve and verify real UAT2 persistence/readback and guarded Statement behavior.
2. Finance-test committed-period Weekly Billing outputs.
3. Inspect real source/data contracts before future platform work; reuse shared Product Summary policy only where evidence supports it. Shopee SG precedes Lazada where the confirmed priority requires it.
4. Keep Billing readiness, broader Analysis, storage optimization, and PostgreSQL separately scoped.

## Approved / Pending

- **APPROVED / PENDING IMPLEMENTATION:** billing readiness gate and later Billing Summary/Analysis layers beyond current committed-data reports.
- **DEFERRED:** Cross adapters beyond committed Shopee scope; Product ID-to-Seller-SKU mapping; bank receipt reconciliation; final underpayment classification; revised Statement replacement/versioning; PostgreSQL/storage redesign.
- **UNRESOLVED:** real-source contracts and Product Owner approval are required before new platform mappings, financial allocation, or schema changes.

## Recent Execution History

- Date: 2026-09-26; Task: Cross/Weekly canonical consolidation; State: COMMITTED / PUSHED; Outcome: shared `NAV + resolved SKU + historical PM Unit Price` grouping; Verification: 82 focused tests plus staged-import/compile/diff checks; Git: `678cd06`; UAT2 write delta: 0; Schema delta: 0.
- Date: 2026-09-26; Task: Weekly legacy barcode control; State: COMMITTED / PUSHED; Outcome: old `Export Barcode PDF` hidden while Product Summary Barcode PDF remains; Git: `8d79888`; UAT2 write delta: 0; Schema delta: 0.
- Date: 2026-09-28; Task: documentation reality reconstruction; State: IMPLEMENTED / UNCOMMITTED; Outcome: rebuilt this handoff from reachable code, focused tests, Git, and approved scope; Verification: documentation-only audit and targeted source/test review; Git: no stage/commit/push; UAT2 write delta: 0; Schema delta: 0; Next: Product Owner review.
