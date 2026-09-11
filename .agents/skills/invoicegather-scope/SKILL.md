---
name: invoicegather-scope
description: Long-term system consensus, business rules, data semantics, architecture guardrails, import/commit workflow, validation/reconciliation boundaries, UI/UX direction, and development constraints for InvoiceGather V2. Use whenever developing, debugging, reviewing requirements, architecture, parsers, validation, reconciliation, Streamlit UI, reporting, persistence, database, settlement, shipment, roles, or Excel export. Preserve stable extraction behavior, separate batch validation from pre-commit database validation, preserve source facts, and ask before locking uncertain business rules.
---

# InvoiceGather Scope & Development Guardrails — V2.4 (UAT2)

## 0. How to use this skill

This file is the **long-term source of truth for InvoiceGather system consensus**.

A task prompt should normally contain only the immediate objective. Do not repeat this entire skill in every task.

Before changing code:

1. Inspect the current implementation and relevant tests.
2. Treat current reachable runtime behavior as the implementation baseline; historical changelogs are not the current specification.
3. Check whether the requested behavior is **Confirmed**, **Deferred / Waiting for Sample**, **TODO**, or **Out of Scope** below.
4. If a required business meaning is not confirmed, **stop and ask the user before implementing**.
5. Do not silently choose a financial interpretation, cross-platform mapping, allocation formula, database relationship, workflow rule, or destructive migration.
6. Prefer incremental changes over rewrites. Stable parser behavior is valuable and should not be reorganized merely for architecture purity.

Use plain-language clarification questions when asking the user.

Core principles:

```text
Accuracy > Feature Count
Deterministic > Guessing
Validation > Silent Data Loss
Auditability > Convenience
Manual/Admin Review > Wrong Financial Data
Stable Existing Behavior > Unnecessary Rewrite
Current Confirmed Requirement > Historical Intermediate Design
```

---

# UAT2 V2.4 — Authoritative Current Delivery Scope

## V2.4 purpose, flow, and precedence — Confirmed

V2.4 supersedes the V2.3 UAT2 delivery scope below wherever the two conflict.
The V2.3 material is retained as historical context only; it is not authority
for the current UAT2 implementation.

InvoiceGather UAT2 now follows this operational flow:

```text
STEP 1 — Daily Invoice Import
STEP 2 — Weekly Statement Import
STEP 3 — Billing / Product Summary
STEP 4 — Database-backed Live Analysis
```

The database is an operational business database, not only a minimal historical
source archive. Invoice data is imported first. A later successfully validated
Weekly Statement may enrich selected Invoice database fields, but original
Invoice source values must not be silently replaced by unrelated data.

Current development remains Phase 3 / STEP 1. Do not implement the future
STEP 1 refinements or any STEP 2–4 work unless a task explicitly requests it.

## STEP 1 — Daily Invoice Import — Confirmed

Normal workflow:

```text
Upload PDF / ZIP
→ Parse
→ Manual Review handling
→ Validate
→ Product Master enrichment
→ Historical Order ID check
→ Review
→ Commit
→ Invoice_Orders + Invoice_Items
```

If parsing, validation, Product Master resolution, or required persistence data
is unreliable, route the source to Manual Review and do not automatically
commit it. Manual Review sources may be removed from the current batch. Future
manual database-entry/correction UI is deferred.

Historical duplicate checking belongs inside the normal Validate workflow.
Do not require a normal workflow button named `Check Historical Status`.

```text
NEW              = Order ID does not exist.
ALREADY_IMPORTED = same Order ID and materially identical Invoice source facts exist.
SOURCE_CONFLICT  = same Order ID exists but material Invoice source facts differ.
NEEDS_REVIEW     = source cannot safely become a persistable Invoice record.
```

Existing safe source-removal recovery may be used for overlap/problem sources.
Immediately before Commit, silently recheck repository/database state for
concurrency and idempotency. Retry / Refresh Historical Status is error
recovery only.

## STEP 1 — Product Master enrichment — Confirmed

The existing Unit Price lookup logic remains authoritative. Do not redesign or
simplify it. Its current lookup may use Seller SKU, Parent SKU, Product Name,
and Variation as already implemented.

Once the existing lookup resolves the Product Master row used for Unit Price:

```text
unit_price = Product Master Unit Price from that matched result
nav        = NAV CODE from the SAME matched Product Master row
```

`NAV CODE` is the confirmed human-facing Product Master header. There must not
be a separate NAV matching algorithm. If Product Master resolution fails, Unit
Price is unresolved/conflicting, or the matched row has missing/blank NAV CODE,
route the source to Manual Review and block persistence.

## STEP 1 — Invoice persistence contract — Confirmed target

`Invoice_Orders` should persist meaningful accepted-Shopee order-level fields:

```text
platform, order_id, order_status, order_created_date, delivered_date,
completed_date, fund_transfer_date, invoice_financial_layout,
merchandise_subtotal, product_price,
shipping_subtotal, shipping_fee_paid_by_buyer,
shipping_fee_charged_by_logistic_provider, shipping_fee_rebate_from_shopee,
seller_paid_shipping_fee_sst, reverse_shipping_fee, reverse_shipping_fee_sst,
vouchers_rebates_total, voucher_type, voucher_code, voucher_funded_by,
voucher_amount,
commission_fee, service_fee, transaction_fee, ams_commission_fee,
ads_escrow_top_up_fee,
fees_charges_total,
order_income, income_type, final_amount, refund_amount,
buyer_merchandise_subtotal, buyer_shipping_fee, shopee_voucher, seller_voucher,
total_buyer_payment,
payment_status, payout_completed_date, difference,
source_pdf, source_hash, source_fingerprint, first_imported_at
```

`fund_transfer_date` is the Invoice/PDF source value. `payout_completed_date`
is blank during Invoice import and may later be populated only after successful
Statement processing. `payment_status` is operational and may later change
after confirmed Statement matching. Missing source values remain `None`/blank;
explicit RM0.00 remains `0.00`. Preserve refund signs, keep `final_amount` and
`order_income` separate, and retain Estimated / Final `income_type` semantics.
Do not persist duplicate helper aliases when a canonical business field exists.

`Invoice_Items` target fields are:

```text
platform, order_id, item_index,
seller_sku, sku_missing_in_source, nav, product_name, variation, quantity,
unit_price, actual_selling_unit_price, line_subtotal,
promotion_group_id, promotion_label, promotion_advertised_amount,
promotion_discount_percent, source_group_total,
statement_product_price, statement_refund_amount, statement_net_selling_amount,
source_pdf, source_hash
```

`unit_price` is the matched Product Master normal/POS Unit Price snapshot;
`nav` is `NAV CODE` from that same matched row. `actual_selling_unit_price` and
`line_subtotal` are Shopee Invoice/PDF values. Statement item amounts are blank
during Invoice import and may only be populated by a future successful
Statement commit. Their future formula is:

```text
statement_net_selling_amount = statement_product_price + signed statement_refund_amount
```

The historical-persistence-only fields `actual_selling_value`, `pricing_status`,
`allocation_method`, and `allocation_evidence` are no longer required canonical
persisted Invoice item fields. Temporary internal reconciliation equivalents may
remain where needed.

## STEP 1 — Source fingerprint — Confirmed

`source_fingerprint` represents material Invoice source facts and must remain
stable when Product Master or Statement enrichment changes. It must not create
a `SOURCE_CONFLICT` merely because Product Master Unit Price/NAV changes, a
Statement is imported, `payout_completed_date` changes, operational
`payment_status` changes, statement item amounts are populated,
`first_imported_at` changes, or source filename/path metadata changes.

Material Invoice facts include real Invoice evidence such as Order ID;
Invoice status/date facts; fund-transfer date; Invoice financial breakdown;
order income, income type, final amount, and signed refund amount; and Seller
SKU, Product Name, Variation, Quantity, actual selling unit price, line
subtotal, and promotion source metadata.

### Invoice financial layout and source completeness — Phase A / Locked

Confirmed Shopee Invoice layouts are `NORMAL_ORDER` and `RETURN_REFUND`.
`UNKNOWN_OR_MIXED` is staging/Manual Review only and cannot become Accepted.
A Return/Refund classification requires at least two independent reliable
source signals among explicit Refund Amount, a product-area Return/Refund
marker, Reverse Shipping Fee, and Reverse Shipping Fee SST. A single signal is
not sufficient.

Required Income fields are layout-specific. Normal orders retain the established
core fields, including Fees & Charges, Commission, Service, and Transaction.
Confirmed Return/Refund orders require Merchandise Subtotal, Product Price,
Refund Amount, the established shipping core, and final Order Income, but may
legitimately omit those four normal fee fields.

Optional never means ignored. A visible supported label must parse and be
preserved; an absent label remains `None`, while explicit zero remains zero.
Invoice AMS Commission Fee, Reverse Shipping Fee, and Reverse Shipping Fee SST
are separate signed source facts. Do not merge reverse shipping into normal
shipping fields.

When Seller SKU is genuinely absent from the source, keep it blank and set
`sku_missing_in_source=True`; exact Product Name + Variation Product Master
matching may still resolve Unit Price and NAV. Never use this flag when a SKU is
visible but extraction failed, and never guess or backfill a Seller SKU.

Persist `promotion_advertised_amount` and `promotion_discount_percent` as
Invoice source evidence. The Phase A Invoice schemas are exactly 44 Order
columns and 22 Item columns. The new material source fields participate in
`source_fingerprint`, except `invoice_financial_layout`, which is deliberately
excluded because it is a derived parser classification.

Every source-fact Manual Review correction must pass one complete revalidation
chain: product count and structure, promotion evidence/allocation, product and
financial totals, layout classification/requirements, Product Master/NAV, and
current-batch rules. Phase B multi-product drafts, promotion-group selection,
and Promotion Subtotal correction remain deferred.

## STEP 2 — Shopee Weekly Statement Locked Scope v1 — Confirmed / Locked

This section records the locked target scope for the future Statement
implementation. It does not authorize implementation in the current
documentation task. Where it conflicts with older Statement reconciliation
wording retained later in this file, this section is authoritative.

The following correction is authoritative over earlier drafts of this locked
scope:

1. Statement commit requires 100% Statement target Order ID coverage in the
   committed Invoice database. Even one `UNMATCHED_ORDER` blocks the whole
   Statement batch commit.
2. Do not omit the amount comparison merely because Invoice `final_amount` is
   missing. Use Invoice `order_income` as the confirmed fallback described
   below and retain the `ESTIMATED_ONLY` outcome.
3. Use the single `Statement_Data` persistence tab defined below instead of
   the older multi-tab Statement persistence direction.
4. Required Statement SKU matching is part of whole-batch readiness. The older
   direction that product-level settlement ambiguity must not block the
   order-level Statement commit is superseded.

### Statement source — Confirmed / Locked

- Current Statement scope is Shopee only.
- Input is the native Shopee XLSX export.
- Users must not be required to repair or Save As the workbook first.
- If worksheet dimension metadata is wrong, the parser must eventually inspect
  actual populated cells using the established deterministic compatibility
  approach.
- The original uploaded Statement file must never be modified.
- A Shopee Statement PDF parser is out of scope.

### Statement import batch and audit boundary — Confirmed / Locked

One uploaded Shopee Weekly Statement XLSX represents one Settlement / Statement
Import Batch. Preserve source and audit metadata equivalent to:

```text
Platform
Statement Period From
Statement Period To
Original Filename
File Hash
Uploaded At
Uploaded By
Statement Order Count
Statement SKU Count
Total Released Amount
Adjustment Total
Validation Status
Commit Status
```

Persist the reconciliation projection in the existing Google Sheets tab named
`Statement_Data`. Keep it at its exact approved 40 columns; do not add
financial-component columns to it.
Each persisted row uses one of these record types:

```text
ORDER
SKU
ADJUSTMENT
```

Persist only Statement data relevant to Invoice reconciliation, Invoice
enrichment, future Billing/Product Summary, and necessary auditability. The
exact approved `Statement_Data` column list is locked below.

Persist the wider settlement-component breakdown in the append-only companion
ledger `Statement_Financial_Components`. It is source evidence for future
Billing and does not redefine Invoice source facts or alter the 40-column
`Statement_Data` projection.

