# InvoiceGather

InvoiceGather is a Streamlit-based **reconciliation and evidence system** for
Shopee, Lazada, and ZENXIN source documents. It validates Invoice-derived
business data, persists that data as the operational **Zenxin DB**, and
reconciles Shopee Weekly Statements against it. It is not a general ledger,
credit-note engine, or a workflow that requires a separate ERP/warehouse
database.

## Features

- Username/password login with salted PBKDF2 hashing
- Mixed-platform PDF upload and batch processing
- Platform detection via positive anchors
- Dedicated parser modules for Shopee, Lazada and ZENXIN
- Two-layer data model: Order-level finance + Product-level details
- Overall dashboard based on Order-level totals and Product-level quantities
- Shopee / Lazada / ZENXIN independent tabs with platform-specific columns
- Platform-level Excel export with `Summary`, `Orders`, and `Products` sheets
- Batch-specific archive storage and review handling
- Shopee Weekly Statement native-XLSX ingestion, financial-component evidence,
  Reconciliation V2, guarded Commit, and readback verification

## Current Shopee reconciliation model

```text
Invoice PDF → Validate → Commit Zenxin DB
Shopee Statement → Upload → Reconcile → Review → V2-aware Commit → Readback

Later: Billing readiness → Billing Summary → Analysis
```

Reconciliation V2 keeps product identity, merchandise value, final seller
settlement, and item allocation distinct:

- Identity scope: `ITEM`, `GROUP`, or `UNRESOLVED`.
- `GROUP` proves a complete compatible product group but not an individual
  Statement-row-to-Invoice-item allocation. It is valid reconciliation, but
  `matched_item_index` and Invoice-item Statement amounts remain blank.
- Settlement basis: `EXACT`, `EXPLAINED`, or `NONE`. Both `EXACT` and
  `EXPLAINED` reconcile final seller settlement; `NONE` blocks Commit.
- RM0.02 is the per-order tolerance. Missing money is never zero and residuals
  cannot be cancelled across orders.
- Statement financial components explain legitimate differences. Adjustments
  are separate events and never rewrite original Invoice Income, Final Amount,
  or Refund.

The real Streamlit Validate path is integrated with V2. Statement Commit
fresh-reads Invoice data and Product Master under the shared commit lock,
reruns V2, compares the reviewed evidence fingerprint, makes one atomic Google
Sheets write, and verifies the result by readback. If the evidence is stale,
it makes zero writes and requires a new review.

The Golden acceptance benchmark (2026-08-31 to 2026-09-06) is test evidence,
not hard-coded production logic: 296 orders, 442 Statement SKU rows, 396
relationship-level `ITEM` and 46 `GROUP` identities, 296/296 merchandise
reconciled, 138 `EXACT`, 158 `EXPLAINED`, 0 `NONE`, and RM0.00 unexplained
residual. Statement quantity is not present in the authoritative source, so
no quantity-match claim is made.

## Quick start

1. Create a virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start the app:

   ```bash
   streamlit run app.py
   ```

4. Sign in with the default admin credentials:

   - Username: `admin`
   - Password: `admin123`

## Notes

- The first version is intentionally rule-based and does not use AI extraction.
- Archived PDF files are stored in the `archive/<batch_id>/` folder.
- After processing, additional PDF uploads are appended to the active batch; use **Clear current batch** to start a new one.
- Duplicate checks use `(Platform, Order ID)`.
- Manual review is generated only for non-deterministic or failed parsing cases.
- Manual review items are exported in a separate review report.
- Google Sheets UAT schemas are locked at 44 `Invoice_Orders`, 22
  `Invoice_Items`, 40 `Statement_Data`, and 17
  `Statement_Financial_Components` columns. Do not change schemas or business
  rules merely to make a reconciliation pass.
- Billing, bank-receipt reconciliation, automatic CN/accounting normalization,
  and final underpayment classification are deferred.
