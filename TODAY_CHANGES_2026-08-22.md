# 2026-08-31 - Product Master Quality Report

- Added a read-only Product Master Quality Report service for exact Shopee Invoice Seller SKU candidate diagnostics against both Product Listing SKU and Parent SKU.
- The report makes candidate rows, unique candidate prices, matched-via evidence, lookup status, and Product Name/Variation mismatches auditable without changing parser, pricing, validation, review, reconciliation, or Product Summary runtime results.
- Added synthetic coverage for parent-only/both candidate pools, duplicate same-price candidates, exact Name/Variation disambiguation, unresolved conflicts, not-found SKUs, mismatch evidence, and opt-in developer CSV output.

# InvoiceGather changes — 2026-08-22

This note records the InvoiceGather changes completed in this Codex session.

## 1. Batch upload outcomes

- Continued uploads append to the active batch instead of replacing existing extracted records.
- Duplicate handling remains per `(Platform, Order ID)`: duplicate orders are skipped from the accepted batch and reported separately.
- Unsupported files and processing errors are kept out of Manual Review data tables and shown as file-level outcomes.
- The latest processing action records PDF processing, imported orders, Manual Review, duplicate, unsupported, and error outcomes for the UI.

## 2. Collapsible workspace sections

- Added native Streamlit collapsible sections for filters, Orders, Products, Manual Review, All Products, All Manual Review, exports, and skipped/error details.
- Large data tables are no longer expanded by default.
- The upload entry point and key batch feedback remain directly visible.

## 3. Export-first UI

The primary page flow is now:

```text
Upload PDF/ZIP → Process files → Result summary → Export Batch Excel
```

- The top area focuses on `Upload PDF or ZIP` and the primary `Process files` action.
- A compact Result summary shows current-batch PDFs, Orders, Products, and Manual Review counts.
- Duplicate skipped and Unsupported counts are low-priority summary text, with their details available in a collapsed section.
- A Manual Review warning and `View issues` action appear only when actionable review records exist.
- `Export Batch Excel` is the primary download action directly below the summary.
- The primary batch export reuses the existing All-products export path: the dedicated one-sheet `Products` workbook, existing All eligibility, fields, and formatting are unchanged.
- All / Shopee / Lazada / ZENXIN tabs, filters, column selection, previews, and `Export current view` remain available inside the default-collapsed `View & Customize` area. `All` remains the first tab.
- Clear Current Batch is now inside the sidebar's collapsed `Batch actions` area.
- No empty dashboard, preview table, or export control is shown when the relevant data is unavailable.

## 4. Scope confirmation

Only presentation/UI orchestration and UI regression tests were changed for the Export-first redesign.

No changes were made to:

- platform parsers;
- validation or financial reconciliation;
- duplicate or unsupported-file business rules;
- Manual Review eligibility or payload rules;
- All eligibility;
- analytics/dashboard metric definitions; or
- Excel data content and workbook format.

## 5. Verification

- Focused Streamlit UI tests: `8 passed`
- Full test suite: `95 passed`
- Streamlit app: `http://localhost:8501` responded with HTTP 200 after hot reload.

## Main files touched today

- `app.py`
- `src/invoice_app/services/batch_service.py`
- `src/invoice_app/services/all_products.py`
- `src/invoice_app/services/exporter.py`
- `tests/test_app_batch_state.py`


## 6. All Products order date display

- Replaced the on-screen All Products `Delivery Fee` column with `Order Date`.
- The display date uses the existing normalized source field for each platform: Shopee `order_created_date`, Lazada `order_date`, and ZENXIN `invoice_date`.
- The dedicated All-products Excel export remains unchanged and continues to include Delivery Fee.

## 7. Verification

- Python compilation: `app.py` and `all_products.py` passed.
- Focused All Products tests: `8 passed`.
- Focused All Products plus Streamlit UI tests: `18 passed`.
- Full test suite: `103 passed`.

## 6. Platform date display regression

- Scoped date parsing and `DateColumn` configuration to the active platform table.
- Lazada `dd mm yyyy` dates remain typed and chronologically sortable.
- ZENXIN `dd/mm/yyyy` Invoice Date is now typed with its own source format, rather than being coerced with Lazada's format and rendered as `None`.
- No parser, batch, validation, preview data source, or Excel export behavior changed.

## 7. Verification

- Added Streamlit regression coverage for ZENXIN Invoice Date display typing.
- Python compilation: `app.py` passed.
- Focused Lazada and ZENXIN Streamlit date tests: `2 passed`.
- Full test suite: `104 passed`.

## 8. All Products date display removed

- Removed the on-screen All Products `Order Date` column after confirming that eligible Manual Review product payloads may not retain a source date.
- The on-screen table now shows Product Name, Product Price, Seller SKU #, Qty, and Platform. Delivery Fee remains excluded from the table.
- No parser, Manual Review eligibility, Accepted-data, analytics, or Excel-export behavior changed.

## 9. Verification

- Python compilation: `app.py` and `all_products.py` passed.
- Focused All Products plus Streamlit UI tests: `19 passed`.
- Full test suite: `104 passed`.

## 10. All Products Excel export fields aligned

- Updated the dedicated one-sheet All Products workbook to export the same five fields as the on-screen table: Product Name, Product Price, Seller SKU #, Qty, and Platform.
- Delivery Fee and Order Date are excluded from the All Products workbook. The All Manual Review table continues to display Delivery Fee.
- No parser, Manual Review eligibility, Accepted-data, analytics, or workbook styling behavior changed.