Statement `file_hash` identifies the uploaded Statement file. Invoice
`source_fingerprint` identifies material normalized Invoice source facts. They
are separate concepts and must not be combined.

### Statement_Data v1 schema — Approved / Locked

`Statement_Data` has one exact 40-column order. It stores only successfully
committed rows; failed, `NEEDS_REVIEW`, and otherwise uncommitted staging rows
never enter this tab. Every committed `ORDER`, `SKU`, and `ADJUSTMENT` source
row is preserved separately. `statement_batch_id + record_type + sequence_no`
is the stable row identity.

```text
 1. statement_batch_id
 2. record_type
 3. sequence_no
 4. platform
 5. statement_source_filename
 6. statement_file_hash
 7. statement_period_from
 8. statement_period_to
 9. statement_uploaded_at
10. statement_uploaded_by
11. statement_order_count
12. statement_sku_count
13. statement_summary_total_released
14. statement_adjustment_control_total
15. validation_status
16. commit_status
17. statement_source_row_number
18. committed_at
19. order_id
20. linked_order_id
21. order_creation_date
22. payout_completed_date
23. release_channel
24. order_type
25. total_released_amount
26. statement_product_id
27. statement_product_name
28. statement_product_price
29. statement_refund_amount
30. statement_net_selling_amount
31. adjustment_complete_date
32. adjustment_type
33. adjustment_reason
34. adjustment_amount
35. comparison_source
36. comparison_amount
37. difference
38. reconciliation_status
39. matched_item_index
40. match_method
```

Committed rows use `validation_status = PASSED` and
`commit_status = COMMITTED`. `comparison_source` is human-readable:
`Final Amount` or `Order Income`. Committed ORDER-row
`reconciliation_status` values may be `MATCHED`, `DIFFERENT`, or
`ESTIMATED_ONLY`. A blocked Statement writes no rows, so `UNMATCHED_ORDER` and
`MISSING_COMPARISON_EVIDENCE` remain staging/readiness outcomes rather than
committed `Statement_Data` values.

`match_method` is business-readable and uses the actual applicable method:
`Product Master SKU`, `Product Master Parent SKU`, `Product Name`,
`Product Name + Price`, or `Verified Name Repair`.

### Statement financial component ledger — Approved / Locked

`Statement_Financial_Components` is an append-only, exact 17-column ledger:

```text
 1. statement_batch_id
 2. statement_file_hash
 3. record_type
 4. statement_source_sheet
 5. statement_source_row_number
 6. sequence_no
 7. platform
 8. order_id
 9. statement_product_id
10. component_name
11. component_amount
12. component_note
13. statement_period_from
14. statement_period_to
15. payout_completed_date
16. committed_at
17. commit_status
```

Its stable identity is `(statement_batch_id, statement_source_sheet,
statement_source_row_number, component_name)`. A duplicate/collision is a
whole-batch zero-write blocker. Only `ORDER`, `SKU`, `SERVICE_FEE_DETAIL`, and
`SHIPPING_FEE_DISCREPANCY` records are permitted. Income records retain every
non-missing parser component under its exact Shopee header, including zero and
signed values; `None` is omitted. `Product Price` and `Refund Amount` are
intentionally duplicated as ledger evidence and must equal the corresponding
`Statement_Data` projection. Service Fee Details retain their dynamic numeric
components. Shipping discrepancies retain numeric expected/actual components
and an optional `Discrepancy reason` text-evidence record.

### UAT2 Statement schema migration contract — Approved / Locked

The pre-Phase-A live UAT2 Google Sheet has the exact Statement-approved
40-column `Invoice_Orders`, 19-column `Invoice_Items`, and exact 40-column `Statement_Data`
schemas. A narrow one-time migration may add only the empty
`Statement_Financial_Components` ledger tab after exact-header and empty-data
preflight. It must not add `Statement_Data` again or insert `difference` again.

The legacy `Invoice_Orders` header is the exact approved Invoice header before
`difference`; the target is the exact current canonical 40-column header with
nullable signed `difference` at zero-based index `35` / column `AJ`, after
`payout_completed_date` and before `source_pdf`. Blank `difference` means not
yet Statement-enriched and never means zero. It is excluded from
`source_fingerprint`.

The one-time migration must recognize only these exact states:

```text
legacy 39-column Invoice_Orders + exact Invoice_Items + Statement_Data absent
→ eligible for migration

target 40-column Invoice_Orders + exact Invoice_Items + exact 40-column Statement_Data
+ Statement_Financial_Components absent
→ eligible only for ledger-tab creation

target 40-column Invoice_Orders + exact Invoice_Items + exact 40-column Statement_Data
+ exact 17-column Statement_Financial_Components
→ already migrated; do not repeat a write

any other/mixed header or Statement_Data state
→ fail closed; zero write
```

Under the shared single-active-commit lock, migration must freshly read schema
metadata/headers, preflight the exact legacy state, build one
`spreadsheets.batchUpdate`, and freshly verify the exact target state. Its
single batch must: add `Statement_Data` using an explicitly unused sheet ID;
write its approved `A1:AN1` header; insert one `Invoice_Orders` column at
zero-based index `35`; and write `difference` at `AJ1`. It must not clear,
reset, recreate, or rewrite existing Invoice rows or change `Invoice_Items`.

If the API/network result is uncertain, reread the final deterministic schema.
If it confirms the target schema, report completion; otherwise stop and do not
blindly repeat the migration.

### Concrete Google Sheets Statement writer — Approved / Locked

The concrete Statement writer must fresh-read `Invoice_Orders`,
`Invoice_Items`, committed `Statement_Data` history, and committed
`Statement_Financial_Components` identities inside the existing
shared application commit lock. Resolve every Order target uniquely by exact
`(Platform, Order ID)` and every Item target by exact
`(Platform, Order ID, item_index)`. Missing or duplicate persisted targets,
unexpected headers, or an unbuildable request are zero-write failures.

Determine both append starts after the last populated row in that fresh
snapshot. Preserve every approved `Statement_Data` row and every committed
financial-component ledger row. Submit one `spreadsheets.batchUpdate`
containing the complete Statement projection append, financial component ledger
append, Invoice Order enrichment, and only eligible Invoice Item enrichment. Repeated
Statement SKU rows that resolve to one Item remain separate Statement rows and
produce no Item enrichment update.

Committed `Statement_Data` is historical duplicate authority. The same
committed file hash is `ALREADY_IMPORTED`; the same committed period with a
different file is `POSSIBLE_REVISION`; both are zero write.

After a successful or uncertain API response, freshly read back deterministic
Statement projection identities, financial-component identities, and expected
Invoice enrichments. All expected changes present means success; none means
safely not applied and requires a fresh commit attempt; mixed or unverifiable
state requires manual integrity recovery. Never retry automatically or claim
database rollback semantics.

### Confirmed Statement enrichment rules

After a successful Statement commit, update the matched Invoice Order with its
committed `payout_completed_date`, `payment_status = RELEASED`, and signed
`difference`. Do not overwrite `order_income` or `final_amount`, and do not
convert an existing `income_type = Estimated` to `Final`. These Invoice fields
remain the source snapshot; `comparison_source` records whether `final_amount`
or `order_income` supplied the expected amount.

For exactly one deterministically matched Statement SKU row, update the
existing Invoice Item fields:

```text
statement_product_price
statement_refund_amount
statement_net_selling_amount = statement_product_price + signed statement_refund_amount
```

If multiple committed Statement SKU rows deterministically match the same
Invoice Item, preserve every `Statement_Data` row but do not sum, first/last
select, overwrite, or otherwise enrich those three Invoice Item fields until
aggregation semantics are separately approved. Duplicate consumption alone is
not `NEEDS_REVIEW`.

### Fixed Statement workflow — Confirmed / Locked

```text
Upload XLSX
→ Parse into staging
→ Internal Statement Validation
→ Order Reconciliation
→ Admin Review
→ Ready to Commit
→ Atomic Commit
```

Internal Statement integrity validation and Invoice reconciliation are
separate concerns.

### Internal Statement validation — Confirmed / Locked

Internal Statement validation is commit-blocking. It covers the integrity of
the Statement itself, including applicable controls for:

- required sheets and required columns;
- a valid Statement period;
- Summary Total Released against Order View released totals;
- order-level component totals;
- SKU totals against Order View totals;
- service-fee controls;
- adjustment controls; and
- conflicting duplicate authoritative Order View records.

Financial comparison tolerance is RM0.02. If internal Statement integrity
fails, the outcome is `VALIDATION_FAILED` and the Statement batch must not
commit. Do not add validation formulas without a separately confirmed rule.

### Reconciliation identity and comparison amount — Confirmed / Locked

Primary reconciliation identity is `(Platform, Order ID)`; in the current
scope this is `(Shopee, Order ID)`. The Statement supplies the released amount.

Choose the Invoice comparison amount in this order:

```text
Invoice final_amount exists
→ comparison_amount = final_amount

Invoice final_amount is empty
→ comparison_amount = order_income
```

The user plans to backfill missing `final_amount` values later. Once available,
`final_amount` automatically takes priority. Do not use the removed Shopee PDF
`released_amount` field as a fallback. It remains removed from Invoice parser
and Invoice export scope; Statement released amount is separate Statement
reconciliation evidence.

Normal Shopee Invoice parsing/validation must not allow both `final_amount` and
`order_income` to be blank on a persistable Invoice. This task does not
authorize redesigning that parser/validation. Future Statement processing must
nevertheless fail closed against corrupted or legacy persisted data: if both
values are blank, do not invent an amount and do not compare the Statement
released amount to itself. Block the whole Statement commit and require review.

### Difference and core reconciliation results — Confirmed / Locked

```text
difference = Statement released amount - comparison_amount
```

Preserve the sign:

```text
abs(difference) <= RM0.02 → MATCHED
abs(difference) >  RM0.02 → DIFFERENT
```

The signed `difference` must eventually be persisted in both:

```text
Statement_Data
Invoice_Orders
```

Locked core outcomes are:

```text
MATCHED
DIFFERENT
ESTIMATED_ONLY
UNMATCHED_ORDER
MISSING_COMPARISON_EVIDENCE
```

- `MATCHED`: `final_amount`, or non-Estimated `order_income` fallback, matches
  the released amount within RM0.02.
- `DIFFERENT`: the same formal comparison source differs beyond RM0.02.
- `ESTIMATED_ONLY`: `final_amount` is absent and an explicitly Estimated
  `order_income` supplies the expected amount. Always preserve the signed
  difference, but never collapse this result into `MATCHED` or `DIFFERENT`.
- `UNMATCHED_ORDER`: the Statement Order ID currently has no matching
  InvoiceGather order.
- `MISSING_COMPARISON_EVIDENCE`: the Invoice order exists, but both
  `final_amount` and `order_income` are absent.

`MATCHED`, `DIFFERENT`, and `ESTIMATED_ONLY` are allowed reconciliation results.
`DIFFERENT` is not a commit blocker. Its signed difference is evidence only and
must not by itself be labelled as confirmed underpayment.

`UNMATCHED_ORDER` and `MISSING_COMPARISON_EVIDENCE` are commit-blocking. If even
one Statement target Order ID is missing, or one covered Invoice lacks both
comparison amounts, block the whole Statement batch commit. Do not partially
commit the matched subset.

Present `BLOCKED` as Commit Readiness, not as another persisted reconciliation
status. Report Order ID Coverage separately from Amount Reconciliation; a
covered Order ID does not prove that its amount matched.

### Payment status terminology — Confirmed / Locked

Successful Statement evidence uses:

```text
payment_status = RELEASED
```

Use `RELEASED`, not `PAID`. It means Shopee has released the payout according
to Statement evidence. It does not prove bank receipt or bank reconciliation.
A released payout may still have `reconciliation_status = DIFFERENT` and a
positive or negative difference. `DIFFERENT` must not change `RELEASED` into
`UNPAID`.

### Adjustments — Confirmed / Locked

Preserve Statement adjustment records. If an adjustment cannot currently be
matched to an Invoice/order, preserve its evidence, flag the reconciliation
issue, and do not discard it or block the whole Statement commit solely for
being unmatched.

### Historical duplicate and revision policy — Confirmed / Locked

Historical duplicate checks apply only to committed Statement batches:

```text
same committed Statement file hash
→ ALREADY_IMPORTED
→ cannot commit again

same committed Statement period but different file
→ POSSIBLE_REVISION
→ block automatic commit
→ require later explicit revision/replacement handling
```

A Statement that existed only in staging, validation, or failed review and was
never committed is not a historical committed duplicate. The user may correct
the XLSX and upload it again normally. Automatic replacement/versioning remains
unresolved and must not be invented.

### Atomic Statement commit — Confirmed / Locked

One Statement Import Batch commits all source records or none. Partial
successful-row commit is not permitted.

The following reconciliation results are not commit blockers by themselves:

```text
DIFFERENT
ESTIMATED_ONLY
unmatched adjustment
```

Commit-blocking categories include internal Statement validation failure,
database integrity failure, any `UNMATCHED_ORDER`, `ALREADY_IMPORTED`, and
`POSSIBLE_REVISION`.

Immediately before commit, freshly reload authoritative persisted state and
recheck at minimum that:

- 100% Statement target Order ID coverage still holds;
- no committed duplicate/revision conflict appeared;
- Invoice comparison source values used during reconciliation have not changed;
  and
- the reviewed batch remains valid against current persisted state.

If relevant state changed after Review, perform zero writes, require fresh
Reconcile / Review, and do not reuse stale review results for persistence. This
fail-closed behavior is locked and must run inside the shared application
commit lock defined below.

Revised Statement replacement/versioning behavior remains deferred; do not
invent automatic overwrite or supersession.

### Google Sheets UAT2 single active commit — Confirmed / Locked

The current UAT deployment is one InvoiceGather application instance with one
shared-memory process. Google Sheets remains the persistence target, and
InvoiceGather is the only supported formal writer. Multiple users/sessions may
Upload, Parse, Validate, Reconcile, and Review concurrently because those
operations do not mutate authoritative storage. At most one authoritative
Commit may run at a time.

Every authoritative InvoiceGather commit/write path must acquire the shared
application commit lock before fresh preflight. A Statement commit must:

```text
acquire shared application commit lock
→ freshly reload authoritative persisted state
→ revalidate
→ build the complete write plan
→ perform the Statement_Data + Statement_Financial_Components + Invoice_Orders + Invoice_Items write
→ resolve the final write outcome
→ release the lock
```

A second Commit must be rejected/disabled without writing, with a clear message
such as `Another commit is currently in progress. Please try again shortly.`
The lock must remain held from before the fresh reload until preflight failure,
successful write, or write-error outcome handling has completed. Direct Sheet
edits during an app commit are outside the supported concurrency model.

Concurrency exclusion and cross-tab write atomicity are separate requirements:
the process-local lock serializes supported commits in this one application
instance, while one `spreadsheets.batchUpdate` must independently cover the
approved Statement_Data inserts, Statement_Financial_Components inserts,
Invoice_Orders updates, and Invoice_Items updates. Preserve deterministic
`statement_batch_id` / stable-row-identity
readback for uncertain network/API outcomes; do not claim database rollback
semantics.

This process-local lock is intentionally insufficient for multi-instance,
multi-worker-without-shared-memory, or multiple-independent-process deployment.
If UAT is ever deployed that way, replace it with a genuine distributed
lock/lease or a transactional database such as PostgreSQL. Do not introduce
that future mechanism in the current single-instance deployment.

### Statement SKU to Invoice Item matching — Confirmed / Locked direction

Statement product enrichment requires deterministic matching. Statement
`Product ID` must not be the primary join key because `Invoice_Items` currently
has no Product ID field. Do not add Product ID to `Invoice_Items` merely to
solve this matching problem without separate approval.

Product Name comparison may use normalized exact equality only. Allowed
normalization is limited to leading/trailing whitespace trimming, repeated
internal whitespace collapse, case-insensitive comparison, and deterministic
Unicode/text normalization. Do not use fuzzy similarity, contains matching,
closest-match selection, AI/LLM guessing, first-row fallback, or min/max
heuristics.

Use this matching hierarchy:

1. Exact Order ID scopes candidate `Invoice_Items`.
2. Within that Order ID, normalized exact Product Name narrows candidates.
3. If exactly one safe candidate remains, matching may proceed subject to
   consistency validation.
4. If multiple items still share that normalized Product Name, use
   deterministic financial/quantity evidence only after the relevant Statement
   amount semantics are proven from multiple real samples. Potential Invoice
   evidence includes `line_subtotal`, `quantity`,
   `actual_selling_unit_price`, and
   `quantity × actual_selling_unit_price`; do not yet assume which Statement
   field equals which Invoice field.
5. Match only when exactly one Invoice Item can be proven.

### Repeated Statement SKU rows — Confirmed / Locked

Multiple Statement SKU rows may resolve to the same `Invoice_Item`. Duplicate
`(Order ID, Invoice item_index)` consumption is not by itself an error and
must not automatically become `NEEDS_REVIEW`.

Each Statement SKU row must still independently satisfy the deterministic
matching contract above. Do not introduce automatic aggregation or merging
semantics for repeated rows. Repeated Statement rows are a valid observed
real-data pattern, but their exact settlement semantics remain unresolved.

If any required Statement SKU row cannot be safely and uniquely matched, mark
the batch `NEEDS_REVIEW` and block the whole Statement commit. Do not partially
enrich a matched subset while silently skipping ambiguous rows. If the prior
version was never committed, the user may correct the source XLSX product data
and re-upload it under the historical duplicate policy above.

### Invoice source fingerprint boundary — Confirmed / Locked

Preserve the existing Invoice `source_fingerprint` contract. It is calculated
from Invoice source business facts and must not include later enrichment or
reconciliation values such as:

- `payment_status`;
- Statement released amount;
- the former derived/persistence-only `actual_selling_value`;
- Product Master lookup output;
- pricing status; or
- promotion allocation results.

A Statement import or Product Master update must not make an unchanged Invoice
look like a changed source Invoice. The Shopee Invoice source
`actual_selling_unit_price` remains a material Invoice fact under the existing
fingerprint rule; it is distinct from the excluded derived
`actual_selling_value`.

After a successful future Statement commit, Invoice Orders may receive
`payout_completed_date` and operational `payment_status`; Invoice Items may
receive `statement_product_price`, `statement_refund_amount`, and
`statement_net_selling_amount`. Original Invoice source facts remain
auditable and must not be silently replaced. Statement source data must also be
persisted separately.

### Deferred / Out of current Statement scope

The following are explicitly deferred or out of scope:

- bank reconciliation and actual bank receipt confirmation;
- a `PAID` / `UNPAID` / `PARTIAL` bank-payment model;
- automatic confirmed-short-payment conclusions;
- the final Potential Underpayment formula;
- the exact Statement SKU amount-to-Invoice financial disambiguation rule,
  pending real-sample evidence;
- automatic revised-Statement replacement/versioning behavior;
- Shopee Statement PDF parsing;
- Lazada Statement and ZENXIN Statement support;
- Product Master matching changes; and
- promotion pricing/allocation changes.

The system may preserve difference and reconciliation evidence, but it must not
automatically conclude that Shopee underpaid a particular amount until a later
business rule is explicitly designed and locked.

## STEP 3 — Billing / Product Summary — Future phase, document only

Billing/Product Summary is a query and aggregation from persisted data, not a
separate Product Summary database table by default. The user selects a Payout
Date range plus other filters; only successfully Statement-matched/committed
orders participate.

Combine rows only when all of Seller SKU, NAV, Product Name, Variation, and
Unit Price match. Different Variation or Unit Price is a separate row. Minimum
output is SKU, NAV, Product Name, Variation, Total Quantity, Unit Price, Total
Selling Price, and Total Given Discount:

```text
Total Quantity       = SUM(Invoice item quantity)
Unit Price           = stored Product Master Unit Price snapshot
Total Selling Price  = SUM(statement_net_selling_amount)
Total Given Discount = SUM(Unit Price × Quantity) - Total Selling Price
```

Weekly Statement is the Total Selling Price authority; do not derive it from
Invoice actual selling unit price. Charges Summary also comes from persisted
data; exact charge categories are deferred.

## STEP 4 — Database-backed Live Analysis — Future phase, document only

Future Shopee reports become database-backed Live Analysis, querying persisted
data rather than current upload-session data. Exact KPI, filter, and chart
requirements are deferred. The current session-based Shopee view may later be
reused in Data Import Validate for current-batch checks, focused on order-level
current-batch data, Manual Review, validation status, historical status, and
safe removal actions.

## V2.4 data safety and current phase boundary — Mandatory

Do not implement in the current documentation-only task or a future task
without explicit scope: Statement persistence; Statement-to-Invoice updates;
Billing Product Summary; Charges Summary; Live Analysis; manual database editor;
Lazada/ZENXIN persistence expansion; a new Product Master pricing algorithm;
or broad parser rewrites.

The existing real UAT2 Google Sheet uses the approved target Statement-capable
schema. Do not clear, reset, rerun migration, rewrite headers, backfill, or
perform any real write without separate explicit authorization.

# UAT2 V2.3 Baseline — Superseded Historical Context

## UAT2 purpose and precedence — Superseded historical context

UAT2 delivers **Statement-Driven Weekly Billing + Historical Invoice Source
Tracking + Invoice ↔ Statement Verification**. One Shopee Weekly Statement
period produces one consolidated billing dataset for the company's external
invoicing/accounting software.

This section records the earlier UAT2 baseline. Preserve its non-conflicting
guidance, especially stable parser contracts, Product Master lookup, promotion
pricing, safe Product Summary grouping, Weekly Statement parsing, Adjustment
Complete Date period ownership, Git/data safety, and stop-and-ask behaviour.
The authoritative V2.4 section above, including the Shopee Weekly Statement
Locked Scope v1, governs wherever the two conflict.

UAT2 is limited to **Shopee Weekly Billing**. Lazada and ZENXIN parsers and
weekly-billing behaviour are unchanged unless a later task explicitly expands
scope.

## UAT2 workflow — Statement first

Historical Invoice PDFs are parsed and validated through the existing flow.
Accepted Invoice order/item source facts are persisted for use by future Weekly
Statements. They provide historical product/order facts, reliable sold quantity,
and statement validation; they do not decide which orders belong in a billing
period.

```text
Upload Weekly Statement
→ authoritative Order View determines target Order IDs
→ look up historical Invoice sources
→ show Found / Missing Order IDs explicitly
→ upload only the missing Invoice PDFs
→ update historical repository
→ Reload / Recheck coverage
→ Invoice ↔ Statement verification and reconciliation
→ READY TO EXPORT
→ export one consolidated billing dataset
```

Do not require users to guess an Invoice date range. The Weekly Statement period
is a payout/settlement period, **not** an Order Creation Date period. Final
billing export requires 100% required Invoice-source coverage.

## UAT2 interim persistence — Confirmed

For UAT2, Google Sheets is the temporary persistence layer. **Do not introduce
PostgreSQL, SQLAlchemy, or Alembic in UAT2.** Use a new dedicated InvoiceGather
UAT2 data spreadsheet; never use the Product Master spreadsheet as this
database.

Canonical source-fact tabs are:

```text
Invoice_Orders
Invoice_Items
Settlement_Orders
Settlement_Items
Adjustments
```

Derived/reporting tabs are:

```text
Settled_View
Unsettled_View
Missing_Source_View
```

Settled/Unsettled are derived states. Never physically move canonical rows
between tabs, duplicate canonical facts, or use a derived view as the source of
truth. Preserve source hash/reference and auditability where practical.

## Identity and historical-source conflict handling

The primary identity remains `(Platform, Order ID)`; for this UAT2 flow,
`Platform = Shopee`. A repeated upload with the same Order ID and materially
identical source facts is **Already Imported** and must not append a duplicate.
The same Order ID with materially different facts is **Source Conflict / Needs
Review**. Do not silently overwrite historical source facts.

## Billing source authority and required invariants

Use signed `Decimal` values and the established RM0.02 tolerance unless a
stricter task rule is confirmed.

| Billing field | Authority | Rule |
|---|---|---|
| Total Quantity | Invoice | Use reliable Invoice item quantity. Refund never reduces quantity; never infer refund quantity. |
| Unit Price | Product Master | Use the normal/POS Unit Price. Do not substitute Invoice or Statement Product Price. |
| Sold Amount | Weekly Statement SKU View | Per SKU: `Product Price + signed Refund Amount`; never apply `abs()` to a refund. |
| Financial Summary | Weekly Statement | Merchandise, Shipping, Vouchers & Rebates, Fees & Charges, their combined total, and Total Released Amount come from the statement. |

