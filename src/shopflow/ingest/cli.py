"""shopflow-ingest: download -> load -> translate.

shopflow-ingest all                 # everything, end to end
shopflow-ingest download [--zip F]  # fetch/extract the Kaggle dataset into data/raw
shopflow-ingest load                # CSV -> staging -> curated tables
shopflow-ingest translate [--limit N] [--backend libretranslate|prefix]
"""

import argparse
import os
import time
from pathlib import Path

import psycopg

from shopflow.core.config import get_settings
from shopflow.ingest import download as dl
from shopflow.ingest import load, translate


def _conninfo() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg", "postgresql")


def cmd_download(args) -> None:
    dl.download(args.raw_dir, zip_path=args.zip, force=args.force)


def cmd_load(args) -> None:
    with psycopg.connect(_conninfo()) as conn:
        t0 = time.perf_counter()
        print("loading staging tables")
        for table, n in load.load_staging(conn, args.raw_dir).items():
            print(f"  staging.{table:<20} {n:>8} rows")
        print("transforming into curated tables")
        load.transform(conn, get_settings().default_stock)
        print(f"load finished in {time.perf_counter() - t0:.1f}s")


def cmd_translate(args) -> None:
    if args.backend == "none":
        print("translation disabled (TRANSLATOR_BACKEND=none)")
        return
    translator = translate.make_translator(args.backend, args.libretranslate_url)
    with psycopg.connect(_conninfo()) as conn:
        print("translating review text pt -> en")
        translate.translate_reviews(
            conn,
            translator,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            limit=args.limit,
        )


def cmd_all(args) -> None:
    cmd_download(args)
    cmd_load(args)
    cmd_translate(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shopflow-ingest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--raw-dir", type=Path, default=Path(os.getenv("RAW_DIR", "data/raw")))
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_download_args(sp):
        sp.add_argument("--zip", type=Path, help="use a manually downloaded Kaggle zip")
        sp.add_argument("--force", action="store_true", help="re-download even if present")

    def add_translate_args(sp):
        sp.add_argument(
            "--backend",
            default=os.getenv("TRANSLATOR_BACKEND", "libretranslate"),
            choices=["libretranslate", "prefix", "none"],
        )
        sp.add_argument(
            "--libretranslate-url", default=os.getenv("LIBRETRANSLATE_URL", "http://localhost:5000")
        )
        sp.add_argument("--limit", type=int, help="translate at most N distinct strings")
        sp.add_argument("--batch-size", type=int, default=25)
        sp.add_argument("--concurrency", type=int, default=4)

    sp = sub.add_parser("download")
    add_download_args(sp)
    sp.set_defaults(fn=cmd_download)

    sp = sub.add_parser("load")
    sp.set_defaults(fn=cmd_load)

    sp = sub.add_parser("translate")
    add_translate_args(sp)
    sp.set_defaults(fn=cmd_translate)

    sp = sub.add_parser("all")
    add_download_args(sp)
    add_translate_args(sp)
    sp.set_defaults(fn=cmd_all)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
