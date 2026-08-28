# System Requirements – Invoice Analytics Web Application V1

## 1. System Objective

Develop a simple and user-friendly **web-based Invoice Analytics System** for processing invoices from:

- Shopee
- Lazada
- ZENXIN

The system will use **Python-based rule processing** to read recurring PDF invoice patterns.

AI is not required for the main extraction process.

The system should allow users to:

**Upload invoices → Automatically analyse them → View Dashboard → Select required data → Export to Excel**

---

# 2. User Login

The system will have a simple login page.

Users must enter:

- Username
- Password

before accessing the system.

For V1:

- No complicated user-role management is required.
- Username and password authentication is sufficient.
- Passwords must not be stored as plain text.

---

# 3. Invoice Upload

Users must be able to upload:

- One PDF
- Multiple PDFs
- Large batches of PDFs

in a single session.

Example:

```text
Shopee_Order01.pdf
Shopee_Order02.pdf
Lazada_Invoice01.pdf
ZENXIN_Invoice01.pdf
Lazada_Invoice02.pdf
...
```

There should be **no requirement for users to separate the PDFs by platform before uploading**.

The system will automatically identify them.

---

# 4. Platform Detection

Every uploaded PDF must first be analysed to determine whether it belongs to:

- Shopee
- Lazada
- ZENXIN

The system will then send the PDF to the correct dedicated parser.

```text
PDF
 ↓
Platform Detection
 ↓
 ├── Shopee → Shopee Parser
 ├── Lazada → Lazada Parser
 └── ZENXIN → ZENXIN Parser
```

If the platform cannot be identified:

**Manual Review Required**

The system must not guess.

---

# 5. Dedicated PDF Parsers

Each platform must have its own independent Python parser.

```text
shopee_parser.py
lazada_parser.py
zenxin_parser.py
```

Each parser will be designed according to the recurring pattern of that platform's invoice.

This allows future maintenance to remain simple.

For example:

If Lazada changes its invoice format, only the Lazada parser should need to be updated.

Shopee and ZENXIN processing should remain unaffected.

---

# 6. Data to Extract

The system should collect as much reliable structured information as possible from the invoices.

Recommended main fields:

- Platform
- Order ID
- Invoice Number
- Invoice Date
- Product Name
- Seller SKU
- Quantity
- Unit Price
- Line Total
- Delivery Fee
- Source PDF
- Processing Status
- Remarks

The system should **collect all available data first**.

The user will decide later which information they want to view or export.

---

# 7. Order ID and Duplicate Control

**Order ID will be the main duplicate identifier.**

Each Order ID should only appear from **one PDF** in the same processing batch.

Example:

```text
Order ID: 123456
PDF A → accepted
PDF B → same Order ID detected
```

PDF B should be flagged as:

**Duplicate Order ID**

and should not be processed into the final dataset again.

This prevents accidentally uploading the same order more than once.

---

# 8. Product Structure

Each product/SKU should normally appear as one data row.

Example:

```text
Order 10001
 ├── Product A
 ├── Product B
 └── Product C
```

becomes:

| Order ID | Product | SKU | Qty |
|---|---|---|---:|
| 10001 | Product A | SKU01 | 1 |
| 10001 | Product B | SKU02 | 2 |
| 10001 | Product C | SKU03 | 1 |

All rows remain connected using the same Order ID.

---

# 9. Duplicate Product Rule

If the same SKU appears several times within the **same Order ID**, its quantity can be combined.

Example:

```text
Order 10001

SKU01 → Qty 1
SKU01 → Qty 2
```

Result:

```text
SKU01 → Qty 3
```

However, the same SKU appearing in different Order IDs must remain separate.

---

# 10. Product Name

Use the exact Product Name available in the invoice.

The system should not rewrite or guess product names.

Product Name should remain linked to:

**Order ID + SKU + Quantity**

If the relationship cannot be determined reliably:

**Manual Review Required**

---

# 11. Quantity

Quantity must:

- Be numeric
- Be an integer
- Be greater than zero