Consolidated product Sold Amount is the sum of Net SKU Sold Amount for target
statement orders. It must reconcile to Statement Merchandise Subtotal. The
following is also a billing gate:

```text
Merchandise Subtotal
+ Shipping Subtotal
+ Vouchers & Rebates Total
+ Fees & Charges Total
≈ Total Released Amount
```

The required consolidated-product fields are Seller SKU, Product Name,
Variation, Total Quantity, Product Master Unit Price, and Total Sold Amount.
Reuse existing safe Product Summary identity/grouping rules; do not merge
identities merely because product names look similar.

## Refund-aware Invoice and Statement validation — Required

For a Shopee Invoice, extract an order-level Refund Amount only from its
explicit `Refund Amount` label. Preserve its sign: `-RM27.67` becomes
`Decimal("-27.67")`, explicit `RM0.00` becomes `Decimal("0.00")`, and an
absent label remains `None`/`N/A`. Do not infer it from Return/Refund text,
Product Price, Merchandise Subtotal, Order Income, Final Amount, or another
field. Its absence alone does not cause Manual Review.

Refund-aware Invoice validation is mandatory:

```text
Gross source product amount + signed Invoice Refund Amount ≈ Merchandise Subtotal
```

For example, `380.46 + -27.67 = 352.79`. This valid relationship must not be
classified as `PRODUCT_AMOUNT_RECONCILIATION_FAILED`; continue existing
financial reconciliation from Merchandise Subtotal and do not subtract the
refund twice. Refund does not modify product quantity.

Weekly Statement refund evidence has two related levels:

```text
Refund Event           = Order ID + Refund ID + order-level signed Refund Amount
Refund Item Allocation = Order ID + product/SKU + signed SKU Refund Amount
```

Do not assume a refund affects one SKU. For each order,
`SUM(SKU Refund Amount) ≈ Order-level Refund Amount` is required; a failure is
Needs Review and a billing-gate failure.

Compare signed Invoice and Statement order-level Refund Amounts:

```text
same non-zero amount                         → Matched
different non-zero amounts                   → Mismatch
Invoice None + Statement non-zero            → Mismatch
Invoice non-zero + Statement zero/no refund  → Mismatch
Invoice None/N/A + Statement explicit 0.00   → equivalent No Refund for derived validation only
```

Keep the original source distinction even for the derived equivalence. A refund
mismatch never reverses settlement, but it blocks Ready to Invoice until
reviewed.

## Settlement and Adjustment/CN rule — Confirmed

Authoritative target-order settlement evidence is an `Income` Order View row
with valid Order ID, Payout Completed Date, and Total Released Amount. If it is
valid, Settlement Status is **Settled**; the released amount may be positive,
zero, or negative. Without matching authoritative evidence, status is **No
Settlement Evidence**—not Unpaid. Bank receipt and Order Creation Date are not
required to establish settlement.

Evaluate settlement per target Order ID. Malformed unrelated rows may remain
statement-quality warnings and do not block valid target evidence. Malformed or
conflicting target evidence, including conflicting duplicate Order View rows,
is Needs Review; never use first-row-wins.

Florence Golden Rule: after an Invoice is shipped and payment is received, the
order is complete and only moves one way. Later post-payment changes are
separate CN/Adjustment events. They must not reverse Settled, reopen the
original transaction, change original Released Amount/billing/Invoice facts, or
automatically block the original Ready to Invoice state. **Adjustment Complete
Date owns the statement period; Adjustment Payout Completed Date is historical
reference only.**

## Coverage, verification, and readiness gate

Target orders are the valid Order IDs in Statement `View By = Order`. For each,
derive Found/Missing based on the historical Invoice repository. The missing
list should show Order ID and, when available, Statement Order Creation Date,
Payout Completed Date, and Statement Period. After missing PDFs are uploaded,
Reload/Recheck must update coverage without restarting the whole workflow.

Show business-facing readiness such as Statement Orders, Invoice Source Found,
Missing Invoice Source, Order Verification, Refund Verification pass/fail,
Product Reconciliation pass/fail, and Merchandise Difference. READY TO EXPORT
requires every gate to pass. Blocking conditions are:

- Missing Invoice source.
- Malformed or conflicting target settlement evidence.
- Invoice ↔ Statement refund mismatch.
- Statement Order ↔ SKU refund reconciliation failure.
- Unreliable required Invoice quantity/product identity.
- Consolidated Product Sold Amount not reconciling to Merchandise Subtotal.
- Required Product Master Unit Price unresolved or conflicting.

Use actionable reasons: Missing Source, Refund Mismatch, Product Mismatch,
Pricing Conflict, and Statement Evidence Review. A Released Amount − Order
Income difference remains informational only; do not infer underpayment.

## UAT2 architecture, implementation order, and non-goals

Keep these boundaries explicit:

```text
Invoice source parsing ≠ Statement source parsing
Source facts ≠ derived billing values
Validation ≠ persistence
Settlement ≠ refund validation
Settlement ≠ Adjustment/CN
Historical source repository ≠ billing projection
Product Master normal price ≠ actual Sold Amount
Canonical data ≠ derived Google Sheet views
```

Implement incrementally in this order: (1) refund-aware Invoice validation,
(2) Google Sheets historical-Invoice persistence abstraction, (3) persist
Invoice_Orders/Invoice_Items, (4) Statement Order/SKU persistence, (5)
statement-first coverage, (6) Reload/Recheck, (7) Invoice ↔ Statement
verification, (8) SKU refund reconciliation, (9) consolidated projection,
(10) financial summary/reconciliation, (11) readiness gate, (12) export, then
(13) UAT2 polish. Each phase requires focused tests, full regression, diff
review, sensitive scan, and its own safe commit/push.

UAT2 excludes PostgreSQL; bank reconciliation/receipt confirmation; automatic
CN accounting; reopening completed transactions; refund-quantity inference;
fuzzy matching; automatic source overwrite; Lazada/ZENXIN Weekly Billing;
replacing Product Master authority; major unrelated UI redesign; and broad
parser rewrites.

Stop and ask before deciding an unspecified field's source authority, a money
allocation without explicit evidence, product identity merging, conflict
overwrite, refund-quantity treatment, automatic acceptance of a financial
mismatch, whether an Adjustment can alter original billing, destructive Git
actions, or whether real/sensitive data may be committed.

---

# 1. Product Mission and System Positioning — Confirmed

InvoiceGather began as a Python + Streamlit PDF-to-Excel extraction tool.

V2 evolves it into an internal ecommerce data system with three major responsibilities:

```text
E-commerce Data Ingestion
        +
Validation / Reconciliation
        +
Persistent Reporting / Export
```

The long-term business questions are:

1. How many orders were received?
2. How many orders were actually shipped?
3. How much money was settled / received?

The target system lifecycle is:

```text
External Sources
      ↓
Admin Ingestion Workspace
      ↓
Staging
      ↓
Validation / Reconciliation
      ↓
Admin Review
      ↓
Atomic Commit
      ↓
Long-term PostgreSQL Production Database
      ↓
User Dashboard / Summary / Search / Export
```

## 1.1 Current InvoiceGather role

The current application should gradually become the:

> **Admin Data Ingestion & Review Workspace**

For UAT2, this workspace persists the confirmed historical Invoice and Shopee
settlement source facts to the dedicated Google Sheets UAT2 data spreadsheet.
PostgreSQL remains the long-term production direction, not the current UAT2
persistence target.

It is responsible for:

- file upload;
- parsing / extraction;
- normalization;
- temporary staging;
- validation;
- duplicate detection;
- reconciliation;
- Manual/Admin Review;
- previewing the candidate data;
- future commit to the production database.

Do not reduce the current system to a mere "cache". Its important role is to convert external files into **validated and reviewable candidate records**.

## 1.2 Future normal-user role

Future normal-user dashboards, summaries, search, filters, and Excel reports should read from the **production database**, not from whichever files happen to be uploaded in the current session.

Import and reporting must be decoupled:

```text
Upload File
→ Validate
→ Commit to Database

Report
→ Query Database
→ Any supported date / platform / product range
```

Do not permanently bind a report to one upload batch.

---

# 2. High-Level Architecture Consensus — Confirmed

Keep these responsibilities separate:

```text
Source Files
    ↓
Parser / Extraction
    ↓
Normalization
    ↓
Validation
    ↓
Staging / Review
    ↓
Reconciliation
    ↓
Repository / Persistence
    ↓
Production Database
    ↓
Reporting / UI / Export
```

Critical boundaries:

```text
Parser ≠ Database
Extraction ≠ Reconciliation
Ingestion ≠ Reporting
UI ≠ Business Logic
Current Session ≠ Production Source of Truth
Source Fact ≠ Derived Reporting State
```

Platform parsing may remain platform-specific. Do not force Shopee, Lazada, and ZENXIN into one parser strategy simply for symmetry.

## 2.1 Recommended code-direction boundary

Do not automatically restructure the repository merely because this skill mentions future layers.

When a task actually needs them, prefer gradual separation such as:

```text
parsers/
services/ingestion/
services/reconciliation/
services/reporting/
db/
domain/
ui/
```

Exact folder names are not a business requirement. The responsibility boundaries are what matter.

Do not keep adding unrelated V2 logic into `app.py` or `batch_service.py` if a clear service/module boundary is available.

## 2.2 Stable parsers are foundations

Existing Shopee, Lazada, and ZENXIN extraction logic has accumulated real-document regression coverage.

Do not broadly rewrite those parsers for:

- database integration;
- settlement integration;
- dashboard needs;
- code-style symmetry;
- architecture aesthetics.

New V2 domains should generally be added around the existing extraction engine rather than replacing it.


## 2.3 Import, validation, and commit boundaries — Confirmed

Treat ingestion as a controlled data lifecycle rather than a direct write path:

```text
Select Source
    ↓
Upload
    ↓
Batch / Source Validation
    ↓
Reconciliation
    ↓
Admin Review
    ↓
Pre-commit Database Validation
    ↓
Atomic Commit
    ↓
Production Database
```

Keep these meanings separate:

```text
Batch / Source Validation
= Is this uploaded source internally trustworthy and usable?

Reconciliation
= How does this trustworthy source compare with other known business facts?

Pre-commit Database Validation
= Can this staged batch safely write to the current production database state?

Atomic Commit
= Write the whole approved batch or write none of it.
```

Do not move source-quality failures to the database layer merely because persistence exists.

Examples that remain Batch / Source Validation concerns:

- incomplete or malformed source document;
- extraction/parsing failure;
- required source fields missing;
- financial/product reconciliation failure within the source;
- unsupported file/layout;
- duplicate identity inside the current batch.

Database-history duplicate/conflict checks belong to Pre-commit Database Validation, not to source extraction.

## 2.4 Import-batch concurrency — Confirmed V1 UX rule

One Import Batch represents one Source Type.

Examples:

```text
Platform Orders
Shopee Weekly Statement
Future Shipment Confirmation
Future Historical Import
```

For V1 Admin UX:

> **Only one Active Import Batch may be worked on at a time.**

An Admin must finish or discard the current active batch before starting another source type.

This is a UX/safety rule for the current phase, not a permanent database limitation.

Future persistent staging may allow multiple staged batches to exist, but production commits must remain controlled/serialized and each batch must still commit atomically.

Do not mix Platform Order PDFs, Weekly Statement Excel, Shipment Confirmation, and historical import data into one Import Batch.

---

# 3. Current AS-IS Baseline to Preserve

Current supported order platforms:

```text
Shopee
Lazada
ZENXIN
```

Current order-document flow is approximately:

```text
Login
  ↓
Upload PDF / ZIP
  ↓
Archive source PDF
  ↓
Platform Detection
  ↓
Platform-specific Parser
  ↓
Normalization
  ↓
Validation
  ↓
Accepted / Manual Review
  ↓
Current Batch
  ↓
Dashboard / Platform / Cross-platform Views
  ↓
Excel Export
```

Current batch uploads are incremental within the active batch. `Clear current batch` begins a new active batch.

Current order duplicate identity is:

```text
(Platform, trimmed Order ID)
```

