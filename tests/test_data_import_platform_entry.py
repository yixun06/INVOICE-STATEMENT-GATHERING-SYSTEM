from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.services.batch_service import process_pdf_text_with_outcome
from src.invoice_app.services.market_context import ACTIVE_IMPORT_MARKET_KEY
from src.invoice_app.ui import data_import


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

LAZADA_SOURCE = """
Invoice Number: LZD-INV-1
Order Number: 501548767521442
Order Date: 02 Mar 2026
Invoice Date: 02 Mar 2026
Payment Method: Online Banking
Product name Seller SKU Shop SKU Price Paid Price
1 Organic Soy Drink Bundle
9555208106944-6 SHOP-9555208106944 28.97 28.97
Subtotal: RM 28.97
Shipping: RM 0.00
Net paid: RM 28.97
"""

ZENXIN_SOURCE = """
Invoice No. INV-161821
Order No. 10123
Date: 18/08/2026
Amount: RM 35.70
Product Qty Price Total
Organic Broccoli 3 RM11.90 RM35.70
SKU: 3000309
Standard Delivery RM0.00
Total RM35.70
"""

SHOPEE_SOURCE = """
Order ID: SHP123456
New Order
SKU: ABC-001
Qty: 2
Unit Price: RM 12.50
Subtotal: RM 25.00
Merchandise Subtotal
"""


def _data_import_app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.run(timeout=20)
    return app


def _enter_platform(app: AppTest, platform_name: str) -> AppTest:
    next(button for button in app.button if button.label == f"Enter {platform_name}").click().run(timeout=20)
    return app


def _source_selector(app: AppTest):
    return next(control for control in app.get("button_group") if control.label == "Import workflow")


def test_data_import_starts_with_a_four_platform_card_chooser(tmp_path, monkeypatch):
    app = _data_import_app(tmp_path, monkeypatch)

    assert app.exception == []
    assert "Choose a platform" in {title.value for title in app.title}
    assert {"Shopee MY", "Shopee SG", "Lazada", "Zenxin Website"} <= {
        item.value for item in app.subheader
    }
    assert {
        "Enter Shopee MY",
        "Enter Shopee SG",
        "Enter Lazada",
        "Enter Zenxin Website",
    } <= {button.label for button in app.button}
    assert app.get("button_group") == []


@pytest.mark.parametrize(
    "platform_name",
    ["Shopee MY", "Shopee SG", "Lazada", "Zenxin Website"],
)
def test_every_platform_enters_the_shared_data_import_shell(tmp_path, monkeypatch, platform_name):
    app = _enter_platform(_data_import_app(tmp_path, monkeypatch), platform_name)

    assert app.exception == []
    assert f"{platform_name} · Data Import" in {item.value for item in app.title}
    assert _source_selector(app).options == [
        "Invoice Import",
        "Weekly Statement",
        "Monthly Statement",
    ]


def test_shopee_my_keeps_all_three_current_source_workflows_available(tmp_path, monkeypatch):
    app = _enter_platform(_data_import_app(tmp_path, monkeypatch), "Shopee MY")
    selector = _source_selector(app)

    for source in ("Invoice Import", "Weekly Statement", "Monthly Statement"):
        selector.set_value(source).run(timeout=20)
        continue_button = next(button for button in app.button if button.label == "Continue to upload")
        assert continue_button.disabled is False


@pytest.mark.parametrize(
    ("platform_name", "source", "message"),
    (
        ("Shopee SG", "Invoice Import", "not available for Shopee SG yet"),
        ("Lazada", "Weekly Statement", "not available for Lazada yet"),
        ("Zenxin Website", "Monthly Statement", "not available for Zenxin Website yet"),
    ),
)
def test_unsupported_platform_source_stays_in_shared_shell_without_upload(
    tmp_path, monkeypatch, platform_name, source, message
):
    app = _enter_platform(_data_import_app(tmp_path, monkeypatch), platform_name)
    _source_selector(app).set_value(source).run(timeout=20)

    assert app.exception == []
    assert any(message in notice.value for notice in app.info)
    assert next(button for button in app.button if button.label == "Continue to upload").disabled
    assert app.file_uploader == []
    assert app.session_state.filtered_state.get("data_import_step") == 1


@pytest.mark.parametrize(
    "platform_name",
    ["Shopee MY", "Shopee SG"],
)
def test_shopee_back_is_secondary_navigation_above_the_platform_title(
    tmp_path, monkeypatch, platform_name
):
    app = _enter_platform(_data_import_app(tmp_path, monkeypatch), platform_name)

    assert app.exception == []
    assert any(button.key == "data_import_platform_back" for button in app.button)
    render_source = Path(data_import.__file__).read_text(encoding="utf-8")
    render_body = render_source[
        render_source.index("def render_data_import(") : render_source.index("def _adopt_legacy_platform_batch(")
    ]
    assert render_body.index('st.button("Back"') < render_body.index(
        'st.title(f"{platform.display_name}'
    )


