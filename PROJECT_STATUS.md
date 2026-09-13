# InvoiceGather Project Status

## Current Baseline — 2026-09-13

Branch: `feature/uat2-invoice-persistence-integration`
HEAD: `d8cef87` — `feat: add v2-aware statement commit`

InvoiceGather is a reconciliation-and-evidence system. Its committed
Invoice-derived dataset is the operational **Zenxin DB**; it is not dependent
on a separate ERP or warehouse database for Shopee reconciliation.

```text
Shopee / Lazada / ZENXIN source documents
→ extraction and validation
→ committed Invoice-derived business data (Zenxin DB)

Shopee Weekly Statement
→ reconciliation against Zenxin DB
→ reviewed, guarded Statement commit
```

## Implemented

- Deterministic Shopee, Lazada, and ZENXIN Invoice PDF extraction and
  validation, Product Master enrichment, and Invoice persistence.
- Shopee Weekly Statement native-XLSX ingestion, source validation, detailed
  financial-component evidence, and Reconciliation V2.
- The real Streamlit path: Statement upload → review → V2 evaluator → adapter
  → UI.
- V2-aware Statement Commit: fresh authoritative reads under the shared commit
  lock, V2 rerun, reviewed-evidence fingerprint check, one atomic Google write,
  and deterministic readback verification.
- Closed original Invoice immutability: a later source change is evidence for a
  separate adjustment domain, never an automatic rewrite of original Income,
  Final Amount, or Refund.

## Reconciliation V2 Contract

- Identity scope is `ITEM`, `GROUP`, or `UNRESOLVED`.
- `GROUP` is a valid product-group reconciliation without a provable individual
  Statement-row-to-Invoice-item allocation. It is not an error and must never
  invent `matched_item_index` or per-item Statement money.
- Merchandise and final seller settlement are distinct results.
- Settlement basis is `EXACT`, `EXPLAINED`, or `NONE`; settlement is reconciled
  when the basis is not `NONE`. `EXPLAINED` is a pass: authoritative Statement
  components explain the difference within the RM0.02 per-order tolerance.
- Missing money is not zero; residuals never cancel across orders; Statement
  adjustments remain separate from original order settlement.

## Golden Acceptance Benchmark

The period **2026-08-31 to 2026-09-06** is an acceptance/regression benchmark,
not production logic:

| Measure | Accepted result |
| --- | ---: |
| Statement orders / SKU source rows | 296 / 442 |
| Relationship identity evidence | 396 `ITEM`, 46 `GROUP`, 0 `UNRESOLVED` |
| Order-level scope summaries | 278 `ITEM`, 18 `GROUP` |
| Merchandise reconciled | 296 / 296 |
| Invoice and Statement Product Price | RM15,948.77 each |
| Settlement | 138 `EXACT`, 158 `EXPLAINED`, 0 `NONE` |
| Unexplained residual | RM0.00 |
| Separate Adjustment total | RM122.20 |
| Commit plan | 740 `Statement_Data` rows; 15,965 component rows |

Statement quantity is absent from the authoritative source, so the benchmark
makes no quantity-match claim.

## Persistence

Approved schema widths remain unchanged: `Invoice_Orders` 44,
`Invoice_Items` 22, `Statement_Data` 40, and
`Statement_Financial_Components` 17 columns. The Golden Statement write is one
atomic `values.batchUpdate` request (known serialized size: 5,445,750 bytes,
about 5.19 MiB), followed by readback. `ITEM` enriches only the proven item;
`GROUP` persists Statement evidence without Invoice-item enrichment.

## Current Priorities

1. Validate real persistence/readback with authoritative UAT data.
2. Define and implement the Billing readiness gate.
3. Build Billing Summary, then database-backed Analysis.
4. Consider storage optimization / PostgreSQL later; do not prematurely remove
   financial evidence or introduce CN/accounting normalization.

## Deferred / Guardrails

- Billing is not yet implemented.
- Do not treat Statement release as bank receipt or conclude Shopee underpayment
  without an unexplained, governed residual.
- Do not fabricate GROUP allocation, fuzzy-match products, infer missing money,
  widen tolerance, or silently overwrite source facts.
- Do not change business rules, schemas, or source interpretation merely to
  make a reconciliation green. Report the exact evidence and classify the
  issue instead.
