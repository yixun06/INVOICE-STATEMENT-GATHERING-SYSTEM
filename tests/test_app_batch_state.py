import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.shopee_weekly_statement_service import (
    AdjustmentReconciliation,
    OrderReconciliation,
    READY_TO_COMMIT,
    StagedShopeeWeeklyStatement,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.services.batch_service import (
    FIELD_LABELS,
    PLATFORM_ORDER_FIELDS,
)
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
)
from src.invoice_app.ui import data_import


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def navigate(app: AppTest, page: str) -> AppTest:
    next(button for button in app.button if button.label == page).click().run(timeout=20)
    return app


def test_clear_current_batch_resets_state_before_a_fresh_batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [{"platform": "Lazada", "order_id": "OLD-1"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = [{"order_id": "OLD-REVIEW"}]
    app.session_state["batch_id"] = "old-batch"
    app.session_state["pdf_count"] = 3
    app.session_state["uploader_version"] = 7
    app.session_state["All_order_filter"] = "OLD"
    app.session_state["All_product_filter"] = "OLD"
    app.session_state["All_platform_filter"] = "Shopee"
    app.session_state["Shopee_optional_order_columns"] = ["source_pdf"]
    app.session_state["Lazada_optional_product_columns"] = ["seller_sku"]
    app.session_state["All_export_success"] = True
    app.session_state["upload_notice"] = ("success", "old message")
    app.session_state["upload_result_summary"] = {"pdfs_processed": 3}
    app.session_state["duplicate_skipped"] = [{"order_id": "DUP-1"}]
    app.session_state["unsupported_files"] = [{"filename": "notes.pdf"}]
    app.session_state["processing_errors"] = [{"filename": "broken.pdf"}]
    app.run(timeout=20)

    app.session_state["pending_batch_discard_confirmation"] = True
    app.run(timeout=20)
    next(button for button in app.button if button.key == "confirm_discard_current_batch").click().run(timeout=20)
    cleared = app.session_state.filtered_state
    assert app.exception == []
    assert cleared["authenticated"] is True
    assert cleared["uploader_version"] == 8
    assert "pdf_uploader_8" not in cleared
    for key in (
        "orders",
        "products",
        "reviews",
        "batch_id",
        "pdf_count",
        "upload_notice",
        "upload_result_summary",
        "duplicate_skipped",
        "unsupported_files",
        "processing_errors",
    ):
        assert key not in cleared
    assert not any(key.startswith("pdf_uploader_") and key != "pdf_uploader_8" for key in cleared)
    assert not any(
        key.endswith(
            (
                "_order_filter",
                "_product_filter",
                "_platform_filter",
                "_optional_order_columns",
                "_optional_product_columns",
                "_export_success",
            )
        )
        for key in cleared
    )

    app.session_state["orders"] = [{"platform": "ZENXIN", "order_id": "NEW-1"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = "fresh-batch"
    app.session_state["pdf_count"] = 1
    app.run(timeout=20)

    fresh = app.session_state.filtered_state
    assert app.exception == []
    assert fresh["batch_id"] == "fresh-batch"
    assert fresh["orders"] == [{"platform": "ZENXIN", "order_id": "NEW-1"}]
    assert fresh["pdf_count"] == 1


def test_processed_pdf_count_remains_cumulative_for_every_archived_pdf():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    process_uploads = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "process_uploads"
    )
    pdf_count_assignment = next(
        node
        for node in ast.walk(process_uploads)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) and target.attr == "pdf_count" for target in node.targets)
    )

    value = pdf_count_assignment.value
    assert isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
    assert isinstance(value.left, ast.Name) and value.left.id == "existing_pdf_count"
    assert isinstance(value.right, ast.Call)
    assert isinstance(value.right.func, ast.Name) and value.right.func.id == "len"
    assert isinstance(value.right.args[0], ast.Name) and value.right.args[0].id == "archived_pdfs"


def test_process_uploads_appends_each_pdf_before_processing_the_next_one():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    process_uploads = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "process_uploads"
    )
    pdf_loop = next(
        node
        for node in ast.walk(process_uploads)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "enumerate"
    )
    called_functions = {
        node.func.id
        for node in ast.walk(pdf_loop)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "process_pdf_file_with_outcome" in called_functions
    assert "append_batch_results_with_metadata" in called_functions


def test_platform_tabs_derive_manual_reviews_without_exposing_internal_payloads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["reviews"] = [
        {
            "batch_id": "batch-review",
            "platform": " shopee ",
            "order_id": "SHP-R",
            "source_pdf": "shopee.pdf",
            "status": "Manual Review",
            "reason": "Income Completion Anchor Missing",
            "timestamp": "2026-08-21T10:00:00+00:00",
            "order_payload": {"delivery_fee": "4.90", "payment_status": "Released"},
            "product_payloads": [
                {
                    "platform": "Shopee",
                    "order_id": "SHP-R",
                    "product_name": "Review product",
                    "unit_price": "10.00",
                    "seller_sku": "SKU-R",
                    "quantity": 1,
                }
            ],
        },
        {
            "batch_id": "batch-review",
            "platform": "Lazada",
            "order_id": "LZD-R",
            "source_pdf": "lazada.pdf",
            "status": "Manual Review",
            "reason": "Product Count Mismatch",
            "timestamp": "2026-08-21T10:01:00+00:00",
        },
        {
            "batch_id": "batch-review",
            "platform": "ZENXIN",
            "order_id": "ZNX-R",
            "source_pdf": "zenxin.pdf",
            "status": "Manual Review",
            "reason": "Product Count Mismatch",
            "timestamp": "2026-08-21T10:02:00+00:00",
        },
    ]
    app.session_state["orders"] = []
    app.session_state["products"] = []
    app.session_state["batch_id"] = "batch-review"
    app.session_state["pdf_count"] = 3
    app.session_state["navigation"] = "Shopee"

    app.run(timeout=20)

    for platform_name in ("Shopee", "Lazada", "ZENXIN"):
        if platform_name != "Shopee":
            navigate(app, platform_name)
        assert app.exception == []
        if platform_name == "Shopee":
            assert "Manual Review" not in {element.value for element in app.subheader}
        else:
            assert "Manual Review" in {element.value for element in app.subheader}
        assert ("Manual Review", "1") in {
            (metric.label, metric.value) for metric in app.metric
        }
        assert app.expander == []
        for dataframe in app.dataframe:
            assert "order_payload" not in dataframe.value.columns
            assert "product_payloads" not in dataframe.value.columns
        if platform_name == "Shopee":
            assert app.dataframe == []
        else:
            assert all("Payment Status" not in dataframe.value.columns for dataframe in app.dataframe)



def test_cross_platform_navigation_uses_the_read_only_committed_report():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    cross_platform_branch = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "selected_page"
        and any(
            isinstance(comparator, ast.Constant)
            and comparator.value == "Cross Platform Summary"
            for comparator in node.test.comparators
        )
    )
    calls = {
        call.func.id
        for call in ast.walk(cross_platform_branch)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }

    assert "render_cross_platform_product_summary" in calls
    assert "show_all_tab" not in calls


