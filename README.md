# Invoice Analytics System V1

A Streamlit-based invoice analytics workflow for mixed Shopee, Lazada and ZENXIN PDF invoices.

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