## 11. Verification

- Python compilation: `app.py` and `all_products.py` passed.
- Focused All Products workbook and Streamlit UI tests: `19 passed`; generated workbook headers were checked directly.
- Full test suite: `104 passed`.

## 12. Shopee Payment Status

- Added the Shopee order-level `Payment Status` field using only Fund Transfer Date and Income Type: `Released`, `Pending`, or `N/A`.
- Accepted orders and Manual Review payloads share the same resolver. Shopee Manual Review and Orders Preview show the field, and selected Orders Excel exports include it.
- No PDF extraction, financial reconciliation, Manual Review validation, Dashboard, All Products, Lazada, or ZENXIN behavior changed.

## 13. Verification

- Python compilation: `app.py`, `shopee_mapper.py`, and `batch_service.py` passed.
- Focused Shopee, Excel, Dashboard/All, and Streamlit regression tests: `55 passed`.
- Full test suite: `111 passed`.

## 14. Current System — 2026-08-24

- product: deterministic Streamlit PDF/ZIP-to-Excel tool; Shopee, Lazada, ZENXIN
- batch: mixed uploads append; explicit Clear resets; processed PDFs counted
- pipeline: detect → parse → normalize → validate → Accepted/Warning/Manual Review
- views: Dashboard, All Products, platform Orders/Products, Manual Review
- export: selected platform columns → Excel; All Products → one Products sheet
- shopee: layered product/financial parsing; Income Type + Payment Status (`Released`/`Pending`/`N/A`)
- boundaries: no database/history, OCR, or LLM path; All is display/export-only
- source: GitHub `main` @ `ef53de8`; PDFs, briefs, archives, secrets remain local
- streamlit: redeploys only if configured for this repo/`main`; Cloud result unverified
- verified: fresh full pytest passed (`111`); workspace-local basetemp used due Windows Temp access denial

## 15. Cross Platform Summary

- Renamed the sidebar `All Products` navigation item to `Cross Platform Summary`; Dashboard, Shopee, Lazada, and ZENXIN navigation remain unchanged.
- Added a reporting-only Product Summary above the shared filters, All Products detail table, and All Manual Review table.
- Product Summary groups by Seller SKU only, keeps the first source Product Name as its display label, sums Qty, and sums platform source product amounts: Shopee `line_subtotal`, Lazada `paid_price`, ZENXIN `line_total_inc_tax`.
- Added shared Platform and Order Created Date Range filters. The reporting date is derived without changing source fields: Shopee `order_created_date`, Lazada `order_date`, ZENXIN `invoice_date`; filtering happens before SKU aggregation and affects the All Products detail rows too.
- Existing All Products eligibility, All Manual Review handling, platform pages, parsers, batch/duplicate rules, payment status, and the existing five-column All Products Excel workbook remain unchanged. Seller-SKU-missing rows stay visible in All Products but are not aggregated; a missing source line amount remains `N/A`, never zero.

## 16. Verification

- Python compilation: `app.py`, `all_products.py`, and the updated regression tests passed.
- Focused All Products and Streamlit tests: `20 passed`.
- Full test suite: `112 passed in 26.59s` using a workspace-local pytest basetemp.

## 17. Cross Platform Summary V2 date audit

- Audited the canonical reporting-date path without changing parsers or source fields. Accepted detail rows now use the canonical order-level date first and fall back only when needed to the existing product source field; Manual Review rows use `review.order_payload` first, then the product source field.
- The fallback mapping remains reporting-only: Shopee `order_created_date`, Lazada `order_date`, ZENXIN `invoice_date`. It resolves source dates already present in product payloads rather than inventing a date.
- Platform and Order Created Date Range still operate on one filtered reporting-row population: no date range retains date-missing detail rows; an active range excludes only rows whose canonical reporting date is truly unavailable. The same filtered population drives Product Summary and All Products.
- Deterministic regression-fixture audit: before the product-source fallback, 6 reporting rows would have been date-missing (Shopee 3, Lazada 2, ZENXIN 1); after it, 1 remains (Shopee 1, Lazada 0, ZENXIN 0). This is fixture evidence, not a claim about a live user batch.
- Added coverage for multiple orders and platforms sharing one Seller SKU, all three platform amount/date mappings, Manual Review order-payload priority, product-date fallback, true missing-date range behavior, and missing-SKU detail-only behavior.
- No changes to parser, validation, Manual Review eligibility, duplicate rules, payment status, platform pages, persistence, or existing Excel exports.

## 18. Verification

- Python compilation: `app.py` and `all_products.py` passed.
- Focused All Products and Streamlit state tests: `22 passed in 9.13s`.
- Full test suite: `114 passed in 26.22s` using a workspace-local pytest basetemp.

## 19. Cross Platform Summary From / To filters and detail date