def test_platform_tabs_hide_manual_review_section_when_that_platform_has_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = []
    app.session_state["products"] = []
    app.session_state["reviews"] = [
        {
            "batch_id": "batch-shopee-only",
            "platform": "Shopee",
            "order_id": "SHP-R",
            "source_pdf": "shopee.pdf",
            "status": "Manual Review",
            "reason": "Income Completion Anchor Missing",
            "timestamp": "2026-08-21T10:00:00+00:00",
        }
    ]
    app.session_state["batch_id"] = "batch-shopee-only"
    app.session_state["pdf_count"] = 1
    app.session_state["navigation"] = "Shopee"

    app.run(timeout=20)

    assert app.exception == []
    assert "Manual Review" not in {element.value for element in app.subheader}
    assert ("Manual Review", "1") in {
        (metric.label, metric.value) for metric in app.metric
    }

    for platform_name in ("Lazada", "ZENXIN"):
        navigate(app, platform_name)
        assert app.exception == []
        assert "Manual Review" not in {element.value for element in app.subheader}
        assert ("Manual Review", "0") in {
            (metric.label, metric.value) for metric in app.metric
        }


def test_lazada_preview_dates_are_typed_for_chronological_sorting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {
            "platform": "Lazada",
            "order_id": "LZD-DATE-1",
            "order_date": "28 02 2026",
            "invoice_date": "27 02 2026",
        },
        {
            "platform": "Lazada",
            "order_id": "LZD-DATE-2",
            "order_date": "01 03 2026",
            "invoice_date": "02 03 2026",
        },
    ]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = "batch-lazada-dates"
    app.session_state["pdf_count"] = 2
    app.session_state["navigation"] = "Lazada"

    app.run(timeout=20)

    assert app.exception == []
    order_table = next(
        dataframe.value
        for dataframe in app.dataframe
        if {"Order Date", "Invoice Date"} <= set(dataframe.value.columns)
    )
    assert order_table["Order Date"].dtype.kind == "M"
    assert order_table["Invoice Date"].dtype.kind == "M"

