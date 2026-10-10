"""Check how many v1 products have community Q&A in the older McAuley Q&A datasets.

The Q&A files (2016 single-answer, 2019 multi-answer) predate Amazon Reviews 2023 and are
keyed by ASIN, while 2023 groups variants under parent_asin. A Q&A ASIN matches a v1
product if it equals the product's parent_asin OR any variant ASIN seen in its reviews.

Always writes data/processed/qa_overlap.json (the recorded result). Writes
data/processed/qa.jsonl (rows for load_db.py) only if at least `qa.min_matched_products`
v1 products matched (data/manifest.yaml).

Both Q&A files are Python dict literals per line (not JSON), parsed with ast.literal_eval,
which evaluates literals only, never code.

Usage:
    python scripts/check_qa_overlap.py
"""

from __future__ import annotations

import ast
import gzip
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

from load_db import QARow, read_jsonl, stable_id
from manifest import (
    QA_OVERLAP_REPORT,
    SUBSET_PRODUCTS,
    SUBSET_QA,
    SUBSET_REVIEWS,
    load_manifest,
)

log = logging.getLogger("check_qa_overlap")


def read_literal_lines(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield ast.literal_eval(line)


def qa_pairs(record: dict, source: str) -> Iterator[dict]:
    """Normalise one source record into (asin, question, answer, type, date) dicts.

    qa_2016: one question + one answer per line.
    qa_2019: one product per line with a list of questions, each with several answers.
    """
    if "questions" not in record:
        yield {
            "asin": record["asin"],
            "question": record.get("question") or "",
            "answer": record.get("answer") or "",
            "question_type": record.get("questionType"),
            "answered_on": record.get("answerTime"),
        }
        return
    for q in record["questions"]:
        for a in q.get("answers") or []:
            yield {
                "asin": record["asin"],
                "question": q.get("questionText") or "",
                "answer": a.get("answerText") or "",
                "question_type": q.get("questionType"),
                "answered_on": a.get("answerTime"),
            }


def asin_to_product(products: set[str], variants: dict[str, str]) -> dict[str, str]:
    """Map every ASIN we can recognise (parent or variant) to its v1 product_id."""
    mapping = dict(variants)
    mapping.update({p: p for p in products})
    return mapping


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manifest = load_manifest()
    if not SUBSET_PRODUCTS.exists():
        log.error("missing %s: run scripts/select_subset.py first", SUBSET_PRODUCTS.name)
        return 2

    products = {p["parent_asin"] for p in read_jsonl(SUBSET_PRODUCTS)}
    variants = {r["asin"]: r["parent_asin"] for r in read_jsonl(SUBSET_REVIEWS)}
    mapping = asin_to_product(products, variants)

    report: dict = {"v1_products": len(products), "v1_variant_asins": len(variants)}
    rows: list[QARow] = []
    matched_products: set[str] = set()
    for source in manifest.qa.sources:
        path = manifest.files[source].local_path
        pairs = list(p for rec in read_literal_lines(path) for p in qa_pairs(rec, source))
        asins = {p["asin"] for p in pairs}
        hits = [p for p in pairs if p["asin"] in mapping and p["question"] and p["answer"]]
        source_products = {mapping[p["asin"]] for p in hits}
        matched_products |= source_products
        report[source] = {
            "qa_pairs": len(pairs),
            "distinct_asins": len(asins),
            "asins_matching_v1": len({p["asin"] for p in hits}),
            "v1_products_with_qa": len(source_products),
            "qa_pairs_for_v1": len(hits),
        }
        for p in hits:
            rows.append(
                QARow(
                    qa_id=stable_id(source, p["asin"], p["question"], p["answer"]),
                    product_id=mapping[p["asin"]],
                    source=source,
                    question=p["question"],
                    answer=p["answer"],
                    question_type=p["question_type"],
                    answered_on=p["answered_on"],
                )
            )
        log.info("%s: %s", source, report[source])

    threshold = manifest.qa.min_matched_products
    use_qa = len(matched_products) >= threshold
    report.update(
        v1_products_with_any_qa=len(matched_products),
        min_matched_products=threshold,
        qa_used=use_qa,
    )
    QA_OVERLAP_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if use_qa:
        with SUBSET_QA.open("w", encoding="utf-8") as out:
            for row in rows:
                out.write(row.model_dump_json() + "\n")
        log.info(
            "Q&A used: %d pairs for %d products -> %s",
            len(rows),
            len(matched_products),
            SUBSET_QA.name,
        )
    else:
        SUBSET_QA.unlink(missing_ok=True)
        log.info(
            "Q&A NOT used: only %d v1 products matched (< %d)", len(matched_products), threshold
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