- Replaced the single `Order Created Date Range` control with independent `From Date` and `To Date` inputs beside Platform. Both use inclusive canonical reporting dates and are independently optional.
- When both dates are empty, no date filtering occurs. From-only keeps rows on/after From Date; To-only keeps rows on/before To Date. If From Date is after To Date, the page shows a validation error and applies only the Platform filter rather than an invalid date filter.
- Added `Order Created Date` as the first Cross Platform Summary All Products display column. It is the reporting-layer canonical date, converted only for the dataframe as a true sortable date and displayed as `DD/MM/YYYY`.
- Existing canonical mapping remains unchanged: Shopee `order_created_date`, Lazada `order_date`, ZENXIN `invoice_date`. Rows without a reliable canonical date remain visible when neither date condition is active and are excluded only when a valid From or To condition is active; the concise excluded-row caption remains in place.
- Product Summary still has no single date column. Product Summary and All Products continue to use the same filtered reporting-row population. Existing five-column All Products Excel export, source fields, parser, validation, Manual Review eligibility, duplicate rules, and platform pages remain unchanged.
- Updated page-control preservation and Clear current batch reset suffixes for the new From/To widgets.

## 20. Verification

- Python compilation: `app.py`, `all_products.py`, and updated tests passed.
- Focused Cross Platform service and Streamlit tests: `24 passed in 10.56s`.
- Full test suite JUnit result: `116 passed`, `0 failures`, `0 errors` in `31.646s`, using a workspace-local pytest basetemp.

## 21. Cross Platform Summary empty date-filter result

- Fixed the Cross Platform Summary All Products empty-result path. The reporting-date column is now included in the display dataframe schema even when Platform / From Date / To Date filtering returns no eligible rows.
- Selecting a date range with no matching products now shows `No products match the current filters.` and a disabled Export All Products button instead of raising `KeyError: reporting_order_created_date`.
- No canonical-date mapping, source field, parser, product eligibility, Manual Review, summary aggregation, or Excel behavior changed.

## 22. Verification

- Python compilation: `app.py` and `test_app_batch_state.py` passed.
- Focused All Products and Streamlit tests: `25 passed in 11.43s`.
- Full test suite JUnit result: `117 passed`, `0 failures`, `0 errors` in `33.321s`, using a workspace-local pytest basetemp.

## 23. InvoiceGather Scope V2.1

- Replaced the project-local `invoicegather-scope` skill with the user-supplied V2.1 system consensus. Installed SHA256: `02435BE1F046911C74D773C8FB7E05699CD7A301847D56C1BA876AB06A41A82A`.
- Weekly Statement implementation follows the confirmed settlement-domain separation, whole-statement staging direction, and no-database/no-parser-rewrite guardrails.

## 24. Shopee Weekly Statement V1 ingestion and staging

- Added an independent Shopee Weekly Statement `.xlsx` parser and staging/reconciliation service; existing Shopee/Lazada/ZENXIN PDF parsers, `app.py`, batch flow, Cross Platform semantics, and Excel exports were not changed.
- Native Shopee exports are accepted directly. The reader first uses normal read-only workbook loading; when a required worksheet is reported as `A1`/empty or required headers are not visible, it scans the worksheet XML for genuinely populated cells, resets the read-only bounds, and streams only the real populated range. The source workbook is never rewritten or Save-As converted.
- Preserved Order View and SKU View as separate views of the same settlement. Order View is authoritative; SKU rows are retained for allocation validation and are not connected to Cross Platform Product Summary or guessed Seller SKUs.
- Added whole-statement staging metadata and outcomes: `Ready to Commit`, `Needs Review`, and `Rejected`. No production database or commit operation was added.
- Blocking validation covers required sheets/columns, valid Payout Completed Date period, Order View total to Summary, Order financial components to Total Released, SKU totals by Order, Service Fee Details, Adjustment details/control/footer, and conflicting authoritative Order View duplicates at RM0.02 tolerance.
- Added non-blocking reconciliation states `Matched`, `Different`, `Estimated Only`, and `Unmatched Order`; unmatched linked adjustments remain `Unmatched Adjustment`. No underpayment conclusion is produced.
- Exact content hash is a separate `Exact Duplicate` import gate and cannot proceed as a new future commit. Same period with different content becomes `Needs Review`; revised-statement supersession remains deferred.
- Added the original Shopee export as a regression fixture without modification. Source and fixture SHA256 are both `A65E37C11866FB3F42895473D716799B84DA34EEACE0DD099B1DAED9E52083F6`.

## 25. Weekly Statement V1 verification

- Original native export declared `Income` as `A1`, while the fallback recovered 49 populated columns and 1,328 rows without modifying the file.
- Sample result: 505 Order rows, 820 SKU rows, RM20,599.26 Total Released, 501 Service Fee detail rows, 14 Shipping Fee operational exceptions, and 3 Adjustment events totalling RM-126.63.
- All blocking validations passed. With no persisted/current Shopee Orders supplied to the isolated service, reconciliation produced 0 Matched, 0 Different, 0 Estimated Only, 505 Unmatched Order, and 3 Unmatched Adjustment; the statement remained `Ready to Commit` because these are non-blocking.
- Python compilation passed for the new parser, service, and tests.
- Focused Weekly Statement tests: `9 passed in 13.54s`.
- Full test suite: `126 passed in 36.21s` using a workspace-local pytest basetemp.
## 26. Data Import wizard presentation refactor