def test_zenxin_preview_invoice_date_is_typed_without_lazada_format_coercion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {
            "platform": "ZENXIN",
            "order_id": "10123",
            "invoice_date": "31/03/2026",
        }
    ]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = "batch-zenxin-date"
    app.session_state["pdf_count"] = 1
    app.session_state["navigation"] = "ZENXIN"

    app.run(timeout=20)

    assert app.exception == []
    order_table = next(
        dataframe.value
        for dataframe in app.dataframe
        if "Invoice Date" in dataframe.value.columns
    )
    assert order_table["Invoice Date"].dtype.kind == "M"
    assert order_table["Invoice Date"].iloc[0].strftime("%d/%m/%Y") == "31/03/2026"

def test_data_import_validate_restores_current_batch_dashboard_and_filterable_order_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {
            "platform": "Shopee",
            "order_id": "SHP-1",
            "payment_status": "Pending",
            "order_income": "10.00",
            "final_amount": "8.00",
        },
        {"platform": "Lazada", "order_id": "LZD-1", "order_income": "5.50", "final_amount": "N/A"},
        {"platform": "ZENXIN", "order_id": "ZNX-1", "order_income": "N/A", "final_amount": "2.00"},
    ]
    app.session_state["products"] = [
        {
            "platform": "Shopee",
            "order_id": "SHP-1",
            "product_name": "Shopee product",
            "seller_sku": "SHP-SKU",
            "quantity": 2,
        },
        {
            "platform": "Lazada",
            "order_id": "LZD-1",
            "product_name": "Lazada product",
            "seller_sku": "LZD-SKU",
            "quantity": 3,
        },
    ]
    app.session_state["reviews"] = [
        {"platform": "Shopee", "order_id": "SHP-REVIEW", "status": "Manual Review"},
        {"platform": "Shopee", "order_id": "SHP-DUP", "status": "Duplicate Skipped"},
    ]
    app.session_state["batch_id"] = "batch-default-columns"
    app.session_state["pdf_count"] = 3
    app.session_state["upload_result_summary"] = {"pdfs_processed": 3}
    app.session_state["navigation"] = "Data Import"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3

    app.run(timeout=20)
    assert app.exception == []
    current_order_table = next(
        dataframe.value
        for dataframe in app.dataframe
        if "Order ID" in dataframe.value.columns
        and dataframe.value["Order ID"].tolist() == ["SHP-1", "LZD-1", "ZNX-1"]
    )
    assert current_order_table["Order ID"].tolist() == ["SHP-1", "LZD-1", "ZNX-1"]
    assert {"Payment Status", "Refund Amount"} <= set(current_order_table.columns)
    assert app.session_state.filtered_state["data_import_current_batch_optional_order_columns"] == [
        "order_created_date",
        "order_status",
        "order_income",
        "final_amount",
        "source_pdf",
    ]
    order_columns_picker = next(
        element for element in app.multiselect if element.label == "Order columns"
    )
    assert "Merchandise Subtotal" in order_columns_picker.options
    assert {"Current batch summary", "Search and Filters"} <= {
        element.value for element in app.subheader
    }
    assert {
        ("Orders", "3"),
        ("Products", "2"),
        ("Quantity", "5"),
        ("Order Income", "RM 15.50"),
        ("Final Amount", "RM 10.00"),
        ("Manual Review", "1"),
    } <= {(metric.label, metric.value) for metric in app.metric}

    order_columns_picker.set_value(
        [
            "Order Created Date",
            "Order Status",
            "Order Income",
            "Final Amount",
            "Source PDF",
            "Merchandise Subtotal",
        ]
    ).run(timeout=20)
    assert "merchandise_subtotal" in app.session_state.filtered_state[
        "data_import_current_batch_optional_order_columns"
    ]

    next(element for element in app.text_input if element.label == "Product or SKU").set_value("LZD-SKU").run(timeout=20)

    filtered_order_table = next(
        dataframe.value
        for dataframe in app.dataframe
        if "Order ID" in dataframe.value.columns
        and dataframe.value["Order ID"].tolist() == ["LZD-1"]
    )
    assert filtered_order_table["Order ID"].tolist() == ["LZD-1"]