Current duplicate detection is active-batch behavior. Persistent cross-batch duplicate/history behavior belongs to the database phase unless explicitly requested.

---

# 4. Data-Model Alignment Rule — Critical

Before changing any existing data behavior, verify:

- platform scope;
- canonical field names;
- parser-only anchors vs output fields;
- Accepted vs Manual Review semantics;
- UI labels;
- Excel fields;
- missing-value behavior;
- duplicate behavior;
- cross-platform side effects.

Never reintroduce deleted fields through placeholders, normalization, validation, UI, Excel, or new persistence models merely because a source document contains a similarly named label.

## 4.1 Shopee order fields intentionally removed from the current order model

These must remain absent as current Shopee order extracted/output/model fields:

```text
adjustment_complete_date
adjustment_reason
remark / remarks
released_amount
```

Important distinction:

The new **Weekly Statement settlement domain** may legitimately contain settlement adjustments and released amounts because it is a different source/domain.

Do **not** confuse:

```text
Removed fields from Shopee Order PDF model
```

with:

```text
Valid fields from Shopee Weekly Statement settlement data
```

A source label may also remain only as a parser boundary anchor without becoming an output field.

---

# 5. Current Platform Order Strategies — Preserve

## 5.1 Shopee Order PDF

Confirmed top-level order statuses:

```text
To Ship
Shipped
Delivered
Order Received
```

Do not treat `Completed` event text as a fifth confirmed top-level order status.

### Product extraction

Current robust strategy is based on deterministic extraction, including coordinate-aware logic and SKU/text anchors where applicable.

It must tolerate:

- multiline product names;
- Chinese product names;
- variations;
- promotions;
- supported Shopee status layouts.

### Product validation

Where the source supports the check:

```text
Expected Product Count == Valid Extracted Product Count
```

and normally:

```text
Quantity × Unit Price ≈ Line Subtotal
```

Tolerance:

```text
<= RM0.02 accepted
> RM0.02 failure
```

Explicit promotions such as `Any 4 at RM178.00` may legitimately break naive quantity × unit-price arithmetic. Do not Manual Review solely for that mismatch if promotion evidence and overall reconciliation support the row.

Also reconcile where supported:

```text
Σ Line Subtotal ≈ Merchandise Subtotal
```

### Shopee order financial parsing

Seller Income and Buyer Payment are separate sections.

Never assume:

```text
Total Buyer Payment == Order Income
```

Source labels control income semantics:

```text
Estimated Order Income → order_income + income_type = Estimated
Order Income           → order_income + income_type = Final
```

Do not infer `income_type` from order status, final amount, payment status, or settlement data.

`final_amount` and `order_income` are independent current order fields.

Seller-side reconciliation where required components exist:

```text
Product Price
+ Shipping Subtotal
+ Vouchers & Rebates (when present)
+ Fees & Charges
≈ Order Income
```

Do not double-add detail fee rows already represented by a fee total.

### Shopee completeness

A complete Income Details section must contain:

```text
Estimated Order Income
OR
Order Income
```

Missing this completion anchor is a strong Manual Review reason.

### Current Shopee Payment Status — provisional only

Current order-PDF resolver may remain:

```text
valid Fund Transfer Date → Released
else Income Type = Estimated → Pending
else → N/A
```

V2 semantics:

> This is only a **Shopee platform-side transfer signal** from the order document.

It is not final evidence of:

- actual bank receipt;
- actual finance reconciliation;
- final paid/unpaid state.

Do not label this current `Released` as `Bank Received`.

## 5.2 Lazada

Keep the stable parser; do not broadly rewrite it.

Supported real layouts include legacy invoice layout and Seller Center / Order Summary layouts that may not contain Invoice Number / Invoice Date.

For no-invoice multi-order documents, Order ID anchors such as these are meaningful:

```text
Order Number: <ID>
Your ordered items for <ID>
```

Do not reject Seller Center Order Summary solely because Invoice Number / Invoice Date are absent.

Preserve Lazada source semantics such as:

```text
Price
Paid Price
Shop SKU
Subtotal
Voucher Applied
Total
Shipping Fee
Net Paid
```

Do not blindly rename fields when source meaning differs.

Do not add broad Lazada financial reconciliation without real failure evidence and user confirmation.

## 5.3 ZENXIN

Keep the stable parser; do not broadly rewrite it.

Current product extraction supports normal row parsing plus SKU-anchor fallback.

Preserve platform-specific semantics such as:

```text
Invoice Number
Order ID
Invoice Date
Invoice Amount
Payment Method
Product Name
Seller SKU
Quantity
Unit Price
Line Total (Inc. Tax)
Subtotal
Discount
Shipping Fee
Total
```

Customer personal data is not required for default reporting/export.

---

# 6. Missing Values, Money, and Core Types

Missing is not zero.

Current display/model convention may include:

```text
missing source value → N/A / blank according to existing layer semantics
explicit RM0.00      → 0.00
```

Do not globally rewrite missing-value behavior without confirming Preview + Excel + database impact.

Money must remain numeric in calculation/storage layers. `RM` belongs to display/export formatting.

When the production database phase begins, prefer relational financial types such as:

```text
NUMERIC(..., 2) for money
INTEGER for quantity
DATE for business dates
TIMESTAMPTZ for audit timestamps
TEXT/VARCHAR for Order ID / SKU
NULL for genuinely missing database values
```

Do not store Order ID or Seller SKU as integers merely because a sample happens to contain digits.

Do not use binary floating-point as authoritative financial storage.

---

# 7. Existing Manual Review and Workflow Semantics

Manual Review is a safety layer for extracted platform-order data, not a generic bucket for every nullable field or every future reconciliation exception.

Use specific reasons where applicable, for example:

```text
Product Count Mismatch
No Valid Product Extracted
Product Amount Reconciliation Failed
Financial Reconciliation Failed
Income Completion Anchor Missing
Source Document Appears Incomplete
```

Manual Review workflow metadata is distinct from source PDF fields.

Where extraction succeeds but validation fails, useful normalized payload may be preserved for review, such as:

```text
order_payload
product_payloads
```

These payloads are workflow data, not automatically Accepted rows.

Keep these concepts separate:

```text
Manual Review
Duplicate / Duplicate Skipped
Unsupported
Processing Error
Settlement Import Validation Issue
Settlement Reconciliation Exception
Operational Exception
```

Do not collapse all of them into one generic review state.

---

# 8. Cross Platform Summary — Confirmed Current Direction

Navigation has moved from:

```text
All Products
```

to:

```text
Cross Platform Summary
```

The existing All Products detail dataset remains part of this page.

## 8.1 Page composition

Confirmed content direction:

```text
Cross Platform Summary
│
├─ Product Summary
├─ Shared Filters
│   ├─ Platform
│   ├─ From Date
│   └─ To Date
├─ All Products
└─ All Manual Review
```

Product Summary must appear above the All Products detail list.

Do not create a separate Product Summary navigation page unless the user explicitly changes this requirement.

## 8.2 Shared filters

Platform choices:

```text
All
Shopee
Lazada
ZENXIN
```

Date selection must use two explicit controls:

```text
From Date
To Date
```

Rules:

```text
From Date → inclusive (>=)
To Date   → inclusive (<=)
Both empty → no date filtering
Only From → from that date onward
Only To   → through that date
From > To → validation message; do not silently apply a reversed range
```

The same active Platform + date conditions must affect:

- Product Summary;
- All Products detail rows.

Filtering must occur before Product Summary aggregation.

If no date filter is active, missing canonical dates should not automatically hide otherwise eligible rows.

If a date condition is active, only rows without a reliable canonical reporting date may be excluded, and the UI may report the excluded count.

Do not invent dates merely to reduce the excluded count.

## 8.3 Canonical Order Created Date

Cross-platform reporting uses canonical:

```text
Order Created Date
```

Mapping:

```text
Shopee → order_created_date
Lazada → order_date
ZENXIN → invoice_date
```

This is a reporting/view-layer concept. Do not rename or destroy original platform source fields.

Where product rows do not directly carry the date, reporting may resolve it using the corresponding order identity:

```text
(Platform, Order ID)
```

Manual Review product payloads may use a reliable corresponding order payload date according to the same mapping.

## 8.4 All Products detail display

The Cross Platform `All Products` detail view should include canonical Order Created Date.

Confirmed display direction:

```text
Order Created Date
Product Name
Product Price
Seller SKU #
Qty
Platform
```

Order Created Date is used for display, sorting, and the From/To filtering semantics.

It should be presented consistently, normally `DD/MM/YYYY` in the UI, while retaining real date semantics for sorting/filtering.

The detail dataset is row-level; do not globally deduplicate it by Seller SKU.

## 8.5 All Products eligibility

All Products can include:

- Accepted product rows;
- eligible product payloads from genuine Manual Review records according to existing logic.

Seller SKU is **not** required merely to remain visible in All Products if the current All eligibility contract otherwise accepts the row.

Missing Seller SKU does not by itself convert a row into All Manual Review.

All Manual Review remains product-level eligibility review, not a sum of platform Manual Review records.

---

# 9. Cross Platform Product Summary — Confirmed

## 9.1 Product identity

Confirmed cross-platform aggregation key:

```text
Seller SKU
```

The business has confirmed Seller SKU is shared consistently across current Shopee, Lazada, and ZENXIN product records for this purpose.

Do not group by Product Name.

Product Name is primarily a display label.

If one Seller SKU has materially different names, keep one deterministic representative label and surface/report the conflict rather than splitting the SKU or inventing a new mapping model without approval.

## 9.2 Product Summary fields

Confirmed minimum fields:

```text
Seller SKU
Product Name
Total Quantity
Total Sales Amount
```

### Total Quantity

```text
Total Quantity = SUM(quantity)
```

### Total Sales Amount

```text
Total Sales Amount = SUM(product line amount)
```

Current platform mapping:

```text
Shopee → line_subtotal
Lazada → paid_price
ZENXIN → line_total_inc_tax
```

Do not sum Unit Price as sales amount.

Keep these concepts distinct:

```text
Product Sales Amount
≠ Expected Platform Payout
≠ Shopee Released Amount
≠ Bank Received Amount
≠ Profit
```

## 9.3 Missing Seller SKU

An otherwise eligible detail product with no Seller SKU may remain in All Products but must not enter Seller-SKU aggregation.

A concise excluded-from-summary count may be shown.

Do not guess a Seller SKU.

## 9.4 Shopee Product Price Lookup — Confirmed

Resolve Shopee product price deterministically in this order:

1. Match the exact Seller SKU first.
2. If the exact-SKU lookup produces one unique price, use that price.
3. If it produces multiple prices, use Product Name and Variation only to disambiguate the matching product record.
4. If multiple prices remain unresolved, classify the result as `Pricing Conflict`.
5. Use a Parent SKU fallback only when Seller SKU is blank.
6. If no applicable price is found, classify the result as `Price Not Found`.

Never guess a price by choosing the maximum, minimum, or zero value.

---

# 10. Three-Source Business Model — Confirmed Direction

The final operating model has three independent evidence domains:

```text
Platform Order Data
= received orders

Physical Order / Packing List
= actual shipment confirmation

Platform Settlement / Finance Data
= platform settlement / payment evidence
```

System internal order matching key is confirmed:

```text
(Platform, Order ID)
```

Do not reduce identity to Order ID alone.

## 10.1 Three distinct time axes

Do not collapse these dates:

```text
Order Created Date
= when the customer order was created
= sales/product reporting

Actual Ship Date
= when the order was actually shipped
= shipment reporting

Payout Completed Date
= when platform settlement was completed
= settlement/finance reporting
```

A statement downloaded for a payout date range can legitimately contain orders created earlier.


## 10.2 Source authority is fact-specific — Confirmed

Do not define one file type as globally "higher priority" than another.

Authority is defined by the business fact being established:

```text
Platform Order / Invoice
→ Order identity
→ Product / SKU / quantity
→ Platform order status
→ Expected / Final Order Income from that source

Shipment Confirmation
→ Actual Shipment evidence / Actual Shipment status

Shopee Weekly Statement
→ Shopee Settlement / Released facts

Future bank / finance evidence
→ Actual Bank Receipt facts
```

Important:

> **Higher authority for a business fact does not mean destructive overwrite of another source's original field.**