- Replaced the former upload-focused entry page with one `Data Import` wizard: Select Source → Upload → Validate → Reconcile → Review & Commit. The current step, step number, progress bar, and Completed / Current / Pending states remain visible throughout.
- Sidebar navigation is now limited to `ADMIN: Data Import` and `REPORTS: Dashboard, Cross Platform Summary, Shopee, Lazada, ZENXIN`. No Import Issues, reconciliation exceptions, history, settlement, or future database pages were added.
- One active batch is enforced in the UI. A current batch is presented with `Continue current batch` or `Discard current batch`; Platform Orders is inferred only for pre-wizard active batches so their existing data is not changed.
- Platform Orders still uses the existing PDF / ZIP uploader and `process_uploads` batch path. Existing Accepted, Manual Review, Duplicate Skipped, Unsupported, Processing Error, current-batch summary, reporting, and export paths are displayed through the wizard without changing parser, validation, duplicate, reconciliation, export, or database semantics.
- Shopee Weekly Statement now uses the existing `.xlsx` staging service in the UI. Validate displays Statement Period, Status, Order Rows, SKU Rows, Total Released, Adjustment Total, and the service's Passed / Warning / Blocking Failure outcome. Reconcile displays the existing Matched, Different, Estimated Only, Unmatched Orders, Unmatched Adjustments, and Shipping exceptions summaries plus up to five representative exceptions; it does not create reconciliation data.
- Added a disabled Validation recovery area and disabled Future Database Phase commit control. They make the future recovery / atomic-commit structure visible without performing a source, record, duplicate, or database action.
- Moved the new import presentation into `src/invoice_app/ui/data_import.py`; `app.py` retains authentication, sidebar/routing, and the existing processing callback.
- No parser, validation, reconciliation, duplicate, export, persistence, or reporting-data semantics were changed.

## 27. Verification

- Python compilation: `app.py`, `src/invoice_app/ui/data_import.py`, and `tests/test_app_batch_state.py` passed.
- Focused Streamlit lifecycle and wizard tests: `16 passed in 14.67s`.
- Full pytest suite JUnit result: `129 passed`, `0 failures`, `0 errors`, `0 skipped` in `38.614s`, using a workspace-local pytest basetemp.
## 28. Temporary Shopee Settlement Test Lab

- Added the removable `DEVELOPMENT / TESTING → Settlement Test Lab` route. The existing Shopee report/page remains unchanged; `app.py` only imports the temporary UI, exposes the isolated navigation entry, routes to it, and clears its session-only test statement together with Clear current batch.
- Added `settlement_test_lab.py` as an independent UI module. Platform Orders continue to come from the existing Data Import validation flow. The Lab separately accepts and validates a native Shopee `.xlsx` through the existing weekly-statement staging service, under its own session key, so validated Orders and a test Statement can coexist without creating a database record or applying a commit.
- Added reusable `settlement_reporting.py` for the reporting-level `(Shopee, Order ID)` projection. It preserves Invoice Payment Signal and all input source mappings, uses authoritative Weekly Statement Order View rows as released evidence, and de-duplicates both session order identities and statement Order IDs before counts are calculated.
- Matched statements derive `Effective Payment Status = Released`, `Settlement Status = Released`, and `Payment Evidence Source = Weekly Statement`. No-match rows retain the existing invoice payment signal with `Settlement Status = No Settlement Evidence`; the Lab never infers `Unpaid`.
- The Lab shows before/after comparison fields and session summary counts for matched/no-evidence, Pending → Released, Already Released → Released, amount-only Difference, and unmatched statement orders. Difference is only shown for Final Order Income and is not an underpayment decision.
- No parser, existing validation rule, reconciliation rule, duplicate rule, export, Shopee/Lazada/ZENXIN reporting page, persistence, database, RBAC, shipment, or underpayment behavior changed.

## 29. Settlement Test Lab verification

- Python compilation: `app.py`, `src/invoice_app/services/settlement_reporting.py`, `src/invoice_app/ui/settlement_test_lab.py`, and the new focused tests passed.
- Focused merge and Streamlit Lab tests: `6 passed in 3.58s`, covering Pending + match, Released + match, Pending + no match, duplicate non-double-counting, immutable invoice `payment_status`, Difference, and the separate temporary sidebar route/table.
- Full pytest suite JUnit result: `135 passed`, `0 failures`, `0 errors`, `0 skipped` in `39.481s`, using a workspace-local pytest basetemp.
## 30. Navigation and temporary test-session sync

- Replaced section-local sidebar radios with always-clickable native navigation buttons for Data Import, Dashboard, Cross Platform Summary, Shopee, Lazada, ZENXIN, and Settlement Test Lab. Navigation now changes the page independently of active-batch data, without clearing the batch or resetting the wizard.
- Added the scoped `TEMP_TEST_ONLY` action at Platform Orders validation: `Sync Accepted Orders to Test Session`. It copies only `status = Accepted` order mappings into the isolated `settlement_test_lab_accepted_orders` session key. Manual Review records remain in `reviews`, are not read by the sync, and are not changed.
- Settlement Test Lab now reads only that explicit temporary Accepted-order snapshot. It is marked TODO for removal when database-backed reporting replaces this temporary bridge; no production report, validation / Ready / Not Ready logic, parser, database, or commit semantics use it.

## 31. Navigation and temporary sync verification

- Python compilation passed for `app.py`, `data_import.py`, `settlement_test_lab.py`, and the affected AppTest modules.
- Focused Streamlit navigation / active-batch / temporary-sync suite: `19 passed in 19.19s`. It covers each Report page returning to Data Import and Settlement Test Lab, retained batch/wizard state, Accepted-only sync, and Manual Review exclusion.
- Full pytest was requested but could not start because the execution environment rejected the command for a temporary usage limit. This is an environment constraint, not a project test failure; rerun the full suite when execution capacity is available.