def test_shopee_order_table_projects_missing_created_date_from_order_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {
            "platform": "Shopee",
            "order_id": "260827SOURCE",
            "order_created_date": "27/08/2026 12:04",
        },
        {"platform": "Shopee", "order_id": "260828J247W9SW", "order_created_date": "None"},
    ]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = "batch-order-date-projection"
    app.session_state["pdf_count"] = 1
    app.session_state["upload_result_summary"] = {"pdfs_processed": 1}
    app.session_state["navigation"] = "Data Import"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3

    app.run(timeout=20)

    assert app.exception == []
    order_table = next(
        dataframe.value
        for dataframe in app.dataframe
        if "Order Created Date" in dataframe.value.columns
    )
    assert order_table["Order Created Date"].iloc[0].date() == date(2026, 8, 27)
    assert order_table["Order Created Date"].iloc[1].date() == date(2026, 8, 28)


def test_platform_tables_keep_all_fields_available_in_native_toolbar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {
            "platform": "Lazada",
            "order_id": "LZD-1",
            "voucher_applied": "1.20",
        }
    ]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = "batch-full-table"
    app.session_state["pdf_count"] = 1
    app.session_state["navigation"] = "Lazada"

    app.run(timeout=20)

    assert app.exception == []
    order_table = next(
        dataframe for dataframe in app.dataframe if "Order ID" in dataframe.value.columns
    )
    assert order_table.value.columns.tolist() == [
        FIELD_LABELS.get(column, column)
        for column in PLATFORM_ORDER_FIELDS["Lazada"]
    ]
    assert "Voucher Applied" in order_table.value.columns
    assert app.session_state.filtered_state["Lazada_optional_order_columns"] == [
        "invoice_number",
        "order_date",
        "invoice_date",
        "payment_method",
        "subtotal",
        "net_paid",
        "source_pdf",
        "status",
    ]


