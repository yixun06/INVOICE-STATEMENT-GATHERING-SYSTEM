from src.invoice_app.services.analytics import compute_overall_dashboard, compute_platform_dashboard
from src.invoice_app.services.all_products import build_all_product_rows


def test_overall_dashboard_uses_platform_specific_income_fields():
    dashboard = compute_overall_dashboard(
        orders=[
            {"platform": "Shopee", "order_income": "24.53", "net_amount": "999.00"},
            {"platform": "Lazada", "net_paid": "33.20", "gross_sales": "999.00"},
            {"platform": "ZENXIN", "total": "12.27", "gross_sales": "999.00"},
        ],
        products=[{"quantity": 2}, {"quantity": "3"}],
        pdf_count=4,
    )

    assert dashboard == {
        "pdf_count": 4,
        "order_count": 3,
        "product_rows": 2,
        "total_quantity": 5,
        "income": "70.00",
    }


def test_platform_dashboard_includes_only_requested_metrics():
    dashboard = compute_platform_dashboard(
        platform_orders=[{"platform": "Shopee", "order_income": "24.53"}],
        platform_products=[{"quantity": "2"}],
    )

    assert dashboard == {
        "orders": 1,
        "products": 1,
        "quantity": 2,
        "income": "24.53",
    }


def test_overall_dashboard_is_empty_but_renderable_for_manual_review_only_batch():
    dashboard = compute_overall_dashboard(orders=[], products=[], pdf_count=0)

    assert dashboard == {
        "pdf_count": 0,
        "order_count": 0,
        "product_rows": 0,
        "total_quantity": 0,
        "income": "0.00",
    }


def test_manual_review_rows_in_all_never_change_dashboard_analytics():
    accepted_orders = [{"platform": "Shopee", "order_id": "SHP-A", "order_income": "10.00"}]
    accepted_products = [
        {
            "platform": "Shopee",
            "order_id": "SHP-A",
            "product_name": "Accepted",
            "seller_sku": "A",
            "quantity": 1,
            "unit_price": "10.00",
        }
    ]
    reviews = [
        {
            "platform": "Shopee",
            "order_id": "SHP-R",
            "order_payload": {"delivery_fee": "4.90", "payment_status": "Released"},
            "product_payloads": [
                {
                    "platform": "Shopee",
                    "order_id": "SHP-R",
                    "product_name": "Review only",
                    "seller_sku": "R",
                    "quantity": 9,
                    "unit_price": "999.00",
                }
            ],
        }
    ]

    assert len(build_all_product_rows(accepted_orders, accepted_products, reviews)) == 2
    assert compute_overall_dashboard(accepted_orders, accepted_products, pdf_count=2) == {
        "pdf_count": 2,
        "order_count": 1,
        "product_rows": 1,
        "total_quantity": 1,
        "income": "10.00",
    }