## 32. Shopee completeness regression: optional Ads Escrow Top Up Fee

- `Ads Escrow Top Up Fee` is no longer a required Income Details completeness field. When its source label is absent, the parsed/source value remains `N/A`; an explicitly printed amount remains unchanged.
- Existing financial reconciliation is unchanged: it continues to reconcile against the source `Fees & Charges` total, so an absent optional Ads Escrow component contributes zero without turning missing source data into `0.00`.
- Added all 12 supplied 26 Aug 2026 Shopee PDFs as real regression fixtures. The eight samples previously false-flagged solely for this field now parse as Accepted.

## 33. Shopee completeness regression verification

- Python compilation passed for the changed parser and regression tests.
- Focused Shopee layers, batch workflow, and real-PDF suite: `85 passed in 11.47s`.
- Full pytest suite: `150 passed in 42.00s`, using a workspace-local pytest basetemp.

## 34. Unified Import Result Contract V1

- Added typed, presentation-only `ImportResult`, `ValidationResult`, `ValidationIssue`, `ReconciliationResult`, commit-readiness, session-state, and summary models. Validation severity and blocking are independent fields; current-session application is explicitly separate from the future database commit state.
- Added adapters for the unchanged Platform Orders session payload and unchanged Shopee Weekly Statement staging result. Platform Orders is represented as `Not Applicable` for reconciliation; Weekly Statement preserves its raw staged statement, order/adjustment reconciliation, and shipping-exception details through `source_specific_details`.
- Data Import Validate, Reconcile, and Final Review now render the common contract. Existing PDF / ZIP and `.xlsx` upload paths, batch handling, Manual Review, duplicate / processing outcomes, temporary test-session sync, parser/service return shapes, reporting, export, and database behavior remain unchanged.
- Future database controls remain disabled. `Ready to Commit` is still a system-derived staging result only and does not write or mark data as committed.

## 35. Unified Import Result Contract verification

- Python compilation passed for `import_result_contract.py`, `import_result_adapters.py`, and `data_import.py`.
- Focused contract and Data Import AppTest suite: `22 passed in 26.60s`.
- Full regression suite was verified in bounded groups because the combined test runner truncated its completion response: `67 + 5 + 6 + 2 + 25 + 35 + 16 = 156 passed`, using workspace-local pytest basetemp directories.

## 36. Validation Recovery Contract V1 and workflow navigation safety

- Added typed `RecoveryAction` contract fields (`action_id`, action type, label, affected source, allowed, destructive, and revalidation requirement) alongside the existing unified import result contract.
- Added session-only recovery execution for the current batch. Manual Review/financial-product mismatch sources, Processing Error sources, Unsupported sources, and intra-batch Duplicate sources are surfaced per source with `View details` plus the approved removal action. Removal changes only current staging projections, re-applies existing batch rules, and never deletes or rewrites archived/original source files.
- `Remove Duplicate` removes only the identified duplicate staging source; it does not remove the retained Accepted source. Force Pass, Ignore Error, and arbitrary recovery action types are rejected and have no UI control.
- Weekly Statement validation/rejection/review outcomes now expose the same adapter-level View Details / Remove staged source metadata. Removal clears only the session staging object; parser and statement-service semantics remain unchanged.
- Replaced the disabled recovery placeholder with inline issue actions. Destructive recovery actions require an explicit native confirmation before removal and revalidation; View Details remains confirmation-free. Resolved sources are absent from the rebuilt result, so they no longer appear as open validation issues.
- Sidebar navigation is no longer coupled to the existence of an active batch. It is only temporarily blocked while `Processing`, `Validating`, or `Revalidating` is active, with an information dialog; completion clears the guard automatically. Data Import, reports, and Settlement Test Lab retain current session batch/wizard state when idle.
- Added destructive confirmation for Clear/Discard Current Batch. Settlement Test Lab now provides `← Back to Data Import`; it returns without clearing the batch or wizard state.
- No parser, validation formula, Manual Review rule, duplicate identity rule, settlement/reconciliation logic, database, RBAC, export, or production record behavior changed.

## 37. Validation Recovery and navigation safety verification

- Python compilation passed for `app.py`, recovery/navigation services, Data Import, Settlement Test Lab, and focused tests.
- Focused recovery/navigation contract and AppTest coverage: `14 passed in 18.08s`; existing Data Import lifecycle: `16 passed in 12.33s`; existing contract/navigation tests: `8 passed in 18.63s`; Weekly Statement regression: `9 passed in 12.66s`.
- Full regression suite: `67 + 21 + 60 + 16 = 164 passed`, using workspace-local pytest basetemp directories.

## 38. Settlement Test Lab validation progress

- Added native Streamlit processing feedback around the existing `Validate test statement` staging call: a status panel and phase-level progress bar show `Reading the native workbook` followed by `Validation and staging complete`.
- The indicator uses the existing synchronous statement-service boundary, so it does not invent row-level parser percentages or alter workbook parsing, validation, session staging, settlement, or database semantics.

## 39. Settlement Test Lab validation progress verification