def test_upload_summary_is_action_scoped_and_skipped_items_stay_out_of_manual_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["batch_id"] = "batch-summary"
    app.session_state["pdf_count"] = 15
    app.session_state["orders"] = [
        {"platform": "Shopee", "order_id": "ORD-A", "delivery_fee": "2.00"}
    ]
    app.session_state["products"] = [
        {
            "platform": "Shopee",
            "order_id": "ORD-A",
            "product_name": "Accepted product",
            "unit_price": "10.00",
            "seller_sku": "SKU-A",
            "quantity": 1,
        }
    ]
    app.session_state["reviews"] = [
        {
            "batch_id": "batch-summary",
            "platform": "Shopee",
            "order_id": "ORD-R",
            "source_pdf": "review.pdf",
            "status": "Manual Review",
            "reason": "Financial Reconciliation Failed",
            "order_payload": {"delivery_fee": "1.00"},
            "product_payloads": [
                {
                    "platform": "Shopee",
                    "order_id": "ORD-R",
                    "product_name": "Review product",
                    "unit_price": "12.00",
                    "seller_sku": "SKU-R",
                    "quantity": 1,
                }
            ],
        },
        {
            "batch_id": "batch-summary",
            "platform": "Shopee",
            "order_id": "ORD-D",
            "source_pdf": "duplicate.pdf",
            "status": "Duplicate Skipped",
            "reason": "Duplicate Order",
            "order_payload": {"delivery_fee": "99.00"},
            "product_payloads": [
                {
                    "platform": "Shopee",
                    "order_id": "ORD-D",
                    "product_name": "Duplicate product",
                    "unit_price": "99.00",
                    "seller_sku": "SKU-D",
                    "quantity": 9,
                }
            ],
        },
    ]
    app.session_state["upload_result_summary"] = {
        "pdfs_processed": 5,
        "orders_imported": 3,
        "manual_reviews": 1,
        "duplicate_orders": 1,
        "unsupported_files": 1,
        "processing_errors": 0,
    }
    app.session_state["duplicate_skipped"] = [
        {
            "platform": "Shopee",
            "order_id": "ORD-D",
            "source_pdf": "duplicate.pdf",
            "message": "Already exists in current batch.",
        }
    ]
    app.session_state["unsupported_files"] = [
        {
            "filename": "lecture_notes.pdf",
            "source_pdf": "lecture_notes.pdf",
            "status": "Unsupported",
            "message": "Not recognized as a supported invoice.",
        }
    ]
    app.session_state["processing_errors"] = []

    app.session_state["navigation"] = "Data Import"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["invoice_upload_attempt"] = "resolved"
    app.run(timeout=20)

    assert app.exception == []
    assert {
        "Data Import",
        "Dashboard",
        "Cross Platform Summary",
        "Shopee",
        "Lazada",
        "ZENXIN",
   } <= {button.label for button in app.button}
    metrics = {(metric.label, metric.value) for metric in app.metric}
    assert {
        ("Orders", "1"),
        ("Products", "1"),
        ("Quantity", "1"),
        ("Manual Review", "1"),
    } <= metrics
    assert ("PDFs", "15") not in metrics
    assert any(
        "Latest upload: File processing finished · 5 PDF(s) in the latest action"
        in caption.value
        and "1 duplicate skipped" in caption.value
        and "1 unsupported" in caption.value
        for caption in app.caption
    )
    assert "batch-summary" not in {caption.value for caption in app.caption}
    assert {"Discard current batch", "Logout"} <= {
        button.label for button in app.button
    }
    assert "Export Batch Excel" not in {
        button.label for button in app.get("download_button")
    }
    assert app.toggle == []
    assert app.get("badge") == []
    assert "View skipped items" in {expander.label for expander in app.expander}
    assert {
        "Validate",
        "Current batch summary",
        "Current Batch — Order Level Data",
    } <= {
        element.value for element in app.subheader
    }
    assert "Review & Commit" not in {element.value for element in app.subheader}
    assert {"Needs Attention", "Resolve Manual Review"} <= {
        element.value for element in app.subheader
    }
    current_order_table = next(
        dataframe.value
        for dataframe in app.dataframe
        if "Order ID" in dataframe.value.columns
        and dataframe.value["Order ID"].tolist() == ["ORD-A"]
    )
    assert current_order_table["Order ID"].tolist() == ["ORD-A"]

    navigate(app, "Shopee")

    assert app.exception == []
    assert {"Search and Filters", "Product Level"} <= {
        element.value for element in app.subheader
    }
    assert {"Order Level", "Manual Review"}.isdisjoint(
        {element.value for element in app.subheader}
    )
    assert app.expander == []
    assert len(app.dataframe) == 1
    assert {"Export full Shopee batch", "Export current filtered view"} <= {
        button.label for button in app.get("download_button")
    }


