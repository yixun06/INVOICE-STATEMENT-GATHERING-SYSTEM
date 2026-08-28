from src.invoice_app.detector import detect_platform


def test_detect_platform_shopee():
    text = """
    Order ID: SHP123456
    New Order
    SKU: ABC-001
    Qty: 2
    Unit Price: RM 12.50
    Subtotal: RM 25.00
    Merchandise Subtotal
    """
    assert detect_platform(text) == "Shopee"


def test_detect_platform_lazada():
    text = """
    Invoice Number: INV-1001
    Order Number: LZ123
    Order Date: 2026-01-10
    Seller SKU: SKU-002
    Paid Price: RM 18.00
    Shipping:
    """
    assert detect_platform(text) == "Lazada"


def test_detect_platform_zenxin():
    text = """
    Invoice No. INV-998
    Order No. ZNX77
    Date: 2026-08-18
    Amount: RM 44.00
    SKU: ZNX-455
    Quantity: 1
    """
    assert detect_platform(text) == "ZENXIN"


def test_platform_names_without_invoice_anchors_are_not_detected():
    text = """
    These lecture notes compare Shopee, Lazada, and ZENXIN as company names.
    They do not contain an invoice number, an order number, or a product table.
    """

    assert detect_platform(text) is None