- Python compilation passed for `settlement_test_lab.py` and its focused UI test.
- Focused Settlement Test Lab UI and Weekly Statement regression checks: `11 passed in 16.64s`.
- Full regression suite: `67 + 21 + 61 + 16 = 165 passed`, using workspace-local pytest basetemp directories.
## 40. Shopee reconciliation V1 verification

- Verified the existing Weekly Statement reconciliation service without changing production code. It continues to match only Shopee records by canonical `(Platform, Order ID)`, classifies Final-income comparisons within RM0.02 as `Matched`, greater differences as non-blocking `Different`, Estimated income as `Estimated Only`, and absent order records as non-blocking `Unmatched Order`.
- Compared the existing real fixtures: the native Statement has 505 unique Order IDs and the 12 real Shopee PDF fixtures have 12 distinct Order IDs, with zero overlap. The real statement therefore naturally covers `Unmatched Order`; the other classifications use its real Order View rows with minimal in-memory Invoice inputs.
- Added RM0.02 / RM0.03 boundary and `(Platform, Order ID)` identity regression checks. They verify that `Different` leaves a valid statement `Ready to Commit`, does not introduce an `Underpayment` status, and does not match a same-ID Lazada record.

## 41. Shopee reconciliation V1 verification results

- Python compilation passed for the changed focused test.
- Focused Weekly Statement and settlement-reporting checks: `16 passed in 16.91s`.
- Full regression suite: `67 + 21 + 61 + 18 = 167 passed`; the 21-test group was additionally verified from JUnit with `0 failures`, `0 errors`, and `0 skipped`.

## 42. UAT presentation layer

- Added the UAT-only HOME / DATA / REPORTS / HELP navigation: Daily Task, Import Data, Payment Check, Dashboard, Cross Platform Summary, Shopee, Lazada, ZENXIN, and How to Use. Settlement Test Lab and development/testing wording are no longer routed or shown to UAT users; their underlying module remains unchanged.
- Added Daily Task session context, temporary-session notice, direct workflow actions, statement period display, and Reset Test Session confirmation. Reset uses the existing current-session cleanup only.
- Added Payment Check as a read-only presentation over the existing Shopee settlement-reporting service. It shows the requested metrics and business-facing table/remarks without adding Underpayment, pricing, allocation, database, parser, validation, or reconciliation rules.
- Reworded Data Import as Choose Data / Upload Files / Check Issues / Review Results / Finish. Existing upload, validation, recovery confirmation, and statement staging behavior are retained; future database and temporary-test bridge wording are hidden from this UAT layer.

## 43. UAT presentation layer verification

- Python compilation passed for `app.py`, `src/invoice_app/ui/uat_presentation.py`, `src/invoice_app/ui/data_import.py`, and affected AppTest modules.
- Focused UAT navigation, Payment Check, session reset, and Import Data AppTest suite: `20 passed in 17.40s`, using workspace-local pytest basetemp.
- Full regression suite completed in confirmed groups: `72 + 6 + 9 + 19 + 61 = 167 passed`, using workspace-local pytest basetemp directories. This covers existing parser, validation, reconciliation, report/export, and Streamlit lifecycle tests.
## 44. UAT presentation rollback to Self-Test UI

- Removed the UAT-only presentation module and its Daily Task, Payment Check, How to Use, HOME / DATA / REPORTS / HELP navigation, UAT wizard wording, and UAT session-reset presentation.
- Restored the Self-Test interface: Data Import default entry; ADMIN / REPORTS / DEVELOPMENT / TESTING navigation; visible Settlement Test Lab; Current Batch / Discard Current Batch; and the Select Source → Upload → Validate → Reconcile → Review & Commit workflow.
- Restored the existing temporary Accepted-order sync into Settlement Test Lab and the disabled Future Database Commit presentation. Weekly Statement parsing/service, reconciliation, validation recovery, session contracts, reporting, and parser semantics were preserved.
- Rebuilt affected AppTest coverage around Self-Test navigation, accepted-only temporary sync, Settlement Test Lab availability, and restored legacy UI copy. Focused checks: 25 passed plus 6 Import Result contract tests; smoke checks: 76 passed; full regression: 164 passed in 72.31s.

## 45. Shopee promotion source metadata

- Preserved `source_line_subtotal` independently from the legacy derived `line_total` / `line_subtotal` behavior for Shopee product parser output.
- For the existing deterministic `Any N at RM...` positioned-parser path, added promotion group ID, source label, source group total, target quantity, and per-product member quantity metadata. Legacy allocation remains unchanged.
- When the existing section quantity check cannot verify membership, no group ID or member quantity is assigned; nearby source evidence is retained with `promotion_metadata_status = incomplete` and no allocation is applied.
- Shopee product mapping now passes this source metadata through without changing Cross Platform Summary, Product Master lookup, Weekly Statement, reconciliation, or existing product amount fields.

## 46. Shopee promotion source metadata verification

- Synthetic promotion metadata and existing Shopee parser regression tests: `35 passed in 0.36s`.
- Full regression suite: `180 passed in 77.71s`, using a workspace-local pytest basetemp.

## 47. Shopee Product Pricing Calculation Engine