def test_platform_export_keeps_full_batch_separate_from_filtered_view(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {"platform": "Shopee", "order_id": "SHP-1", "order_income": "10.00"},
        {"platform": "Shopee", "order_id": "SHP-2", "order_income": "20.00"},
    ]
    app.session_state["products"] = [
        {
            "platform": "Shopee",
            "order_id": "SHP-1",
            "product_name": "First product",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_subtotal": "10.00",
        },
        {
            "platform": "Shopee",
            "order_id": "SHP-2",
            "product_name": "Second product",
            "seller_sku": "SKU-2",
            "quantity": 2,
            "line_subtotal": "20.00",
        },
    ]
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = "batch-export-scopes"
    app.session_state["pdf_count"] = 2
    app.session_state["navigation"] = "Shopee"

    app.run(timeout=20)
    next(element for element in app.text_input if element.label == "Order ID").set_value("SHP-1").run(timeout=20)

    assert app.exception == []
    assert {"Export full Shopee batch", "Export current filtered view"} <= {
        button.label for button in app.get("download_button")
    }

    full_workbook = load_workbook(tmp_path / "exports" / "batch-export-scopes-shopee-full-batch-report.xlsx")
    filtered_workbook = load_workbook(tmp_path / "exports" / "batch-export-scopes-shopee-filtered-view-report.xlsx")
    assert full_workbook["Orders"].max_row == 5
    assert full_workbook["Products"].max_row == 5
    assert filtered_workbook["Orders"].max_row == 4
    assert filtered_workbook["Products"].max_row == 4
    assert dict(full_workbook["Summary"].iter_rows(min_row=4, values_only=True))["Orders"] == 2
    assert dict(filtered_workbook["Summary"].iter_rows(min_row=4, values_only=True))["Orders"] == 1

def test_cross_platform_page_does_not_keep_session_date_filtering_code():
    source = APP_PATH.read_text(encoding="utf-8")
    branch = source[source.index('elif selected_page == "Cross Platform Summary":') :]

    assert "render_cross_platform_product_summary()" in branch
    assert "show_all_tab(orders, products, reviews)" not in branch


def test_cross_platform_page_uses_its_own_read_only_ui_module():
    source = APP_PATH.read_text(encoding="utf-8")

    assert "from src.invoice_app.ui.cross_platform_product_summary import" in source
    assert "render_cross_platform_product_summary" in source


def _weekly_stage_for_ui() -> StagedShopeeWeeklyStatement:
    order_row = SettlementIncomeRow(
        sequence_no="1",
        view_by="Order",
        order_id="SHP-1",
        product_id="PID-1",
        product_name="Statement product",
        order_creation_date=date(2026, 8, 1),
        payout_completed_date=date(2026, 8, 7),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal("12.34"),
        financial_components={},
        source_values={},
        source_row_number=2,
    )
    sku_row = SettlementIncomeRow(
        sequence_no="2",
        view_by="Sku",
        order_id="SHP-1",
        product_id="PID-1",
        product_name="Statement product",
        order_creation_date=date(2026, 8, 1),
        payout_completed_date=date(2026, 8, 7),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal("12.34"),
        financial_components={},
        source_values={},
        source_row_number=3,
    )
    statement = ParsedShopeeWeeklyStatement(
        source_filename="Income.released.my.xlsx",
        file_hash="test-hash",
        statement_period_from=date(2026, 8, 1),
        statement_period_to=date(2026, 8, 7),
        summary_total_released=Decimal("12.34"),
        adjustment_control_total=Decimal("-1.00"),
        adjustment_footer_total=Decimal("-1.00"),
        income_rows=(order_row, sku_row),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )
    return StagedShopeeWeeklyStatement(
        result=READY_TO_COMMIT,
        source_filename=statement.source_filename,
        file_hash=statement.file_hash,
        statement=statement,
        validation_issues=(),
        review_reasons=(),
        rejection_reasons=(),
        duplicate_status=None,
        order_reconciliations=(
            OrderReconciliation(
                order_id="SHP-1",
                status="Different",
                released_amount=Decimal("12.34"),
                order_income=Decimal("10.00"),
                difference=Decimal("2.34"),
            ),
        ),
        adjustment_reconciliations=(
            AdjustmentReconciliation(
                linked_order_id="SHP-OLD",
                status="Unmatched Adjustment",
                adjustment_amount=Decimal("-1.00"),
            ),
        ),
    )


