---
name: invoicegather-scope
description: Long-term system consensus, business rules, data semantics, architecture guardrails, import/commit workflow, validation/reconciliation boundaries, UI/UX direction, and development constraints for InvoiceGather V2. Use whenever developing, debugging, reviewing requirements, architecture, parsers, validation, reconciliation, Streamlit UI, reporting, persistence, database, settlement, shipment, roles, or Excel export. Preserve stable extraction behavior, separate batch validation from pre-commit database validation, preserve source facts, and ask before locking uncertain business rules.
---

# InvoiceGather Scope & Development Guardrails — V2.2

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
PostgreSQL Production Database
      ↓
User Dashboard / Summary / Search / Export
```

## 1.1 Current InvoiceGather role

The current application should gradually become the:

> **Admin Data Ingestion & Review Workspace**

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
```

These are **non-blocking reconciliation outcomes**, not statement corruption.

### Matched

When a corresponding Shopee order has Final Order Income and:

```text
abs(Shopee Released Amount - Final Order Income) <= RM0.02
```

### Different

When a corresponding Shopee order has Final Order Income but the Released Amount differs beyond tolerance.

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

### Unmatched Order

If the statement contains an Order ID not currently found in InvoiceGather / production Orders:

- preserve the settlement record;
- mark it Unmatched;
- do not reject the statement;
- future historical Order import may allow reconciliation later.

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

However, these reconciliation statuses do not block atomic commit if internal validation passes:

```text
Matched
Different
Estimated Only
Unmatched Order
Unmatched Adjustment
Operational Shipping Discrepancy
```



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

# 14. Production Database Direction — Confirmed Engine, Hosting TODO

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

# 25. Current Development Roadmap — Guidance, Not Automatic Execution

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