- Added standalone `product_pricing` service that resolves Unit Selling Price exclusively through the existing Product Price Master and returns derived pricing results without changing parser rows or existing report fields.
- Normal rows calculate Normal Selling Value from Product Master price × quantity, Actual Selling Value from `source_line_subtotal`, and Discount Given as the difference.
- Complete same-price promotion groups allocate the source group total by member quantity with Decimal rounding and a deterministic final-member remainder, preserving exact group-total reconciliation.
- Missing price, pricing conflict, incomplete promotion evidence, unresolved group-member price, and mixed-price promotion members return explicit unavailable/unsupported statuses without using zero or guessing. Negative discount below -RM0.02 is retained as a Pricing Anomaly.
- Cross Platform Summary, Product Summary, Shopee parser behavior, Weekly Statement, and reconciliation remain unchanged.

## 48. Product Pricing Calculation Engine verification

- Focused Product Pricing, Product Master, and promotion metadata tests: `26 passed in 0.91s`.
- Full regression suite: `190 passed in 76.74s`, using a workspace-local pytest basetemp.

## 49. Promotion Pricing Phase 3 — Cross Platform Summary integration

- Cross Platform Product Summary now consumes the existing Shopee Product Price Master and Product Pricing Engine without changing parser, Weekly Statement, reconciliation, payment, or All Products detail contracts.
- Summary pricing identity is Seller SKU + normalized Product Name + available Variation. Variation is shown in the existing Product Name display label, so same SKU entries with different name/variation and resolved prices remain separate summary rows.
- The six summary fields are Seller SKU, Product Name, Unit Selling Price, Total Quantity, Total Selling Price, and Total Discount Given. Product Master lookup remains generic: exact SKU first, then normalized Product Name and available Variation only when multi-price candidates need disambiguation.
- Shopee pricing runs per Order ID before aggregation, preserving promotion-group boundaries. Lazada uses `paid_price` and ZENXIN uses `line_total_inc_tax` as their actual selling values; no voucher, fee, rebate, parser, or payment adjustment is deducted again.
- Price Not Found / Pricing Conflict retain reliable actual selling amounts and quantity while Unit Selling Price and Discount Given remain `N/A`. Complete promotion groups with an unavailable member price retain only their source-supported allocated actual selling values; incomplete promotion metadata remains unavailable and is surfaced as `N/A`.

## 50. Promotion Pricing Phase 3 verification

- Local Product Master validation (read-only, not tracked): all 7 confirmed multi-price Seller SKUs and all 14 candidate records resolved uniquely through generic Seller SKU + Product Name + available Variation matching; no SKU or price was hardcoded.
- Focused Summary, Product Master, Product Pricing, All Products, and Streamlit AppTest coverage: `54 passed in 16.39s`; additional promotion source-actual safety coverage: `17 passed in 0.96s`.
- Full regression suite: `197 passed in 80.81s`, using a workspace-local pytest basetemp.

## 51. Shopee promotion container source facts and pricing safety

- Replaced target-quantity-based Shopee promotion grouping with a coordinate-aware container rule: the promotion label starts a candidate region, SKU-anchored member blocks continue until the next independent normal subtotal, promotion label, section, or page boundary.
- Added distinct source fields for label evidence (`promotion_advertised_amount` or `promotion_discount_percent`) and the coordinate-confirmed Subtotal-column `source_group_total`; matching numeric values are never treated as the same fact.
- Promotion members no longer fabricate individual line subtotals. Complete groups validate against their source group total at group level; incomplete layout evidence remains unallocated.
- Added `Any N enjoy P% off` label support. The percentage is retained as metadata only and is never used to derive the source group total.
- Shopee Product Master resolution now combines exact Seller SKU and Parent SKU candidates before unique-price/name/variation resolution. Lazada and ZENXIN stay source-only and do not query the Shopee Master.

## 52. Promotion container verification

- Real `260818N824EBFU` read-only verification: the two Popcorn rows form one group with `source_group_total = RM20.00`, `promotion_advertised_amount = RM20.00`, `participating_qty = 2`, missing individual source subtotals, and no Manual Review; the numbers remain separately sourced.
- Focused synthetic and real parser/pricing/lookup regression: `53 passed in 18.00s`.
- Full regression suite: `196 passed in 56.68s`, using a workspace-local pytest basetemp.


## 53. Shopee promotion subtotal on later member row

- Extended the coordinate-based container extractor to accept exactly one Subtotal-column amount positioned on any SKU-anchored member row inside the candidate region, not only the label's first product block.
- The amount remains a source_group_total; it is never assigned as an individual member subtotal. Multiple or absent candidate Subtotal amounts remain incomplete rather than guessed.
- Read-only supplied-PDF verification (discounted pdf.pdf): the Any 4 at RM15.00 group now has four members, one source group total of RM15.00, missing individual source subtotals, and is accepted as 1 order / 7 products / 0 Manual Review.

## 54. Later-member promotion subtotal verification

- Focused Shopee promotion parser, real-sample, and validation regression: 56 passed in 16.28s.
- Full regression suite: 197 passed in 81.78s, using a workspace-local pytest basetemp.


## 55. Promotion parser and source validation contract

- Product Count now uses reliable Seller SKU + Quantity anchors. Product Name and individual line subtotals do not suppress an extracted promotion member.
- Positioned and text fallback paths preserve Any N at RM and Any N enjoy P% off labels through the same promotion-container validation path.
- Normal source line subtotals plus each complete promotion source group total reconcile once against Merchandise Subtotal. Existing pricing allocation and Refund reconciliation are unchanged.
- When a promotion container, member set, or source group total cannot be coordinate-confirmed, Manual Review now reports INCOMPLETE_PROMOTION_EVIDENCE rather than treating members as ordinary line arithmetic.