def test_data_import_wizard_selects_weekly_statement_before_upload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"

    app.run(timeout=20)

    assert app.exception == []
    assert "Data Import" in {title.value for title in app.title}
    stepper = {element.value for element in app.markdown if "badge[" in element.value}
    assert len(stepper) == 5
    assert any("1. Select Source" in item and "Current" in item for item in stepper)
    assert any("5. Review & Commit" in item and "Pending" in item for item in stepper)
    assert app.get("progress") == []
    source_type = next(
        control
        for control in app.get("button_group")
        if control.label == "Import workflow"
    )
    assert source_type.options == [
        "Invoice Import",
        "Shopee Weekly Statement",
        "Shopee Monthly Statement",
    ]
    source_type.set_value("Shopee Weekly Statement").run(timeout=20)
    next(button for button in app.button if button.label == "Continue to upload").click().run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["import_source_type"] == "Shopee Weekly Statement"
    assert app.session_state.filtered_state["data_import_step"] == 2
    assert any(element.label == "Shopee Weekly Statement (.xlsx)" for element in app.file_uploader)
    assert {"Shipment Confirmation", "Historical Import"}.isdisjoint(
        {element.value for element in app.subheader}
    )


def test_data_import_exposes_distinct_monthly_statement_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"

    app.run(timeout=20)
    source_type = next(
        control
        for control in app.get("button_group")
        if control.label == "Import workflow"
    )
    source_type.set_value("Shopee Monthly Statement").run(timeout=20)
    next(button for button in app.button if button.label == "Continue to upload").click().run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["import_source_type"] == "Shopee Monthly Statement"
    assert any(
        element.label == "Shopee Monthly Statement (.xlsx)"
        for element in app.file_uploader
    )
    stepper = {element.value for element in app.markdown if "badge[" in element.value}
    assert len(stepper) == 4
    assert not any("Reconcile" in item for item in stepper)


def test_platform_invoice_uses_five_steps_without_provisional_normalization():
    state = {
        "import_source_type": data_import.PLATFORM_ORDERS,
        "data_import_step": 5,
    }

    assert data_import._workflow_steps_for_source(data_import.PLATFORM_ORDERS) == (
        "Select Source",
        "Upload",
        "Validate",
        "Reconcile",
        "Review & Commit",
    )
    assert data_import._normalize_step_for_source(state) == 5
    assert state["data_import_step"] == 5


def test_platform_invoice_review_route_shows_five_stepper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "legacy-platform-review",
        "import_source_type": "Platform Orders",
        "data_import_step": 5,
        "invoice_upload_attempt": "resolved",
        "upload_result_summary": {"pdfs_processed": 1},
        "orders": [],
        "products": [],
        "reviews": [],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    stepper = {element.value for element in app.markdown if "badge[" in element.value}
    assert len(stepper) == 5
    assert any("4. Reconcile" in item for item in stepper)
    assert app.session_state.filtered_state["data_import_step"] == 5
    assert "Review & Commit" in {element.value for element in app.subheader}


