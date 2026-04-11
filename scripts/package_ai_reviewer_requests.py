#!/usr/bin/env python3
"""Package pair candidates into self-contained AI reviewer requests."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUEST_CSV_FIELDS = [
    "pair_id",
    "resource_type",
    "input_style",
    "reviewer_prompt",
]

RESULT_TEMPLATE_FIELDS = [
    "pair_id",
    "reviewer_name",
    "faithfulness",
    "unsupported_fact",
    "omission",
    "naturalness",
    "context_leakage",
    "flag_type",
    "short_rationale",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Package existing pair candidates into self-contained AI reviewer "
            "requests without changing the underlying pair content."
        )
    )
    parser.add_argument(
        "--pairs-file",
        type=Path,
        help="Path to the pair candidates JSONL or CSV. If omitted, auto-discover under outputs/large/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Directory where the reviewer request outputs should be written.",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        help="Path to the extracted Large dataset directory used to resolve the target seed JSON.",
    )
    return parser.parse_args()


def resolve_pairs_file(explicit: Path | None, output_dir: Path) -> Path:
    if explicit:
        return explicit.resolve()

    candidates = [
        output_dir / "synthea_pilot50_pair_candidates.jsonl",
        output_dir / "synthea_pilot50_pair_candidates.csv",
    ]
    for path in candidates:
        if path.exists():
            return path.resolve()
    raise FileNotFoundError("Could not locate synthea_pilot50_pair_candidates.jsonl or .csv under outputs/large/.")


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


def load_pair_candidates(path: Path) -> list[PairCandidate]:
    if path.suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
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
                        unsupported_fact_added=bool(data["unsupported_fact_added"]),
                        missingness_preserved=bool(data["missingness_preserved"]),
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
                unsupported_fact_added=row["unsupported_fact_added"].lower() == "true",
                missingness_preserved=row["missingness_preserved"].lower() == "true",
                review_status=row["review_status"],
                notes=row["notes"],
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


def build_reviewer_prompt(
    pair: PairCandidate,
    target_fhir_json: dict[str, Any],
) -> str:
    target_json_text = json.dumps(target_fhir_json, indent=2, ensure_ascii=True)

    return (
        "You are reviewing one candidate pairing example for a FHIR data generation project.\n\n"
        "Your task is to judge whether the input_text is a faithful natural-language rendering of the target_fhir_json.\n"
        "Judge only this single pair. Do not use outside knowledge. Do not assume facts from linked resources unless they are explicitly present in the target_fhir_json shown below.\n\n"
        "Return exactly one JSON object with these keys:\n"
        'pair_id, faithfulness, unsupported_fact, omission, naturalness, context_leakage, flag_type, short_rationale\n\n'
        "Use these output conventions:\n"
        '- faithfulness: integer 1-5 where 5 = fully faithful and 1 = clearly inconsistent\n'
        '- unsupported_fact: yes or no\n'
        '- omission: yes or no\n'
        '- naturalness: integer 1-5 where 5 = very natural and 1 = unusable\n'
        '- context_leakage: yes or no\n'
        '- flag_type: one of none, possible_hallucination, possible_omission, context_leakage, style_uncertainty, other\n'
        '- short_rationale: one short paragraph\n\n'
        f"Pair metadata:\n"
        f"- pair_id: {pair.pair_id}\n"
        f"- resource_type: {pair.resource_type}\n"
        f"- input_style: {pair.input_style}\n\n"
        "Input text to judge:\n"
        f"{pair.input_text}\n\n"
        "Target FHIR JSON:\n"
        f"{target_json_text}\n"
    )


def write_requests_jsonl(
    path: Path,
    pairs: list[PairCandidate],
    targets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("w", encoding="utf-8") as handle:
        for pair in pairs:
            target_fhir_json = targets[pair.seed_id]
            row = {
                "pair_id": pair.pair_id,
                "resource_type": pair.resource_type,
                "input_style": pair.input_style,
                "input_text": pair.input_text,
                "target_fhir_json": target_fhir_json,
                "reviewer_prompt": build_reviewer_prompt(pair, target_fhir_json),
            }
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return rows


def write_requests_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUEST_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "pair_id": row["pair_id"],
                    "resource_type": row["resource_type"],
                    "input_style": row["input_style"],
                    "reviewer_prompt": row["reviewer_prompt"],
                }
            )


def write_results_template_csv(path: Path, pair_ids: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_TEMPLATE_FIELDS)
        writer.writeheader()
        for pair_id in pair_ids:
            writer.writerow(
                {
                    "pair_id": pair_id,
                    "reviewer_name": "",
                    "faithfulness": "",
                    "unsupported_fact": "",
                    "omission": "",
                    "naturalness": "",
                    "context_leakage": "",
                    "flag_type": "",
                    "short_rationale": "",
                }
            )


def batching_recommendation(total_pairs: int, per_batch: int) -> str:
    batches = (total_pairs + per_batch - 1) // per_batch
    return f"{per_batch} pairs per batch, about {batches} batches total."


def write_batching_summary(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    prompt_lengths = {row["pair_id"]: len(row["reviewer_prompt"]) for row in rows}
    prompt_tokens_est = {pair_id: max(1, length // 4) for pair_id, length in prompt_lengths.items()}
    very_large = [
        (pair_id, prompt_lengths[pair_id], prompt_tokens_est[pair_id])
        for pair_id in sorted(prompt_lengths, key=prompt_lengths.get, reverse=True)
        if prompt_lengths[pair_id] > 14000 or prompt_tokens_est[pair_id] > 3500
    ]

    lines = [
        "# AI Review Batching Summary",
        "",
        f"- Total number of pairs packaged: {len(rows)}",
        f"- Median reviewer prompt length: {sorted(prompt_lengths.values())[len(prompt_lengths) // 2]:,} characters",
        f"- Maximum reviewer prompt length: {max(prompt_lengths.values()):,} characters",
        "",
        "## Suggested Batching Strategy for GPT-5.4 Thinking",
        "",
        f"- {batching_recommendation(len(rows), 20)}",
        "- Use the JSONL request file directly and keep one model response per request.",
        "- Leave extra headroom for longer target FHIR JSON payloads and richer rationales.",
        "",
        "## Suggested Batching Strategy for Claude 3.7 Sonnet",
        "",
        f"- {batching_recommendation(len(rows), 24)}",
        "- Prefer homogeneous batches by resource type when possible, especially for large Patient prompts.",
        "- Keep the CSV request export handy for spreadsheet-driven spot checks.",
        "",
        "## Suggested Batching Strategy for Gemini 2.5 Pro",
        "",
        f"- {batching_recommendation(len(rows), 28)}",
        "- Gemini can usually tolerate slightly larger mixed batches, but keep retries isolated at the request level.",
        "- Use the JSONL file as the source of truth for API submission.",
        "",
        "## Very Large Prompts",
        "",
    ]

    if very_large:
        lines.append("- The following prompts are large enough that you may want to isolate them into smaller batches or monitor truncation risk:")
        for pair_id, chars, tokens_est in very_large:
            lines.append(f"- {pair_id}: about {chars:,} chars (~{tokens_est:,} tokens)")
    else:
        lines.append("- No prompts crossed the large-prompt threshold used here (>14,000 chars or >3,500 estimated tokens).")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = Path.cwd()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pairs_file = resolve_pairs_file(args.pairs_file, output_dir)
    dataset_dir = resolve_dataset_dir(args.dataset_dir, repo_root)

    pairs = load_pair_candidates(pairs_file)
    targets = resolve_target_seed_json(pairs, dataset_dir)

    rows = write_requests_jsonl(output_dir / "ai_reviewer_requests.jsonl", pairs, targets)
    write_requests_csv(output_dir / "ai_reviewer_requests.csv", rows)
    write_results_template_csv(
        output_dir / "ai_reviewer_results_template.csv",
        [row["pair_id"] for row in rows],
    )
    write_batching_summary(output_dir / "ai_review_batching_summary.md", rows)

    print("AI reviewer request packaging complete.")
    print(f"Pairs packaged: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