Preserve source facts separately and derive canonical operational states from them.

Examples:

```text
Invoice payment signal = Pending
Weekly Statement settlement = Released
→ preserve both
→ canonical Settlement state may be Released
```

```text
Platform order status = To Ship
Shipment Confirmation = Confirmed
→ preserve both
→ canonical Actual Shipment state may be Confirmed
→ highlight the source inconsistency if useful
```

This separation is required for auditability, reconciliation, and future underpayment/operational-exception analysis.

---

# 11. Shopee Weekly Statement — Settlement Data Contract V1 — Confirmed

A real Shopee Weekly Statement Excel sample has been reviewed.

The standard reviewed sample is conceptually represented by:

```text
Income.released.my.<period>.xlsx
```

## 11.1 Input source

For InvoiceGather settlement ingestion:

> **Shopee Weekly Statement Excel is the supported primary source.**

Do not develop a Shopee Weekly Statement PDF parser.

The PDF statement is not required for settlement ingestion.



## 11.1.1 Native Shopee XLSX compatibility — Confirmed

Production workflow must support the **original Shopee-exported `.xlsx` directly**.

The user must not be required to:

- open the file in Microsoft Excel first;
- Save As / repair the workbook;
- convert to CSV;
- manually edit worksheet dimensions, sheets, or columns.

A reviewed real Shopee export showed that the `Income` worksheet can contain full data while its worksheet metadata declares an incorrect used range/dimension such as `A1`.

Therefore:

- do not rely only on worksheet declared dimension / used range;
- if a required worksheet is present but a normal reader returns clearly implausible empty/1×1 data, use a deterministic compatibility fallback that reads actual populated worksheet cells;
- do not modify or rewrite the uploaded original workbook merely to read it;
- only classify the source as unsupported/rejected after the compatibility path also fails to recover required data;
- keep the original Shopee export as the canonical regression fixture/source evidence.

The parser/service implementation may evolve, but the user-facing contract is:

```text
Shopee Seller Centre original XLSX
→ Upload directly
→ InvoiceGather reads it successfully
```

## 11.2 Statement meaning

Weekly Statement is a:

> **Shopee Settlement Source**

It is not an Order Created Date report and not direct bank-receipt proof.

Statement period semantics:

```text
Statement Period
= Payout Completed Date range
```

Do not interpret it as the order creation range.

## 11.3 Reviewed workbook structure

The real workbook contains these important data sections/sheets:

```text
Summary
Income
Service Fee Details
Shipping Fee Discrepancy
Adjustment
```

If future real files materially change this contract, stop and ask before inventing fallback semantics.

## 11.4 Income: Order View vs SKU View

Income contains two views of the same settlement money:

```text
View By = Order
→ authoritative order-level settlement

View By = Sku
→ product/SKU allocation breakdown of that same settlement
```

Critical rule:

> **Never sum Order View and SKU View together.**

They represent the same payout at different aggregation levels and doing so would double count settlement.

### Order View

Use Order View as the authoritative order-level settlement source.

Important business fields may include:

```text
Order ID
Order Creation Date
Payout Completed Date
Release Channel
Order Type
Total Released Amount
Product Price
Refund Amount
Shipping financial components
Voucher / rebate financial components
Commission Fee
Service Fee
Transaction Fee
AMS Commission Fee
Ads Escrow Top Up Fee
Buyer Amount Paid
Buyer Payment Method
Shipping Provider
other supported statement components
```

Not every available column must become a core reporting field, but source information required for validation and future reconciliation should not be discarded prematurely.

### Total Released Amount

Use semantic naming such as:

```text
Shopee Released Amount
Settlement Amount
```

Do not call it:

```text
Bank Received
Actual Bank Payment
```

because a reviewed statement can release via Seller Wallet and does not by itself prove company bank receipt.

## 11.5 Summary sheet

Summary is primarily a **statement-level control total / validation source**.

Do not treat Summary as the detailed transaction ledger when Order View already provides the settlement records.

Reporting can later aggregate production transaction data and compare it back to the stored statement control total.

## 11.6 SKU View

SKU View provides product-level settlement allocation.

The reviewed sample demonstrates that SKU-level Total Released allocations can reconcile back to the authoritative Order View totals.

However, current Weekly Statement SKU View does not provide the confirmed cross-platform Seller SKU key needed by Product Summary.

Do not assume:

```text
Shopee Product ID = Seller SKU
```

and do not use Product Name as an automatic permanent identity mapping.

TODO:

```text
Confirm whether Shopee Product Master / Product Listing can provide:
Shopee Product ID ↔ Seller SKU
```

Until this is solved:

```text
Order-level Settlement → can proceed
Product-level Settlement by Seller SKU → deferred
```

Product-level settlement must not block the order-level settlement domain.

## 11.7 Refund semantics

Where a top-level Refund Amount and refund breakdown fields coexist:

```text
Refund Amount
= authoritative financial component for payout reconstruction

Detailed refund fields
= breakdown / explainability
```

Do not double count Refund Amount plus its breakdown values.

## 11.8 Reference-only values

Values explicitly presented as reference-only, such as relevant seller shipping-promotion reference values, must not be added into Total Released reconstruction unless the statement contract explicitly treats them as payout components.

Reference data may still be stored for explanation/analysis.

## 11.9 Service Fee Details

Service Fee Details is a supporting breakdown for Service Fee explainability and internal validation.

It may contain programme-level components such as platform/cashback/live programme fee categories.

It is useful for answering why a service fee was charged.

It is not a separate additional payout transaction.

## 11.10 Shipping Fee Discrepancy

Shipping Fee Discrepancy is an **Operational Exception** dataset.

It can provide fields such as:

```text
Order ID
Expected Shipping Fee
Actual Shipping Fee
Discrepancy Reason
```

This is not an import error merely because discrepancies exist.

It may later support operational analysis such as recurring product-weight / shipping-cost issues.

## 11.11 Adjustment

Adjustment must be stored conceptually as an **independent historical event**.

Do not overwrite an earlier Released Amount when a later adjustment occurs.

Correct conceptual model:

```text
Initial Release
+/- Later Adjustment Events
= Net Settled To Date
```

Shopee adjustments may refer to orders released in another settlement period or orders not present in the current statement period.

Therefore an adjustment is not invalid merely because its linked order is absent from the current Weekly Statement.

---

# 12. Shopee Settlement Import / Admin Review Rules V1 — Confirmed

This section defines business behavior. It does not itself instruct Codex to implement a database unless the current task asks for that phase.

## 12.1 One upload = one Settlement Import Batch

Conceptual workflow:

```text
Upload Weekly Statement Excel
        ↓
Parse to Staging
        ↓
Internal Validation
        ↓
Order Reconciliation
        ↓
Admin Review
        ↓
Ready to Commit
        ↓
Future Atomic Commit to Production DB
```

Useful batch metadata includes:

```text
Platform
Statement Period From
Statement Period To
Source Filename
File Hash
Upload Time
Uploaded By
Order Count
SKU Row Count
Total Released
Adjustment Total
Validation Result
Commit Status
```



## 12.1.1 General batch identity and duplicate boundary

Weekly Statement follows the system-wide import rule:

```text
One Source Type = One Import Batch
```

Duplicate checking is layered:

```text
Current Batch
→ detect duplicates/conflicting authoritative identities inside this batch

Production Database
→ checked later during Pre-commit Database Validation
```

Do not use current source parsing as a substitute for production-history duplicate checking.

Likewise, do not defer incomplete/corrupt/source-invalid data until commit time: source validity must be resolved before a batch becomes Ready to Commit.

## 12.2 Batch-level result semantics

Use these high-level import outcomes:

```text
Ready to Commit
Needs Review
Rejected
```

### Rejected

Reserve for files that cannot reasonably be treated as a valid supported Weekly Statement, for example:

- unreadable/corrupt workbook;
- unsupported structure;
- critical Income source missing;
- required identity/amount columns missing;
- statement period cannot be reliably determined.

### Needs Review

Use when the workbook is recognized but its **internal financial/structural validation** fails.

### Ready to Commit

Use when required internal statement validations pass.

Reconciliation exceptions against InvoiceGather orders do not automatically prevent Ready to Commit.

## 12.3 Blocking Internal Validation

Internal validation answers:

> Is this statement internally trustworthy enough to commit as source settlement data?

Confirmed validation direction includes:

1. Workbook required structure / columns are valid.
2. Statement period is valid.
3. `SUM(Order View Total Released)` reconciles to Summary Total Released.
4. Each Order View's primary financial components reconcile to that Order's Total Released Amount.
5. `SUM(SKU Total Released by Order)` reconciles to the authoritative Order View Total Released Amount.
6. Service Fee Details reconcile to the corresponding Income Service Fee where applicable.
7. Adjustment detail reconciles to the adjustment control total where applicable.
8. Conflicting duplicate authoritative Order View records inside one statement must not silently pass.

Financial tolerance:

```text
RM0.02
```

Do not add component breakdowns twice when reconstructing totals.

Do not require every SKU-level subcomponent allocation to perfectly mirror Order View component allocation if the platform uses legitimate allocation/rounding behavior; authoritative SKU Total Released → Order Total Released reconciliation is the core product-allocation check unless further rules are confirmed.

Blocking validation failure means the statement must not become Ready to Commit.

## 12.4 Reconciliation is separate from Internal Validation

After internal validation passes, compare statement orders with existing InvoiceGather order data.

Use reconciliation statuses:

```text
Matched
Different
Estimated Only
Unmatched Order
Missing Comparison Evidence
```

These are reconciliation outcomes, not Statement internal-validation results.
`Different` and `Estimated Only` are non-blocking; missing Order coverage or
comparison evidence blocks Commit Readiness for the whole batch.

### Matched

When `final_amount`, or a non-Estimated `order_income` fallback, exists and:

```text
abs(Shopee Released Amount - comparison_amount) <= RM0.02
```

### Different

When the same formal comparison source exists but the Released Amount differs beyond tolerance.

`Different` does not block settlement commit.

In V1, do not automatically conclude:

```text
Shopee underpaid
```

merely because Released < Final Order Income.

### Estimated Only

If only Estimated Order Income exists:

- a numerical comparison may be informational;
- do not treat it as a final settlement mismatch;
- do not accuse the platform of underpayment based on estimate vs settlement.
- keep the status `Estimated Only` regardless of the RM0.02 tolerance;
- calculate and display the signed difference; and
- do not rename the Invoice income type to Final after Statement commit.

### Unmatched Order

If the statement contains an Order ID not currently found in InvoiceGather / production Orders:

- mark it Unmatched;
- block the whole Statement commit with zero write; and
- allow a later historical Order import and fresh review to resolve coverage.

This is important because historical data may be imported in a different order from settlement data.

## 12.5 Unmatched Adjustment

If an Adjustment references an Order ID not currently available:

```text
Unmatched Adjustment
```

is a non-blocking reconciliation state.

Preserve the adjustment event so it can be matched later.

## 12.6 Atomic Commit — Confirmed Future Database Rule

A Weekly Statement is one financial source batch.

Future production commit must be **whole-statement atomic**:

```text
all statement source records commit together
OR
none commit
```

Do not design a production workflow where 500 rows are committed while 5 failed rows from the same internally valid statement remain outside the transaction.

These reconciliation statuses do not block atomic commit if internal validation passes:

```text
Matched
Different
Estimated Only
Unmatched Adjustment
Operational Shipping Discrepancy
```

`Unmatched Order` and `Missing Comparison Evidence` do block the whole
Statement commit. They are presented under Commit Readiness, not persisted as
an extra `BLOCKED` reconciliation enum.



## 12.6.1 Pre-commit Database Validation — Confirmed direction

Immediately before any future production commit, re-check the staged batch against the **current database state**.

Pre-commit checks may include:

- exact source/file already committed;
- existing `(Platform, Order ID)` identities;
- existing settlement/source-event identities;
- same-period statement collision/revision indicators;
- database uniqueness/integrity conflicts;
- staging data changed since its prior validation;
- any other confirmed source-specific persistence constraint.

Do not assume that an earlier staging validation remains sufficient indefinitely.

Production-history outcomes must be distinguished rather than collapsed into one generic `Duplicate` state.

Conceptually:

```text
Exact same source/data already committed
→ Already Imported / Exact Duplicate

Same business identity but materially different incoming data
→ Database Conflict / Existing Record Difference

Recognized legitimate newer independent source event
→ preserve as a new source event according to its domain rule
```

Do not silently overwrite an existing production source fact.

A database duplicate/conflict is a Pre-commit concern. An intra-batch duplicate is a Batch Validation concern.

## 12.7 Duplicate statement handling

### Exact same file

Use content/file hash as the strongest exact-duplicate signal.

An exact previously committed/imported statement must not silently create duplicate settlement records.

### Same period, different file

If the same Payout Completed Date period is uploaded with different file content:

```text
Needs Admin Review
```

Do not automatically overwrite existing data.

Compare high-level controls such as:

```text
Order Count
Order IDs
Total Released
Adjustment Total
```

If materially different, treat it as a possible revised statement case.

Final revised-statement versioning/supersession behavior remains TODO until a real case appears or the user explicitly confirms it.

---

# 13. Future Underpayment Detection — Confirmed Goal, Deferred Detailed Logic

A key long-term settlement reconciliation goal is to determine whether Shopee potentially underpaid an order.

Do **not** implement the naive rule:

```text
Released < Final Order Income
→ Shopee Underpaid
```

That is not sufficiently reliable because settlement can legitimately differ due to components/events such as:

- refund;
- adjustment;
- shipping differences;
- vouchers;
- platform fees;
- other confirmed statement components.

Target future progression:

```text
Final Order Income
      ↓
Shopee Released Amount
      ↓
Relevant Settlement Components / Adjustments
      ↓
Difference Classification
      ↓
Explained Difference
OR
Unexplained Difference
      ↓
Unexplained Negative Difference
      ↓
Potential Shopee Underpayment
```

Only after legitimate differences are accounted for should the system label a remaining negative discrepancy as potential underpayment.

The exact formula/classification is not yet locked. Ask before implementing the final underpayment decision engine.

---

# 14. Long-Term Production Database Direction — Post-UAT2

This section is a long-term direction only. It is superseded for the current
UAT2 delivery by the authoritative UAT2 Google Sheets interim-persistence
contract above. Do not begin PostgreSQL work while implementing UAT2 unless the
user explicitly reopens that scope.

The production database engine direction is now confirmed:

> **PostgreSQL**

Recommended Python integration direction:

```text
PostgreSQL
SQLAlchemy 2.x
Alembic migrations
```

This is a technology direction, not an instruction to implement persistence in every current task.

## 14.1 Hosting is still undecided

Do not hardcode the application to one provider.

Possible future deployment may include:

- company-hosted PostgreSQL;
- managed PostgreSQL;
- Supabase PostgreSQL;
- another PostgreSQL-compatible provider.

Hosting/server constraints still need company confirmation.

## 14.2 Production source of truth

After the database phase is implemented:

> **Production Database becomes the formal source of truth for normal-user reporting.**

Current Streamlit session/batch data remains ingestion/staging state, not permanent reporting history.

## 14.3 Staging direction

Development can continue using current session-state staging when appropriate.

Long-term production should support persistent staging/import batches so an Admin can return to an unfinished review without losing it when the browser/session closes.

Do not implement persistent staging automatically unless the current task reaches that phase.

## 14.4 Source audit trail

Production records should remain traceable to:

```text
Production Record
→ Import Batch
→ Source File
```

Do not discard original-source lineage after parsing.

Source files should eventually have metadata such as file identity/hash, import batch, storage location/reference, and upload audit information according to the final storage architecture.

## 14.5 Event history over destructive overwrite

For financial facts such as settlement and adjustment, preserve source events.

Do not overwrite historical source facts merely to show a new derived balance.

Conceptual distinction:

```text
Source Fact:
Release +100
Adjustment -20

Derived State:
Net Settled = 80
```

Derived values may be recalculated; source events should remain auditable.

## 14.6 Relational direction

InvoiceGather data is relational by nature:

```text
Order
 ├─ Order Items
 ├─ Shipment Confirmations
 ├─ Settlement Records
 ├─ Settlement Item Allocations
 ├─ Adjustments
 ├─ Fee / Exception Details
 └─ Source / Import Batch lineage
```

Prefer normalized relational columns for core identity/date/amount data.

PostgreSQL JSONB may be used for auxiliary source metadata, but do not put all business data into one JSON blob.

---

# 15. Conceptual Production Domains — Direction Only

Do not treat these names as a final schema unless the database-design task explicitly confirms them.

The production model will likely need concepts equivalent to:

```text
Orders
Order Items
Import Batches
Source Files
Settlement Statements
Settlement Orders
Settlement Item Allocations
Settlement Adjustments
Service Fee Details / supporting fee breakdown
Shipping Fee Discrepancies / operational exceptions
Shipment Confirmations
Users / Roles
```

Database schema design must preserve:

```text
(Platform, Order ID)
```

as the canonical order relationship key at the business layer.

Exact surrogate keys, foreign keys, uniqueness constraints, indexes, and table names belong to the database-schema task, not this skill.

---

# 16. Admin vs Normal User — Confirmed Direction

Do not create two separate applications by default.

Preferred direction:

```text
One InvoiceGather Application
+ Role-based Navigation / Permissions
```

Initial roles:

```text
Admin
User
```

### Admin responsibilities

```text
Upload
Parse / Ingestion
Validation
Reconciliation
Manual/Admin Review
Future Commit
Import History
Administrative correction workflows
```

### Normal User responsibilities

```text
Dashboard
Search
Filters
Cross Platform Summary
Settlement / reporting views
Historical reports
Excel Export
```

Additional roles such as Finance, Management, or Ecommerce may be added later only when real permission requirements justify them.

Do not rely on merely hiding sidebar items as the only security control once write operations and production DB are implemented; service/write actions must enforce authorization too.


## 16.1 Admin navigation and Data Import UX — Confirmed direction

Keep Admin navigation intentionally simple.

Do not create many separate navigation destinations merely because backend architecture has separate concepts such as staging, validation, reconciliation, issues, and commit.

Preferred Admin UX:

```text
Data Import
    ↓
Select Source
    ↓
Upload
    ↓
Validate
    ↓
Reconcile
    ↓
Review & Commit
```

These stages should normally remain in one continuous Data Import workflow.

For the current UI direction:

```text
ADMIN
- Data Import

REPORTS
- Dashboard
- Cross Platform Summary
- Shopee
- Lazada
- ZENXIN
```

Future report/navigation items such as Settlement, Shipment, Import History, or role-specific reports should be opened only when their real data/backend exists.

Do not create empty navigation pages solely to represent future architecture.

The Data Import workflow should clearly show progress/current state so an Admin knows:

- what source type is active;
- which step they are on;
- what is complete;
- what is blocked;
- what action is expected next.

Exact visual styling/progress-component choice belongs to the UI task, not this long-term skill.

### Current Data Import workflow split — Confirmed / Locked

Keep one `Data Import` area with two clearly separated modes; do not create a
standalone heavyweight Statement management page:

```text
Invoice Import
→ PDF / ZIP upload
→ Parse / Manual Review
→ Validate current-source extraction, Order Level Data, Manual Review, and processing issues
→ Reconcile historical Invoice DB Order IDs and remove only unwanted current-batch candidates
→ Review & Commit Invoice_Orders + Invoice_Items

Statement Import
→ Weekly Statement XLSX upload
→ Parse
→ Validate / Reconcile
→ compact Order and SKU review
→ guarded whole-batch Commit to Statement_Data + Statement_Financial_Components + Invoice_Orders + eligible Invoice_Items
```

Statement Import reuses the existing parser/service, reconciliation, Product
Matching Contract v1, persistence plan, concrete Google writer, and Single
Active Commit boundary. The UI must not reproduce business rules, write Google
Sheets directly, or treat Streamlit session state as authoritative.

Statement readiness requires 100% target Order ID coverage and every required
SKU row to match deterministically. `UNMATCHED_ORDER` and SKU `NEEDS_REVIEW`
block the whole commit; `DIFFERENT` remains visible and non-blocking. A stale
precommit result performs zero writes and returns the batch to fresh
validation/review.

Keep Statement presentation compact and import-focused: batch facts,
validation/readiness status, Order reconciliation rows, and SKU matching rows.
Billing remains a future committed-data query and Live Analysis remains a
future database-backed report; neither belongs in Statement Import.

### Invoice Import responsibility split — Confirmed / Locked

For the Invoice Import workflow, `Validate` answers whether the current uploaded
source is valid. It owns the current-batch Order Level Data, the read-only
Manual Review table, processing errors, and source validation status. It must
not show Historical Invoice DB classification.

`Reconcile` answers how that validated batch compares with the historical
Invoice DB. It owns Historical Invoice Status, existing Order ID
classification/reclassification, and safe removal from the **current** candidate
batch. Removal must never delete a historical DB row.

The duplicated Shopee Dashboard Order Level Data and Manual Review table are
removed. The Shopee Dashboard remains reserved for a future DB-backed Live
Analysis view. Manual Review correction/editing, Add Missing Product, and Apply
& Revalidate are explicitly deferred to Round 2.

### Manual Review resolution — Round 2

Manual Review corrects supported extraction facts only; it must never invent a
missing source value, SKU, amount, date, or zero. Use compact issue-driven
forms and Apply & Revalidate, not a generic Invoice editor. Product Count
Mismatch may add one source-supported product fact; NAV and Master Unit Price
remain authoritative Product Master-derived values. Non-resolvable source or
Product Master issues remain NEEDS_REVIEW. A corrected record re-enters the
normal Validate → Reconcile → Review & Commit workflow without a special writer.

Missing parsed Income alone is not proof that a source PDF is incomplete.
Income extraction-missing and proven source-incomplete are distinct Manual
Review states: only an extraction-missing Income value may be corrected after
the operator confirms it is visible in the original source. A source proven
incomplete remains non-resolvable and must be replaced with the complete PDF.

## 16.2 Validation Recovery UX — Confirmed principle, actions partly TODO

A validation failure must not be a dead-end error message when a safe, well-defined correction can be performed inside the current workflow.

Core UX principle:

> **Explain the exact reason, identify the affected source/record, and provide the smallest safe recovery action whenever that action is already business-approved.**

Examples of the intended direction:

```text
Intra-batch duplicate PDF / Order
→ identify duplicate
→ allow a future confirmed remove/keep action
→ revalidate without forcing the user to rebuild the whole batch

Invalid/incomplete source file
→ identify the exact file/reason
→ allow a future confirmed remove/replace action where safe

Database duplicate/conflict
→ show the existing production identity and incoming conflict
→ provide a future confirmed resolution action

Commit transaction failure
→ explain that no partial production write occurred
→ provide retry/review path when safe
```

Do not make the Admin manually discover which file caused a known validation error and then re-upload the entire batch if the system can safely isolate the problem.

However:

- do not invent destructive actions such as Delete Existing DB Record, Force Overwrite, Replace, or Skip without explicit business approval;
- recovery actions must respect audit trail and source immutability;
- after any recovery action that changes staging content, rerun the relevant validation before commit.

## 16.3 Import issues should stay in context

During an active import, prefer inline/current-workflow presentation for:

```text
Blocking Validation Issues
Warnings
Reconciliation Exceptions
Commit Failures
Recovery Actions
```

Do not force users to navigate to a separate "Import Issues" page just to understand or resolve the current batch.

Long-term unresolved production reconciliation exceptions may later have dedicated reporting views once database-backed settlement/shipment reporting exists.


---

# 17. Reporting Direction — Database-First Production Model

Current batch Dashboard and Cross Platform Summary are useful prototypes and can remain during development.

Long-term reporting should query production data rather than current upload state.

Future management concepts may include:

```text
Received Orders
Sales Amount
Actual Shipped Orders
Expected Platform Payout
Shopee Released Amount
Adjustment
Net Settled
Fees / Deductions
Difference
Settlement Reconciliation Status
```

Do not overload all of these into one ambiguous `Income` metric.

## 17.1 Period reporting

Confirmed future needs include:

```text
Week-by-week
Month-by-month
Year-by-year
Year-over-year
```

Production period reporting should be built on persistent database history.

If asked to implement these before database history exists, clarify whether the user wants a temporary current-batch-only version.

## 17.2 Export

Excel export remains core functionality.

Do not regress current platform exports or current All Products export without explicit scope.