def test_platform_invoice_validate_reconcile_and_review_keep_their_own_controls(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    historical_entries = (
        InvoiceIntakeEntry(
            staging_id="already-imported.pdf:hash:SHP-HISTORY",
            source_filename="already-imported.pdf",
            source_hash="hash",
            order_id="SHP-HISTORY",
            status=IntakeStatus.ALREADY_IMPORTED,
            message="Invoice already exists in UAT2.",
        ),
    )
    monkeypatch.setattr(
        data_import,
        "_reconcile_historical_invoice_staging",
        lambda: historical_entries,
    )
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "platform-ownership",
        "import_source_type": data_import.PLATFORM_ORDERS,
        "data_import_step": 3,
        "invoice_upload_attempt": "resolved",
        "upload_result_summary": {"pdfs_processed": 1},
        "orders": [],
        "products": [],
        "reviews": [],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    assert "Validate" in {element.value for element in app.subheader}
    assert "Historical classification details" not in {
        element.label for element in app.expander
    }
    assert "Current batch summary" in {element.value for element in app.subheader}
    assert "Current Batch — Order Level Data" in {
        element.value for element in app.subheader
    }
    assert not any("non-NEW" in button.label for button in app.button)

    app.session_state["data_import_step"] = 4
    app.run(timeout=20)

    assert app.exception == []
    assert "Reconcile" in {element.value for element in app.subheader}
    assert "Current batch summary" not in {element.value for element in app.subheader}
    assert "Historical classification details" in {
        element.label for element in app.expander
    }
    assert any("non-NEW" in button.label for button in app.button)
    assert any(
        button.label == "Continue to review & commit"
        for button in app.button
    )

    app.session_state["data_import_step"] = 5
    app.run(timeout=20)

    assert app.exception == []
    assert "Review & Commit" in {element.value for element in app.subheader}
    assert "Current batch summary" not in {element.value for element in app.subheader}
    assert "Current Batch — Order Level Data" not in {
        element.value for element in app.subheader
    }
    assert "Search and Filters" not in {element.value for element in app.subheader}
    assert not any(element.label == "Order columns" for element in app.multiselect)
    assert "Historical Invoice Commit" in {element.value for element in app.subheader}
    assert any(button.key == "uat2_historical_commit" for button in app.button)
    assert "Historical classification details" not in {
        element.label for element in app.expander
    }

    next(
        button for button in app.button if button.key == "invoice_commit_back"
    ).click().run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["data_import_step"] == 4
    assert "Reconcile" in {element.value for element in app.subheader}


def test_data_import_prevents_second_source_for_an_active_batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "active-platform-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 1

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue current batch" in {element.value for element in app.subheader}
    assert not any(
        control.label == "Import workflow"
        for control in app.get("button_group")
    )
    assert {"Continue", "Discard current batch"} <= {
        button.label for button in app.button
    }


def test_weekly_statement_stage_without_review_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "weekly-batch"
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 4
    app.session_state["weekly_statement_stage"] = _weekly_stage_for_ui()

    app.run(timeout=20)

    assert app.exception == []
    assert any("Statement check required" in item.value for item in app.error)
    assert not app.metric
    assert not any(
        button.label == "Commit Statement" and not button.disabled
        for button in app.button
    )
    assert "Representative exceptions" not in {caption.value for caption in app.caption}
    assert "These results are shown for review and do not change the source outcome." not in {
        caption.value for caption in app.caption
    }


def test_discard_current_batch_uses_stronger_confirmation_and_cancel_is_safe(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Dashboard",
        "batch_id": "discard-cancel-batch",
        "import_source_type": "Platform Orders",
        "orders": [{"order_id": "KEEP-ME"}],
        "products": [],
        "reviews": [],
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Discard current batch"
    ).click().run(timeout=20)

    assert app.session_state.filtered_state["batch_id"] == "discard-cancel-batch"
    assert any(
        "not just the current step" in warning.value
        and "cannot be undone" in warning.value
        for warning in app.warning
    )
    assert any(button.label == "Discard Current Batch" for button in app.button)
    next(
        button for button in app.button
        if button.key == "cancel_discard_current_batch"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert state["batch_id"] == "discard-cancel-batch"
    assert state["orders"] == [{"order_id": "KEEP-ME"}]


def test_logout_with_unfinished_work_requires_confirmation_and_stay_is_safe(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "authenticated_username": "Admin",
        "navigation": "Dashboard",
        "batch_id": "logout-warning-batch",
        "import_source_type": "Platform Orders",
        "orders": [{"order_id": "UNCOMMITTED"}],
        "products": [],
        "reviews": [],
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)

    pending = app.session_state.filtered_state
    assert pending["authenticated"] is True
    assert pending["batch_id"] == "logout-warning-batch"
    assert any(
        "unfinished work" in warning.value
        and "Uncommitted progress may be lost" in warning.value
        for warning in app.warning
    )
    assert any(button.label == "Log Out" for button in app.button)
    next(
        button for button in app.button
        if button.key == "cancel_logout_with_unfinished_work"
    ).click().run(timeout=20)

    stayed = app.session_state.filtered_state
    assert stayed["authenticated"] is True
    assert stayed["batch_id"] == "logout-warning-batch"

    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)
    next(button for button in app.button if button.label == "Log Out").click().run(timeout=20)

    logged_out = app.session_state.filtered_state
    assert logged_out["authenticated"] is False
    assert "batch_id" not in logged_out


def test_logout_without_unfinished_work_has_no_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Dashboard"

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["authenticated"] is False
    assert not any(
        button.key == "confirm_logout_with_unfinished_work"
        for button in app.button
    )
