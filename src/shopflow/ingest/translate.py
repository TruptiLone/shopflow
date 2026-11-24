"""Translate Portuguese review text to English.

Design:
  * Content-addressed cache (`translations` table keyed by sha256 of the source text):
    Olist has ~41k review messages but many repeat ("Recomendo", "Muito bom"), so each
    distinct string is translated exactly once. Re-runs and crashes resume for free.
  * Pluggable backends behind one small interface. Default is a self-hosted
    LibreTranslate container (open source, no API key, no per-call cost). An LLM backend
    can be added in the AI phase without touching the pipeline.
  * Batches + bounded concurrency + retry with backoff, so a flaky backend doesn't kill
    a multi-hour run.
"""

import hashlib
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

import httpx
import psycopg


class Translator(Protocol):
    name: str

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]: ...


class LibreTranslate:
    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ):
        self.name = "libretranslate"
        self.url = url.rstrip("/") + "/translate"
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout)

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        body: dict = {"q": list(texts), "source": source, "target": target, "format": "text"}
        if self.api_key:
            body["api_key"] = self.api_key
        resp = self.client.post(self.url, json=body)
        resp.raise_for_status()
        out = resp.json()["translatedText"]
        return out if isinstance(out, list) else [out]


class Prefix:
    """Deterministic fake for tests and dry runs."""

    name = "prefix"

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        return [f"[{target}] {t}" for t in texts]


def make_translator(backend: str, libretranslate_url: str) -> Translator:
    if backend == "libretranslate":
        return LibreTranslate(libretranslate_url)
    if backend == "prefix":
        return Prefix()
    raise ValueError(f"unknown translator backend: {backend}")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _with_retry(fn, attempts: int = 5):
    for i in range(attempts):
        try:
            return fn()
        except (httpx.HTTPError, KeyError) as exc:
            if i == attempts - 1:
                raise
            wait = 2**i
            print(f"    translate failed ({exc!s:.80}); retrying in {wait}s")
            time.sleep(wait)


def _chunks(seq: list[str], n: int) -> Iterator[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


PENDING_SQL = """
    SELECT DISTINCT txt FROM (
        SELECT title_pt AS txt FROM reviews WHERE title_pt IS NOT NULL AND title_en IS NULL
        UNION
        SELECT message_pt FROM reviews WHERE message_pt IS NOT NULL AND message_en IS NULL
    ) s
    WHERE NOT EXISTS (
        SELECT 1 FROM translations t
        WHERE t.source_hash = encode(sha256(convert_to(s.txt, 'UTF8')), 'hex')
          AND t.target_lang = %(target)s
    )
    ORDER BY txt
"""

APPLY_SQL = """
    UPDATE reviews r SET
        title_en = COALESCE(r.title_en, (
            SELECT t.translated_text FROM translations t
            WHERE t.target_lang = %(target)s
              AND t.source_hash = encode(sha256(convert_to(r.title_pt, 'UTF8')), 'hex'))),
        message_en = COALESCE(r.message_en, (
            SELECT t.translated_text FROM translations t
            WHERE t.target_lang = %(target)s
              AND t.source_hash = encode(sha256(convert_to(r.message_pt, 'UTF8')), 'hex')))
    WHERE (r.title_pt IS NOT NULL AND r.title_en IS NULL)
       OR (r.message_pt IS NOT NULL AND r.message_en IS NULL)
"""


def translate_reviews(
    conn: psycopg.Connection,
    translator: Translator,
    *,
    source: str = "pt",
    target: str = "en",
    batch_size: int = 25,
    concurrency: int = 4,
    limit: int | None = None,
) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(PENDING_SQL, {"target": target})
        pending = [r[0] for r in cur.fetchall()]
    if limit is not None:
        pending = pending[:limit]
    print(f"  {len(pending)} distinct strings to translate with {translator.name}")

    done = 0
    t0 = time.perf_counter()

    def work(batch: list[str]) -> tuple[list[str], list[str]]:
        return batch, _with_retry(lambda: translator.translate(batch, source, target))

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for batch, out in pool.map(work, _chunks(pending, batch_size)):
            if len(out) != len(batch):
                raise RuntimeError("translator returned a different number of results")
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO translations (source_hash, target_lang, source_lang,
                                                 source_text, translated_text, backend)
                       VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    [
                        (text_hash(s), target, source, s, t, translator.name)
                        for s, t in zip(batch, out, strict=True)
                    ],
                )
            conn.commit()  # checkpoint every batch so a crash loses at most one batch
            done += len(batch)
            if done % (batch_size * 20) == 0 or done == len(pending):
                rate = done / max(time.perf_counter() - t0, 1e-6)
                print(f"    {done}/{len(pending)} ({rate:.1f}/s)")

    with conn.cursor() as cur:
        cur.execute(APPLY_SQL, {"target": target})
        applied = cur.rowcount
    conn.commit()
    print(f"  applied translations to {applied} reviews")
    return {"translated": done, "reviews_updated": applied}