The final Cross Platform Product Summary workbook/button layout is still not fully locked unless a task prompt confirms it.

When new reporting exports are implemented after database integration, they should respect the same data semantics as the database-backed view/filter state.

---

# 18. Actual Shipment Confirmation — Waiting for Real Sample

Confirmed business rule:

> If the ecommerce team uploads the physical order / Packing List after picking, packing, and shipping is completed, that uploaded document is evidence that the order was actually shipped.

The future shipment source should primarily identify:

```text
Platform
Order ID
```

Do not assume it must re-extract all products, quantities, and amounts already known from platform order data.

Exact shipment-date source remains deferred until a real physical-order/Packing-List sample is reviewed.

Do not substitute upload time or folder date unless the user explicitly approves that rule.

## OCR

Do not add OCR now.

After real samples arrive:

- use deterministic text extraction if a usable text layer exists;
- only consider targeted OCR fallback if real scans are image-only and required identifiers cannot be reliably extracted.

Ask before introducing OCR architecture.

---

# 19. Historical Data Import — Confirmed Need, Source Format TODO

Production needs persistent historical data so users can:

```text
view old reports
avoid repeated re-upload
search/filter historical records
export historical records
reconcile older settlement data
```

Historical platform order export format is still unknown.

TODO: confirm whether historical master data arrives as:

```text
Excel
CSV
PDF
another platform export
```

Prefer structured Excel/CSV ingestion for bulk historical master data if available.

Do not invent a historical import contract before seeing the real source.

Historical settlement data can be imported independently of orders if the settlement source passes its internal validation; unmatched order relationships may be reconciled later.

---

# 20. Google Drive — Confirmed Manual Workflow Only

Current intended team workflow:

```text
Google Drive stores/organizes source files
        ↓
User manually selects/downloads/uploads files
        ↓
InvoiceGather ingestion
```

Do not build automatic Google Drive synchronization unless explicitly requested later.

---

# 21. TikTok — Deferred

TikTok is not currently supported because no real samples have been reviewed.

Do not create TikTok extraction or mapping rules based on Shopee/Lazada assumptions.

Wait for real TikTok samples.

---

# 22. Profit / COGS — Out of Scope

Current data does not provide reliable product cost / COGS.

Do not implement or label metrics as:

```text
Profit
Gross Profit
Product Margin
COGS-based Profitability
```

Allowed financial/reconciliation concepts include:

```text
Sales Amount
Platform Fees / Deductions
Expected Payout
Shopee Released Amount
Adjustment
Net Settled
Difference
Potential Underpayment (after future validated logic)
```

These are not profit.

---

# 23. Product-Level Settlement Allocation — Partially Resolved, Mapping TODO

Earlier V2 planning assumed order-level actual settlement might require a custom formula to allocate money across products.

The reviewed Shopee Weekly Statement now provides SKU-level settlement allocations, so for Shopee the platform may already provide the authoritative product allocation.

However, current unresolved mapping is:

```text
Shopee Statement Product ID
        ↕
Cross-platform Seller SKU
```

Therefore:

- do not invent proportional allocation for Shopee while platform allocation exists;
- do not map by Product Name automatically;
- do not assume Product ID equals Seller SKU;
- keep product-level settlement deferred until reliable Product ID ↔ Seller SKU mapping is available.

For another future platform that provides only order-level payment, any product allocation formula remains a separate business rule and must be confirmed before implementation.

---

# 24. Duplicate, Conflict, Revision, and Immutability Direction

Duplicate handling is explicitly layered.

## 24.1 Batch layer

The current batch checks only identities/files within that active batch.

For Platform Orders:

```text
same (Platform, Order ID) appears more than once in the current batch
→ Intra-batch Duplicate
```

The batch layer must still reject/review source-quality failures such as incomplete documents, parser failure, or source financial/product validation failure.

It does **not** need production database history to decide whether the uploaded source itself is valid.

## 24.2 Pre-commit database layer

Before production write, compare staged records against the current database.

Do not reduce all outcomes to `Duplicate`.

Distinguish at least conceptually:

```text
Already Imported / Exact Duplicate
Database Conflict / Existing Record Difference
Possible Revised Statement
Legitimate New Independent Event
```

Exact resolution behavior may differ by source/domain and must be confirmed before destructive action.

For settlement statements:

```text
Exact same source hash
→ duplicate; do not create another equivalent import

Same statement period + different content
→ Admin Review / possible revised statement
```

Do not automatically overwrite a previously committed source fact.

Potential future revised-statement behavior may use version/supersession history rather than destructive replacement, but this remains TODO until a real case or explicit decision.

## 24.3 Source facts are immutable/auditable by default

Higher-authority evidence updates derived/canonical business status; it does not erase lower-authority source evidence.

Examples:

```text
Weekly Statement confirms settlement
→ add settlement source fact
→ derive settlement status
→ do not rewrite the original Invoice payment signal

Shipment Confirmation proves actual shipment
→ add shipment source fact
→ derive actual shipment status
→ do not erase the platform order status
```

---

# 25. Long-Term Development Roadmap — Post-UAT2 Guidance

For the current delivery, follow the UAT2 implementation order above. This
roadmap resumes after UAT2 and must not be read as permission to introduce
PostgreSQL during UAT2.

Use the following sequence by default to reduce refactor/rework cost. A task prompt may intentionally work on an isolated later concern.

```text
PHASE A — UI FOUNDATION

1. Keep this Skill current with confirmed system rules.
2. Presentation/UI refactor:
   Data Import as one step-by-step workflow.
3. Move/reuse existing Platform Order PDF/ZIP upload inside the workflow without changing business logic.
4. Connect the completed Shopee Weekly Statement XLSX ingestion/validation/reconciliation backend to the workflow.
5. Define a Unified Import Result Contract so UI does not need one-off handling for every future importer.


PHASE B — VALIDATION UX

6. Define Validation Recovery Contract:
   map each blocking/non-blocking condition to its safest minimal Admin action.
   Do not invent destructive DB actions.


PHASE C — DATABASE FOUNDATION

7. Design Production Database Schema V1 from real Order + Settlement contracts.
8. Add PostgreSQL + SQLAlchemy + Alembic + repository/transaction foundation.
9. Implement Pre-commit Database Validation as a distinct layer.
10. Implement Platform Order atomic commit first.
11. Implement Shopee Weekly Statement atomic commit second.


PHASE D — PRODUCTION WORKFLOW

12. Implement approved Validation Recovery actions.
13. Add Import History / audit trail views.
14. Migrate Dashboard / Cross Platform / platform reporting from current session data to production database queries.
15. Add Admin/User role-based navigation and write authorization after the page/write boundaries stabilize.


PHASE E — BUSINESS INTELLIGENCE / DEFERRED SOURCES

16. Settlement reconciliation V2:
    Explained Difference / Unexplained Difference / Potential Underpayment.
17. Shipment Confirmation after real physical-order/Packing-List samples.
18. Historical import after real historical source format is known.
19. Week / Month / Year / YoY production reporting on persistent history.
20. Product-level Shopee settlement after Product ID ↔ Seller SKU mapping.
```

Do not combine UI refactor + database migration + new financial inference in one task unless the user explicitly requests that risk.

Prefer changing one architectural axis at a time:

```text
Presentation
OR
Business Logic
OR
Persistence
```

Stable parsers should normally remain untouched throughout these phases unless a real supported source requires a parser fix.

---

# 26. Explicit TODO / Waiting List

Keep these unresolved items visible:

```text
[TODO]    Unified Import Result Contract
[TODO]    Validation Recovery action matrix
[TODO]    Shopee Product ID ↔ Seller SKU source / Product Master mapping
[TODO]    Final Potential Underpayment classification formula
[TODO]    Same-period different-file / revised statement versioning behavior
[TODO]    Production Database Schema V1
[TODO]    Production hosting/server/network/privacy constraints
[TODO]    Historical platform order export format
[WAITING] Historical ecommerce master dataset sample
[WAITING] Physical order / Packing List real examples
[WAITING] TikTok real samples
[DEFER]   Destructive DB recovery actions until explicitly approved
[DEFER]   OCR until real shipment scans require it
[DEFER]   Product-level Shopee settlement until Product ID ↔ Seller SKU mapping
[DEFER]   Bank receipt reconciliation until actual bank/finance evidence exists
[DEFER]   Final Cross Platform summary export workbook UX unless explicitly confirmed
```

---

# 27. Scope Exclusions Unless Explicitly Reopened

Do not turn InvoiceGather into:

```text
full ERP
inventory management system
COGS/profitability engine
bank reconciliation engine without bank evidence
LLM-first financial document reader
OCR-first ingestion system
Google Drive auto-sync service
TikTok parser without samples
advanced BI platform merely for appearance
```

LLM/Vision may only be considered later as a targeted fallback if deterministic extraction cannot cover a real supported document class. It must not silently become the sole authority for financial amounts.

---

# 28. Stop-and-Ask Conditions — Mandatory

Before implementing, ask the user if a task requires choosing any of these without an already confirmed rule:

- a new cross-platform semantic mapping;
- a new required/optional source field with business impact;
- removal of fields across platforms;
- a new platform-order Manual Review trigger at scale;
- a settlement financial component formula not confirmed by real data;
- final Potential Underpayment classification logic;
- Shopee Product ID → Seller SKU matching method;
- product-level settlement allocation when no authoritative platform allocation exists;
- final bank Paid/Received semantics;
- shipment-date source before sample review;
- OCR architecture;
- historical import format;
- destructive database migration or overwrite behavior;
- destructive validation-recovery action such as deleting/replacing an existing production record, force overwrite, or silent skip;
- revised statement supersession/version logic;
- automatic Google Drive integration;
- TikTok extraction rules;
- final new Cross Platform export workbook/button structure;
- changes that make large amounts of previously Accepted data become Manual Review;
- changes to current active-batch duplicate semantics outside explicit persistence scope;
- any consequential architecture choice that conflicts with this system consensus.

When uncertain, report what the current code/data does and ask a short plain-language question.


# 28.1 Large-file / Codex context-efficiency guardrail

InvoiceGather may process large Excel/PDF datasets.

Complete validation coverage must be preserved, but large raw datasets should normally be processed locally rather than dumped into model context.

Preferred workflow:

```text
Process full source locally
→ calculate schema/counts/totals/validation
→ surface only necessary summary + representative exceptions
```

For large Excel files, normally report:

- sheet names / required columns;
- row counts / unique counts;
- date ranges;
- financial totals;
- pass/fail counts;
- at most a small representative set of exceptions unless more is explicitly requested.

Do not repeatedly print hundreds of Order/SKU rows merely to prove processing occurred.

Do not reduce validation coverage to save context; save **model context**, not data-quality checks.

Reuse already-confirmed workbook contracts within the same task instead of repeatedly re-auditing the entire large fixture when no relevant input changed.


---

# 29. Regression Guardrail

Every implementation task should preserve existing platform behavior unless explicitly in scope.

Before completion of code changes:

1. Run focused tests for the changed component.
2. Run the full test suite when practical.
3. Report exact test results.
4. Do not claim support for layouts not represented by real samples/tests.
5. Prefer a regression test based on a real failure case for parser changes.
6. Do not modify parser behavior solely to improve dashboard completeness statistics.
7. Do not weaken validation merely to make more rows appear Accepted.
8. Do not make financial reconciliation silently discard unmatched but internally valid source facts.
9. Keep the original malformed-dimension Shopee Weekly Statement XLSX as a regression case; direct original-export ingestion must remain supported.

The project's enduring value is:

> **Reliable source data → validated structured records → auditable production facts → trustworthy operational/reporting output.**

---

# 30. Git Development Guardrails — Confirmed

- Do not develop a new feature or fix directly on `main`.
- Before substantial work begins, confirm the intended branch.
- After a logical task is complete: run the relevant tests, review the diff, commit, then push the feature branch.
- Do not leave completed work in the working tree for an extended period.
- Ask the user before merging work into `main`.
- Never commit real PDF/XLSX source files, `archive/`, secrets, environment files, pytest artifacts, or confidential customer, order, or financial data.
- Ask the user before `git reset --hard`, `git clean`, force push, or a destructive rebase.
- If a business rule, data meaning, architecture decision, or Git state is uncertain: stop and ask the user.