def test_shopee_market_names_use_text_only_without_flag_icons():
    platforms = {platform.key: platform for platform in data_import.DATA_IMPORT_PLATFORMS}

    assert platforms["shopee_my"].display_name == "Shopee MY"
    assert platforms["shopee_sg"].display_name == "Shopee SG"
    assert not hasattr(platforms["shopee_my"], "flag_icon_path")
    assert not hasattr(platforms["shopee_sg"], "flag_icon_path")

    source = Path(data_import.__file__).read_text(encoding="utf-8")
    assert "st.image" not in source
    assert "flag_icon_path" not in source


def test_back_returns_to_chooser_without_discarding_the_active_batch(tmp_path, monkeypatch):
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "my-active-batch",
        "orders": [{"platform": "Shopee", "order_id": "SHP-1"}],
        "products": [],
        "reviews": [],
        "import_source_type": data_import.PLATFORM_ORDERS,
        "data_import_step": 2,
        data_import.ACTIVE_IMPORT_PLATFORM_KEY: "shopee_my",
        data_import.SHOW_PLATFORM_SELECTOR_KEY: False,
    }.items():
        app.session_state[key] = value
    monkeypatch.chdir(tmp_path)
    app.run(timeout=20)

    next(button for button in app.button if button.key == "data_import_platform_back").click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert "Choose a platform" in {title.value for title in app.title}
    assert state["batch_id"] == "my-active-batch"
    assert state["orders"] == [{"platform": "Shopee", "order_id": "SHP-1"}]
    assert state[data_import.ACTIVE_IMPORT_PLATFORM_KEY] == "shopee_my"


def test_active_batch_blocks_switch_until_safe_discard_then_enters_target(tmp_path, monkeypatch):
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "my-active-batch",
        "orders": [{"platform": "Shopee", "order_id": "SHP-1"}],
        "products": [],
        "reviews": [],
        "import_source_type": data_import.PLATFORM_ORDERS,
        "data_import_step": 2,
        data_import.ACTIVE_IMPORT_PLATFORM_KEY: "shopee_my",
        data_import.SHOW_PLATFORM_SELECTOR_KEY: True,
        ACTIVE_IMPORT_MARKET_KEY: "shopee_my",
    }.items():
        app.session_state[key] = value
    monkeypatch.chdir(tmp_path)
    app.run(timeout=20)

    next(button for button in app.button if button.label == "Enter Lazada").click().run(timeout=20)
    assert "Current import is still active" in {item.value for item in app.subheader}
    assert app.session_state.filtered_state["batch_id"] == "my-active-batch"

    next(
        button
        for button in app.button
        if button.label == "Discard current import and switch to Lazada"
    ).click().run(timeout=20)
    next(button for button in app.button if button.key == "confirm_discard_current_batch").click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert "Lazada · Data Import" in {title.value for title in app.title}
    assert "batch_id" not in state
    assert state[data_import.ACTIVE_IMPORT_PLATFORM_KEY] == "lazada"
    assert ACTIVE_IMPORT_MARKET_KEY not in state


@pytest.mark.parametrize(
    ("selected", "detected_source", "detected_name"),
    (
        ("Shopee", LAZADA_SOURCE, "Lazada"),
        ("Lazada", SHOPEE_SOURCE, "Shopee"),
    ),
)
def test_selected_platform_mismatch_is_rejected_before_parser_admission(
    selected, detected_source, detected_name
):
    result = process_pdf_text_with_outcome(
        "wrong-platform.pdf",
        detected_source,
        "batch-platform-mismatch",
        expected_platform=selected,
        selected_platform_label=f"{selected} selected",
    )

    assert result.orders == []
    assert result.products == []
    assert result.reviews == []
    assert result.unsupported_files[0]["status"] == "PLATFORM MISMATCH"
    assert f"Detected platform: {detected_name}." in result.unsupported_files[0]["message"]


def test_lazada_and_zenxin_correct_platform_sources_keep_existing_parser_behavior():
    lazada = process_pdf_text_with_outcome(
        "lazada.pdf", LAZADA_SOURCE, "batch-lazada", expected_platform="Lazada"
    )
    zenxin = process_pdf_text_with_outcome(
        "zenxin.pdf", ZENXIN_SOURCE, "batch-zenxin", expected_platform="ZENXIN"
    )

    assert lazada.unsupported_files == [] and len(lazada.orders) == len(lazada.products) == 1
    assert zenxin.unsupported_files == [] and len(zenxin.orders) == len(zenxin.products) == 1