## 56. Promotion parser and source validation verification

- Focused parser, source-validation, pricing, and real-fixture regression: 47 passed in 1.36s.
- Local supplied review samples: 13 of 19 accepted; two Product Count cases retain genuinely missing anchors, two remain Income Completion Anchor Missing, one is explicit incomplete promotion evidence, and the existing Refund-related amount discrepancy remains unchanged.
- Full regression suite: 197 passed in 69.31s, using a workspace-local pytest basetemp.


## 57. Shopee promotion group-total evidence resolver

- Replaced nearest-row promotion subtotal selection with a three-step source contract: parser collects every Subtotal-column candidate owned by a promotion container, source validation rejects only true strikethrough candidates using horizontal-rule overlap through the amount middle band, then order-level reconciliation certifies exactly one candidate combination.
- Promotion labels, original/struck prices, Merchandise Subtotal, vouchers, shipping, fees, and amounts outside the product-container boundary are not candidate totals. Numeric equality with an `Any N at RM...` label is not itself an exclusion.
- Supports both `Any N at RM...` and `% off` labels through one container pipeline. Target quantity remains metadata and never gates participating quantity or Product Count.
- Ambiguous, missing, or non-certifiable candidates now produce `INCOMPLETE_PROMOTION_EVIDENCE`; complete groups retain one `source_group_total` for validation. Pricing allocation, Product Master, Refund reconciliation, UI, and main remain unchanged.

## 58. Promotion evidence resolver verification

- Focused synthetic promotion, source-validation, and real-fixture regression: `60 passed in 15.23s`.
- Supplied Manual Review ZIP re-audit: all 26 formerly promotion-related Product Amount failures now resolve to their source group totals and are accepted; promotion-related Product Amount failures are zero. Seven genuinely ambiguous/incomplete promotion cases remain `INCOMPLETE_PROMOTION_EVIDENCE`; the two Product Count mismatches remain; 37 income-incomplete documents remain outside this change.
- `2608259RPJPYNG` remains the original Refund-related Product Amount discrepancy and was not changed.
- Full regression suite: `201 passed in 88.07s`, using a workspace-local pytest basetemp.


## 59. Later-member promotion subtotal collection

- Corrected the evidence collector to scan every SKU-anchored member block for Subtotal-column candidates, not only the block that contains the promotion label. A valid group total may be physically aligned on a later member SKU row.
- Container ownership, strict strikethrough rejection, and order-level unique-combination certification remain unchanged. This is a source-evidence parser correction, not an inference from the promotion label.
- Existing pricing behavior already allocates a certified group total by member quantity only when every member has the same resolved Product Master Unit Selling Price; mixed-price groups remain unsupported and do not receive guessed SKU actuals.

## 60. Later-member collection verification

- Added a parser-level regression for an unlabelled later member carrying RM15.00 in the Subtotal column, plus promotion-container and real-fixture regression: `32 passed in 15.28s`.
- Supplied Manual Review ZIP re-audit: 30 Accepted, 3 `INCOMPLETE_PROMOTION_EVIDENCE`, 2 Product Count mismatches, 37 Income Completion Anchor Missing, and the unchanged Refund discrepancy. Promotion-related Product Amount failures remain zero.
- The corrected direct-evidence samples are `2608210MU8Y8CH`, `260821W03YK44W`, `260826D5BNTGNU`, and `260826D6JQ2Q8E`; their PDF group totals are RM15.00, RM15.00, RM15.00, and RM44.00 respectively.
- Full regression suite: `202 passed in 86.65s`, using a workspace-local pytest basetemp.

## 61. Product Master lookup normalization and identity safety

- SKU identifiers are now normalized as text at Product Master load, pricing input, internal product rows, aggregation, and Excel export. Existing text and leading zeros are preserved; numeric values are rendered without decimal/scientific notation but missing leading zeros are not invented.
- Shopee parser and mapper now keep `Variation:` content in an independent Variation field instead of appending it to Product Name. Lookup comparison uses only deterministic trim, casefold, and whitespace-insensitive exact matching.
- Master lookup combines exact Seller SKU and Parent SKU candidates, but resolves only one Master identity. Multiple identities remain `PRICING_CONFLICT` even when their prices happen to be equal. Blank, `N/A`, and `exp` SKUs may resolve only through one exact Product Name plus available Variation identity.
- Added the narrow auditable `-Less` alias: it runs only after the original SKU has no candidate, uses `REMOVE_SUFFIX_LESS`, and does not authorize any other suffix or fuzzy matching.
- The read-only Product Master Quality Report now shares runtime lookup statuses, reports alias/name-variation outcomes separately, preserves the full candidate pool, and flags `SOURCE_TEXT_CONTAMINATION` without altering source Product Name text.

## 62. Product Master lookup verification

- Final focused lookup, quality-report, pricing, aggregation, parser, and export regression: `75 passed in 15.85s`.
- Local Product Listing workbook was not present in the workspace, adjacent project materials, or supplied download materials, so the requested new 5,793-row real-master count was not generated. No substitute master or inferred pricing was used.
- Full regression suite: `212 passed in 62.25s`, using a workspace-local pytest basetemp.
