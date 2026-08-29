from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = APP_ROOT / "archive"
SECRETS_PATH = APP_ROOT / ".streamlit" / "secrets.toml"
SHOPEE_PRODUCT_MASTER_PATH = Path(
    os.getenv(
        "INV_SHOPEE_PRODUCT_MASTER_PATH",
        str(APP_ROOT.parent / "UNIT PRICE BASED SKU (SHOPEE).xlsx"),
    )
)

DEFAULT_USERNAME = os.getenv("INV_USERNAME", "admin")
DEFAULT_PASSWORD_HASH = os.getenv(
    "INV_PASSWORD_HASH",
    "pbkdf2_sha256$100000$SW52b2ljZUdhdGhlcg==$lJQBN/mMLpX/37TqOkOZ/h9VbgRtcJ5c8l3aijKnAf0=",
)
APP_TITLE = "Invoice Analytics System"
