"""Fetch the Olist dataset from Kaggle.

Credentials are read by the official Kaggle client from either
  * env vars KAGGLE_USERNAME / KAGGLE_KEY (e.g. in .env), or
  * ~/.kaggle/kaggle.json
If neither is present we fail with instructions instead of hanging. You can also skip
Kaggle entirely: download the zip in a browser and pass --zip.
"""

import os
import sys
import zipfile
from pathlib import Path

DATASET = "olistbr/brazilian-ecommerce"

EXPECTED_FILES = {
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
}

HELP = """Kaggle credentials not found.

Provide them one of these ways, then re-run:
  1. Add KAGGLE_USERNAME and KAGGLE_KEY to .env
     (kaggle.com -> Settings -> API -> "Create New Token" gives you both)
  2. Put the downloaded kaggle.json at ~/.kaggle/kaggle.json (chmod 600)
  3. Download the zip manually from
     https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
     and run: shopflow-ingest download --zip /path/to/archive.zip
"""


def _has_credentials() -> bool:
    if os.getenv("KAGGLE_USERNAME") and os.getenv("KAGGLE_KEY"):
        return True
    config_dir = Path(os.getenv("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle"))
    return (config_dir / "kaggle.json").exists()


def missing_files(raw_dir: Path) -> set[str]:
    return {f for f in EXPECTED_FILES if not (raw_dir / f).exists()}


def extract_zip(zip_path: Path, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name in EXPECTED_FILES:  # ignore anything unexpected in the archive
                (raw_dir / name).write_bytes(zf.read(member))


def download(raw_dir: Path, zip_path: Path | None = None, force: bool = False) -> None:
    if zip_path:
        extract_zip(zip_path, raw_dir)
    elif force or missing_files(raw_dir):
        if not _has_credentials():
            sys.exit(HELP)
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
        except ImportError:
            sys.exit("kaggle package missing: pip install 'shopflow[ingest]'")
        api = KaggleApi()
        api.authenticate()
        raw_dir.mkdir(parents=True, exist_ok=True)
        api.dataset_download_files(DATASET, path=str(raw_dir), unzip=True, quiet=False)

    if missing := missing_files(raw_dir):
        sys.exit(f"dataset incomplete in {raw_dir}, missing: {sorted(missing)}")
    print(f"dataset ready in {raw_dir}")
