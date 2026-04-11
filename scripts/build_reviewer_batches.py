#!/usr/bin/env python3
"""Convert single-pair AI reviewer requests into fixed-size review batches."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BATCH_SIZE = 10

SHARED_INSTRUCTION_BLOCK = """You are acting as an external reviewer in a clinical-data-to-FHIR pairing evaluation workflow.

You will review multiple candidate paired samples.
Apply the same scoring rubric independently to each item.
Do not compare items with each other.
Judge each pair only on its own merits.

For each item, return:
- pair_id
- faithfulness
- unsupported_fact
- omission
- naturalness
- context_leakage
- short_rationale
- flag_type

Scoring rubric:
- faithfulness: 1 = faithful, 0 = not faithful
- unsupported_fact: 1 = yes, 0 = no
- omission: 1 = yes, 0 = no
- naturalness: 1 to 5
- context_leakage: 1 = yes, 0 = no

Allowed flag_type values:
- none
- possible_hallucination
- possible_omission
- awkward_input
- context_leakage
- style_uncertainty
- other

Review principles:
1. Only judge alignment between the shown input text and the shown target FHIR JSON.
2. Do not assume facts from linked resources unless explicitly present in the shown target JSON.
3. Do not reward unsupported extra detail.
4. Be conservative about unsupported facts.
5. Be conservative about omission of core information.
6. If the target is sparse, do not punish the input for not containing unavailable details.

Return only a JSON array.
Do not include markdown.
Do not include any text before or after the JSON array."""


@dataclass(frozen=True)
class ReviewerRequest:
    pair_id: str
    resource_type: str
    input_style: str
    input_text: str
    target_fhir_json: dict[str, Any]
    reviewer_prompt: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Group self-contained reviewer requests into fixed-size batch prompts "
            "for external reviewer models."
        )
    )
    parser.add_argument(
        "--requests-file",
        type=Path,
        default=Path("outputs/large/ai_reviewer_requests.jsonl"),
        help="Path to ai_reviewer_requests.jsonl or CSV.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Base output directory. Batch prompts will be written under reviewer_batch_prompts/ inside it.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="Number of review items per batch prompt.",
    )
    return parser.parse_args()


def load_requests(path: Path) -> list[ReviewerRequest]:
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            return [
                ReviewerRequest(
                    pair_id=row["pair_id"],
                    resource_type=row["resource_type"],
                    input_style=row["input_style"],
                    input_text=row["input_text"],
                    target_fhir_json=row["target_fhir_json"],
                    reviewer_prompt=row["reviewer_prompt"],
                )
                for row in (json.loads(line) for line in handle)
            ]

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append(
                ReviewerRequest(
                    pair_id=row["pair_id"],
                    resource_type=row["resource_type"],
                    input_style=row["input_style"],
                    input_text="",
                    target_fhir_json={},
                    reviewer_prompt=row["reviewer_prompt"],
                )
            )
        return rows


def build_item_block(index: int, request: ReviewerRequest) -> str:
    target_json_text = json.dumps(request.target_fhir_json, indent=2, ensure_ascii=True)
    return (
        f"ITEM {index}\n"
        f"pair_id: {request.pair_id}\n"
        f"resource_type: {request.resource_type}\n"
        f"input_style: {request.input_style}\n"
        f"input_text:\n"
        f"{request.input_text}\n"
        f"target_fhir_json:\n"
        f"{target_json_text}\n"
    )


def build_batch_prompt(requests: list[ReviewerRequest]) -> str:
    blocks = [SHARED_INSTRUCTION_BLOCK]
    for idx, request in enumerate(requests, start=1):
        blocks.append(build_item_block(idx, request))
    return "\n\n".join(blocks).rstrip() + "\n"


def write_batch_files(batch_dir: Path, batches: list[list[ReviewerRequest]]) -> list[dict[str, Any]]:
    batch_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    for batch_number, requests in enumerate(batches, start=1):
        batch_id = f"review_batch_{batch_number:03d}"
        file_name = f"{batch_id}.txt"
        file_path = batch_dir / file_name
        file_path.write_text(build_batch_prompt(requests), encoding="utf-8")
        manifest_rows.append(
            {
                "batch_id": batch_id,
                "file_name": file_name,
                "item_count": len(requests),
                "pair_ids": [request.pair_id for request in requests],
            }
        )
    return manifest_rows


def write_batch_index(path: Path, manifest_rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["batch_id", "file_name", "start_pair_id", "end_pair_id", "item_count"],
        )
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(
                {
                    "batch_id": row["batch_id"],
                    "file_name": row["file_name"],
                    "start_pair_id": row["pair_ids"][0],
                    "end_pair_id": row["pair_ids"][-1],
                    "item_count": row["item_count"],
                }
            )


def write_batch_manifest(path: Path, manifest_rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in manifest_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def write_notes(path: Path, requests: list[ReviewerRequest], manifest_rows: list[dict[str, Any]]) -> None:
    full_batches = sum(1 for row in manifest_rows if row["item_count"] == BATCH_SIZE)
    final_partial = manifest_rows[-1]["item_count"] != BATCH_SIZE if manifest_rows else False
    prompt_lengths = sorted(
        (
            len(request.input_text) + len(json.dumps(request.target_fhir_json, ensure_ascii=True)),
            request.pair_id,
        )
        for request in requests
    )
    longest = prompt_lengths[-5:] if prompt_lengths else []

    lines = [
        "# Reviewer Batching Notes",
        "",
        f"- Total number of pairs: {len(requests)}",
        f"- Total number of batches: {len(manifest_rows)}",
        f"- Full 10-item batches created: {full_batches}",
        f"- Final partial batch present: {'yes' if final_partial else 'no'}",
        "",
        "## Unusually Long Items",
        "",
    ]

    threshold = 5500
    flagged = [
        (pair_id, approx_len)
        for approx_len, pair_id in reversed(prompt_lengths)
        if approx_len >= threshold
    ]
    if flagged:
        for pair_id, approx_len in flagged:
            lines.append(f"- {pair_id}: approx {approx_len:,} chars across input_text + target_fhir_json")
    else:
        lines.append("- No items crossed the manual-attention threshold used here (>= 5,500 chars across input_text + target_fhir_json).")

    if longest:
        lines.extend(["", "## Longest Items Snapshot", ""])
        for approx_len, pair_id in reversed(longest):
            lines.append(f"- {pair_id}: approx {approx_len:,} chars")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = output_dir / "reviewer_batch_prompts"

    requests = load_requests(args.requests_file.resolve())
    batches = [
        requests[index : index + args.batch_size]
        for index in range(0, len(requests), args.batch_size)
    ]

    manifest_rows = write_batch_files(batch_dir, batches)
    write_batch_index(output_dir / "reviewer_batch_index.csv", manifest_rows)
    write_batch_manifest(output_dir / "reviewer_batch_manifest.jsonl", manifest_rows)
    write_notes(output_dir / "reviewer_batching_notes.md", requests, manifest_rows)

    print("Reviewer batch prompt generation complete.")
    print(f"Pairs batched: {len(requests)}")
    print(f"Batch files created: {len(manifest_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