Invalid or uncertain quantity values should be flagged for manual review instead of being guessed.

---

# 12. Price Logic

Store both where possible:

### Unit Price

Price for one unit/product.

### Line Total

Actual total value for that product line.

Recommended calculation:

```text
Line Total = Unit Price × Quantity
```

Where the invoice provides an actual transaction value that differs because of discounts, the actual transaction/selling value should be preferred.

The Dashboard should primarily use **Line Total** when calculating sales totals.

---

# 13. Delivery Fee

Delivery Fee belongs to an **Order**, not automatically to every product row.

Example:

```text
Order 10001
Delivery Fee = RM3.81

Product A
Product B
Product C
```

The system must not calculate:

```text
Product A → RM3.81
Product B → RM3.81
Product C → RM3.81
```

Recommended representation:

```text
Product A → RM3.81
Product B → RM0.00
Product C → RM0.00
```

Therefore:

```text
Total Delivery Fee = RM3.81
```

and not RM11.43.

---

# 14. Invoice Processing

The processing flow should be:

```text
Upload PDFs
      ↓
Validate PDFs
      ↓
Detect Platform
      ↓
Detect Order ID
      ↓
Check Duplicate Order ID
      ↓
Run Correct Parser
      ↓
Extract Data
      ↓
Validate Data
      ↓
Normalize Data
      ↓
Generate Current Batch Dataset
      ↓
Dashboard
```

---

# 15. Large Batch Processing

The application should support large invoice batches.

The user should be able to upload many invoice PDFs in one operation.

The system should:

- Process files individually
- Display processing progress
- Continue processing even if one PDF fails
- Avoid loading unnecessary PDF content into memory
- Show successful and failed invoice counts

Example:

```text
Processing invoices...

145 / 277 completed

Current:
Lazada_Order_123.pdf
```

---

# 16. Raw PDF Retention

Uploaded source PDFs should be retained/archive according to the system's storage structure.

This is recommended so that extracted data can later be traced back to the original invoice if required.

Each extracted record should retain:

**Source PDF filename**

for traceability.

---

# 17. Error Handling

One problematic PDF must **not stop the whole batch**.

Example:

```text
100 PDFs uploaded

97 successfully processed
3 require manual review
```

Possible errors include:

- Unknown platform
- Duplicate Order ID
- Missing SKU
- Quantity not detected
- Product mapping unclear
- Unexpected invoice format
- Corrupted PDF

These invoices should be listed separately for user review.

---

# 18. No Manual Data Editing in V1

Users will **not be allowed to manually edit extracted records inside the system in Version 1**.

The system is initially focused on:

- Upload
- Processing
- Analysis
- Viewing
- Filtering
- Export

Manual correction functionality can be considered in a later version.

---

# 19. Dashboard

After processing finishes, users should automatically see the **current batch Dashboard**.

Example:

```text
Invoice Dashboard

PDFs Processed        277
Orders                277
Product Records       645
Total Quantity        1,250

Total Sales           RM 25,420.50
Delivery Fees         RM 780.20

Shopee                150
Lazada                 85
ZENXIN                  42

Manual Review           3
```

---

# 20. Dashboard Scope

Version 1 will display **only the current uploaded batch**.

There is currently:

- No historical Dashboard
- No historical transaction search
- No yearly Master Dataset
- No historical Excel database requirement

Each new upload session is treated as a new processing batch.

Historical functionality can be added later if required.

---

# 21. Dashboard Filters

Users should be able to filter the current data by:

- Platform
- Order ID
- Invoice Date
- Product Name
- SKU
- Processing Status

Example:

```text
Platform
[ All ▼ ]

Product / SKU
[ Search ]

Status
[ All ▼ ]
```

---

# 22. User-Selectable Columns

Users should decide which information they want to see.

Example:

```text
Select Columns

☑ Platform
☑ Order ID
☑ Product Name
☑ Seller SKU
☑ Quantity
☐ Unit Price
☐ Line Total
☐ Delivery Fee
☐ Invoice Date
☐ Source PDF
```

The Dashboard table should update based on the user's selection.

