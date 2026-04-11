#!/usr/bin/env python3
"""Build fixed-size reviewer batch prompts from reviewer requests or pair candidates."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
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


INDEX_FIELDS = [
    "batch_id",
    "file_name",
    "start_pair_id",
    "end_pair_id",
    "item_count",
]

HIGH_RISK_FIELDS = [
    "pair_id",
    "seed_id",
    "resource_type",
    "input_style",
    "high_risk_reason",
]


@dataclass(frozen=True)
class PairCandidate:
    pair_id: str
    case_id: str
    seed_id: str
    resource_type: str
    input_style: str
    input_text: str
    target_seed_file: str
    unsupported_fact_added: bool
    missingness_preserved: bool
    review_status: str
    notes: str


@dataclass(frozen=True)
class ReviewerRequest:
    pair_id: str
    seed_id: str
    resource_type: str
    input_style: str
    input_text: str
    target_fhir_json: dict[str, Any]


@dataclass(frozen=True)
class ReviewFlag:
    pair_id: str
    seed_id: str
    resource_type: str
    flag_type: str
    flag_reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Group reviewer items into fixed-size batch prompts for external "
            "reviewer models while preserving the existing multi-item format."
        )
    )
    parser.add_argument(
        "--requests-file",
        type=Path,
        default=Path("outputs/large/ai_reviewer_requests.jsonl"),
        help="Path to ai_reviewer_requests.jsonl. Ignored if --pairs-file is provided.",
    )
    parser.add_argument(
        "--pairs-file",
        type=Path,
        help="Path to pair candidates JSONL or CSV. When provided, target FHIR JSON is resolved directly from the dataset.",
    )
    parser.add_argument(
        "--flags-file",
        type=Path,
        help="Optional pair review flags CSV used for high-risk tracking.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Base output directory. Batch prompts will be written under reviewer_batch_prompts/ inside it.",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        help="Path to the extracted Large dataset directory used to resolve target seed JSON when batching directly from pair candidates.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="Number of review items per batch prompt.",
    )
    parser.add_argument(
        "--batch-dir-name",
        default="reviewer_batch_prompts",
        help="Name of the batch prompt subdirectory under --output-dir.",
    )
    parser.add_argument(
        "--index-name",
        default="reviewer_batch_index.csv",
        help="Output file name for the batch index CSV under --output-dir.",
    )
    parser.add_argument(
        "--manifest-name",
        default="reviewer_batch_manifest.jsonl",
        help="Output file name for the batch manifest JSONL under --output-dir.",
    )
    parser.add_argument(
        "--notes-name",
        default="reviewer_batching_notes.md",
        help="Output file name for the batching notes markdown under --output-dir.",
    )
    parser.add_argument(
        "--high-risk-name",
        help="Optional output CSV file name for a high-risk subset under --output-dir.",
    )
    parser.add_argument(
        "--start-batch-number",
        type=int,
        help="Explicit batch number to start from. If omitted with --continue-numbering, the next available batch number is used.",
    )
    parser.add_argument(
        "--continue-numbering",
        action="store_true",
        help="Continue numbering after the highest existing review_batch_XXX.txt in the target batch directory.",
    )
    return parser.parse_args()


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return value.strip().lower() == "true"


def resolve_dataset_dir(explicit: Path | None, repo_root: Path) -> Path:
    if explicit:
        return explicit.resolve()

    guesses = [
        repo_root / "sample-bulk-fhir-datasets-1000-patients",
        repo_root.parent / "sample-bulk-fhir-datasets-1000-patients",
        repo_root.parent.parent / "sample-bulk-fhir-datasets-1000-patients",
    ]
    for guess in guesses:
        if guess.exists():
            return guess.resolve()
    raise FileNotFoundError(
        "Could not locate the extracted Large dataset directory. Pass --dataset-dir explicitly."
    )


def load_requests(path: Path) -> list[ReviewerRequest]:
    if path.suffix != ".jsonl":
        raise ValueError(
            "Batch generation from ai_reviewer_requests currently expects the JSONL export so input_text and target_fhir_json are preserved."
        )

    with path.open("r", encoding="utf-8") as handle:
        return [
            ReviewerRequest(
                pair_id=row["pair_id"],
                seed_id="",
                resource_type=row["resource_type"],
                input_style=row["input_style"],
                input_text=row["input_text"],
                target_fhir_json=row["target_fhir_json"],
            )
            for row in (json.loads(line) for line in handle if line.strip())
        ]


def load_pair_candidates(path: Path) -> list[PairCandidate]:
    if path.suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                data = json.loads(line)
                rows.append(
                    PairCandidate(
                        pair_id=data["pair_id"],
                        case_id=data["case_id"],
                        seed_id=data["seed_id"],
                        resource_type=data["resource_type"],
                        input_style=data["input_style"],
                        input_text=data["input_text"],
                        target_seed_file=data["target_seed_file"],
                        unsupported_fact_added=parse_bool(data["unsupported_fact_added"]),
                        missingness_preserved=parse_bool(data["missingness_preserved"]),
                        review_status=data["review_status"],
                        notes=data["notes"],
                    )
                )
        return rows

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            PairCandidate(
                pair_id=row["pair_id"],
                case_id=row["case_id"],
                seed_id=row["seed_id"],
                resource_type=row["resource_type"],
                input_style=row["input_style"],
                input_text=row["input_text"],
                target_seed_file=row["target_seed_file"],
                unsupported_fact_added=parse_bool(row["unsupported_fact_added"]),
                missingness_preserved=parse_bool(row["missingness_preserved"]),
                review_status=row["review_status"],
                notes=row["notes"],
            )
            for row in reader
        ]


def load_review_flags(path: Path | None) -> list[ReviewFlag]:
    if not path:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            ReviewFlag(
                pair_id=row["pair_id"],
                seed_id=row["seed_id"],
                resource_type=row["resource_type"],
                flag_type=row["flag_type"],
                flag_reason=row["flag_reason"],
            )
            for row in reader
        ]


def resolve_target_seed_json(
    pairs: list[PairCandidate],
    dataset_dir: Path,
) -> dict[str, dict[str, Any]]:
    wanted: dict[str, set[str]] = defaultdict(set)
    for pair in pairs:
        source_file = pair.target_seed_file.split("/", 1)[-1]
        resource_id = pair.seed_id.split("/", 1)[-1]
        wanted[source_file].add(resource_id)

    resolved: dict[str, dict[str, Any]] = {}
    for source_file, resource_ids in wanted.items():
        path = dataset_dir / source_file
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                resource = json.loads(line)
                resource_id = resource.get("id")
                resource_type = resource.get("resourceType")
                if resource_id in resource_ids:
                    resolved[f"{resource_type}/{resource_id}"] = resource

    missing = sorted({pair.seed_id for pair in pairs if pair.seed_id not in resolved})
    if missing:
        raise RuntimeError(
            f"Could not resolve {len(missing)} target seed resources from the Large dataset: {missing[:5]}"
        )
    return resolved


def requests_from_pairs(
    pairs: list[PairCandidate],
    targets: dict[str, dict[str, Any]],
) -> list[ReviewerRequest]:
    return [
        ReviewerRequest(
            pair_id=pair.pair_id,
            seed_id=pair.seed_id,
            resource_type=pair.resource_type,
            input_style=pair.input_style,
            input_text=pair.input_text,
            target_fhir_json=targets[pair.seed_id],
        )
        for pair in pairs
    ]


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


def chunked(items: list[Any], chunk_size: int) -> list[list[Any]]:
    return [
        items[index : index + chunk_size]
        for index in range(0, len(items), chunk_size)
    ]


def discover_existing_batch_numbers(batch_dir: Path) -> list[int]:
    pattern = re.compile(r"review_batch_(\d{3})\.txt$")
    numbers = []
    if not batch_dir.exists():
        return numbers
    for path in batch_dir.iterdir():
        match = pattern.match(path.name)
        if match:
            numbers.append(int(match.group(1)))
    return sorted(numbers)


def determine_start_batch_number(
    batch_dir: Path,
    explicit: int | None,
    continue_numbering: bool,
) -> int:
    if explicit is not None:
        return explicit
    if continue_numbering:
        existing = discover_existing_batch_numbers(batch_dir)
        return (existing[-1] + 1) if existing else 1
    return 1


def write_batch_files(
    batch_dir: Path,
    batches: list[list[ReviewerRequest]],
    start_batch_number: int,
) -> list[dict[str, Any]]:
    batch_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    for offset, requests in enumerate(batches):
        batch_number = start_batch_number + offset
        batch_id = f"review_batch_{batch_number:03d}"
        file_name = f"{batch_id}.txt"
        file_path = batch_dir / file_name
        if file_path.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing batch file: {file_path}"
            )
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
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
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


def write_notes(
    path: Path,
    requests: list[ReviewerRequest],
    manifest_rows: list[dict[str, Any]],
    batch_size: int,
    start_batch_number: int,
) -> None:
    full_batches = sum(1 for row in manifest_rows if row["item_count"] == batch_size)
    final_partial = bool(manifest_rows and manifest_rows[-1]["item_count"] != batch_size)
    prompt_lengths = sorted(
        (
            len(request.input_text) + len(json.dumps(request.target_fhir_json, ensure_ascii=True)),
            request.pair_id,
        )
        for request in requests
    )
    threshold = 5500
    flagged = [
        (pair_id, approx_len)
        for approx_len, pair_id in reversed(prompt_lengths)
        if approx_len >= threshold
    ]
    longest = prompt_lengths[-5:] if prompt_lengths else []

    lines = [
        "# Reviewer Batching Notes (Remaining 70)",
        "",
        f"- Total number of new pairs packaged: {len(requests)}",
        f"- Total number of new batches created: {len(manifest_rows)}",
        f"- Full 10-item batches created: {full_batches}",
        f"- Final partial batch present: {'yes' if final_partial else 'no'}",
        f"- Numbering continued after review_batch_015.txt: {'yes' if start_batch_number >= 16 else 'no'}",
        f"- First new batch file: review_batch_{start_batch_number:03d}.txt",
        "",
        "## Unusually Long Items",
        "",
    ]

    if flagged:
        for pair_id, approx_len in flagged:
            lines.append(
                f"- {pair_id}: approx {approx_len:,} chars across input_text + target_fhir_json"
            )
    else:
        lines.append(
            "- No items crossed the manual-attention threshold used here (>= 5,500 chars across input_text + target_fhir_json)."
        )

    if longest:
        lines.extend(["", "## Longest Items Snapshot", ""])
        for approx_len, pair_id in reversed(longest):
            lines.append(f"- {pair_id}: approx {approx_len:,} chars")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_high_risk_csv(
    path: Path,
    pairs: list[PairCandidate],
    targets: dict[str, dict[str, Any]],
    flags: list[ReviewFlag],
) -> None:
    flags_by_pair: dict[str, list[ReviewFlag]] = defaultdict(list)
    for flag in flags:
        flags_by_pair[flag.pair_id].append(flag)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HIGH_RISK_FIELDS)
        writer.writeheader()
        for pair in pairs:
            reasons: list[str] = []
            target = targets[pair.seed_id]

            if pair.resource_type == "Condition":
                reasons.append("Condition pair prioritised for early review.")

            if pair.resource_type == "Patient" and target.get("deceasedDateTime"):
                reasons.append("Patient target includes deceasedDateTime.")

            for flag in flags_by_pair.get(pair.pair_id, []):
                reasons.append(
                    f"Flagged during pair generation: {flag.flag_type} ({flag.flag_reason})"
                )

            deduped_reasons = list(dict.fromkeys(reasons))
            if deduped_reasons:
                writer.writerow(
                    {
                        "pair_id": pair.pair_id,
                        "seed_id": pair.seed_id,
                        "resource_type": pair.resource_type,
                        "input_style": pair.input_style,
                        "high_risk_reason": " ; ".join(deduped_reasons),
                    }
                )


def main() -> int:
    args = parse_args()
    repo_root = Path.cwd()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = output_dir / args.batch_dir_name

    if args.pairs_file:
        pairs = load_pair_candidates(args.pairs_file.resolve())
        dataset_dir = resolve_dataset_dir(args.dataset_dir, repo_root)
        targets = resolve_target_seed_json(pairs, dataset_dir)
        requests = requests_from_pairs(pairs, targets)
        flags = load_review_flags(args.flags_file.resolve() if args.flags_file else None)
    else:
        requests = load_requests(args.requests_file.resolve())
        pairs = []
        targets = {}
        flags = []

    batches = chunked(requests, args.batch_size)
    start_batch_number = determine_start_batch_number(
        batch_dir,
        args.start_batch_number,
        args.continue_numbering,
    )

    manifest_rows = write_batch_files(batch_dir, batches, start_batch_number)
    write_batch_index(output_dir / args.index_name, manifest_rows)
    write_batch_manifest(output_dir / args.manifest_name, manifest_rows)
    write_notes(
        output_dir / args.notes_name,
        requests,
        manifest_rows,
        args.batch_size,
        start_batch_number,
    )

    if args.high_risk_name:
        if not pairs:
            raise ValueError("--high-risk-name requires --pairs-file so seed-level context is available.")
        write_high_risk_csv(output_dir / args.high_risk_name, pairs, targets, flags)

    print("Reviewer batch prompt generation complete.")
    print(f"Pairs batched: {len(requests)}")
    print(f"Batch files created: {len(manifest_rows)}")
    print(f"Batch numbering started at: review_batch_{start_batch_number:03d}.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