---

# 23. Excel Export

Every Export action must generate a **new Excel file**.

There is no permanent Master Excel file in V1.

Example filename:

```text
Invoice_Report_2026-08-18_1030.xlsx
```

The Excel should contain:

- Current filtered records
- User-selected columns

For example, if the user selects:

```text
Product Name
SKU
Quantity
Platform
```

the exported Excel should contain only:

| Product Name | SKU | Quantity | Platform |
|---|---|---:|---|

---

# 24. Full Data Export

Also provide a separate option:

**Export Full Dataset**

This exports all successfully extracted fields from the current batch.

Recommended buttons:

```text
[ Export Selected Data ]

[ Export Full Dataset ]
```

Every click generates a new Excel file.

Existing Excel files should not be overwritten automatically.

---

# 25. Excel Formatting

Generated Excel files should be user-friendly.

Requirements:

- Header formatting
- Auto-sized columns
- Excel filters
- Frozen header row
- Currency formatting
- Quantity formatting
- SKU stored as text
- Clear file name
- Processing date/time included

---

# 26. No Historical Database in V1

Version 1 will **not maintain historical structured invoice data**.

Therefore the application does not initially need:

- SQL database
- SQLite database
- Master historical Excel database
- Historical Dashboard

The active dataset can be processed using:

**Pandas DataFrame**

during the user's current session.

Excel is used as the final output/report format.

This keeps Version 1 simple and maintainable.

---

# 27. Recommended Technical Architecture

```text
                    Web Application
                          │
                    Username/Login
                          ↓
                   Upload Multiple PDFs
                          ↓
                 Python PDF Reader
                          ↓
                 Platform Detector
                          ↓
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
     Shopee            Lazada            ZENXIN
      Parser             Parser             Parser
        └─────────────────┼─────────────────┘
                          ↓
                    Validation
                          ↓
                     DataFrame
                          ↓
                     Dashboard
                    ↙     ↓      ↘
                Filters  Analytics Columns
                          ↓
                     Excel Export
```

---

# 28. Recommended Technology

### Web Interface

**Streamlit**

### Processing

**Python**

### PDF Reading

**PyMuPDF / pdfplumber**

### Data Processing

**Pandas**

### Excel Generation

**OpenPyXL**

### Authentication

Simple username/password authentication.

### Storage

Current batch only.

Raw PDFs may be archived separately.

No structured historical database for Version 1.

---

# 29. Maintainability Requirement

Business logic must remain separate from the Web UI.

Recommended structure:

```text
invoice_system/
│
├── app.py
│
├── auth.py
│
├── config.py
│
├── models.py
│
├── parsers/
│   ├── shopee.py
│   ├── lazada.py
│   └── zenxin.py
│
├── services/
│   ├── pdf_reader.py
│   ├── platform_detector.py
│   ├── duplicate_checker.py
│   ├── validator.py
│   ├── analytics.py
│   └── excel_exporter.py
│
├── archive/
│
└── tests/
```

The Web UI should never contain platform-specific PDF extraction rules.

---

# 30. Main User Experience

The final system should require only:

**1. Login**

↓

**2. Upload all invoice PDFs**

↓

**3. Click Process**

↓

**4. View Dashboard**

↓

**5. Filter / Select Required Data**

↓

**6. Export Excel**

No technical knowledge should be required.

---

# Version 1 Final Scope

### Included

- Username/password login
- Multiple PDF upload
- Large batch processing
- Automatic platform identification
- Shopee dedicated parser
- Lazada dedicated parser
- ZENXIN dedicated parser
- Order ID duplicate checking
- Product/SKU/Qty extraction
- Price and Delivery Fee extraction
- Validation
- Manual Review flagging
- Current batch Dashboard
- Data filters
- User-selectable columns
- New Excel generated for every export
- Full dataset export
- Source PDF archive
- Processing progress

### Not Included in V1

- Historical Dashboard
- Historical structured database
- Master Excel database
- User editing of extracted records
- AI extraction
- Google Sheets integration
- Complex user roles
- Historical invoice search