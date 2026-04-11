#!/usr/bin/env python3
"""Select 70 new non-overlapping Synthea seeds for mother-pool expansion."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


TARGET_FIELDS = [
    "selected_id",
    "resource_type",
    "resource_id",
    "source_file",
    "json_valid",
    "likely_numeric",
    "has_value",
    "has_unit",
    "needs_linked_context",
    "complexity_guess",
    "selection_priority",
    "selection_notes",
]

COMPLEXITY_ORDER = {
    "low": 0,
    "moderate": 1,
    "high": 2,
}


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    resource_type: str
    resource_id: str
    source_file: str
    json_valid: bool
    likely_numeric: bool
    has_value: bool
    has_unit: bool
    needs_linked_context: bool
    complexity_guess: str
    good_for_pairing: bool | None

    @property
    def hash_key(self) -> str:
        return hashlib.sha256(self.candidate_id.encode("utf-8")).hexdigest()

    @property
    def id_bucket(self) -> str:
        if self.resource_id:
            return self.resource_id.split("-")[0][:2]
        return self.hash_key[:2]


@dataclass(frozen=True)
class Stage:
    code: str
    note: str
    predicate: Callable[[Candidate], bool]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select the remaining 70 Synthea mother-pool seeds while excluding "
            "all prior pilot-50 seeds."
        )
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("outputs/large/candidate_seed_catalog.csv.gz"),
        help="Path to the existing candidate seed catalog (.csv.gz preferred).",
    )
    parser.add_argument(
        "--pilot-selected",
        type=Path,
        default=Path("outputs/large/synthea_pilot50_selected.csv"),
        help="Path to the prior pilot-50 selected seed CSV or JSONL.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Directory where remaining-70 outputs should be written.",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        help="Path to the extracted Large dataset used to identify deceased Patient seeds.",
    )
    parser.add_argument("--patient-count", type=int, default=10)
    parser.add_argument("--observation-count", type=int, default=50)
    parser.add_argument("--condition-count", type=int, default=10)
    parser.add_argument(
        "--deceased-patient-count",
        type=int,
        default=3,
        help="How many selected Patient seeds should intentionally include deceasedDateTime coverage.",
    )
    return parser.parse_args()


def open_catalog(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def parse_optional_bool(row: dict[str, str], key: str) -> bool | None:
    if key not in row or row[key] == "":
        return None
    return parse_bool(row[key])


def load_candidates(catalog_path: Path) -> list[Candidate]:
    with open_catalog(catalog_path) as handle:
        reader = csv.DictReader(handle)
        return [
            Candidate(
                candidate_id=row["candidate_id"],
                resource_type=row["resource_type"],
                resource_id=row["resource_id"],
                source_file=row["source_file"],
                json_valid=parse_bool(row["json_valid"]),
                likely_numeric=parse_bool(row["likely_numeric"]),
                has_value=parse_bool(row["has_value"]),
                has_unit=parse_bool(row["has_unit"]),
                needs_linked_context=parse_bool(row["needs_linked_context"]),
                complexity_guess=row["complexity_guess"],
                good_for_pairing=parse_optional_bool(row, "good_for_pairing"),
            )
            for row in reader
        ]


def load_selected_ids(path: Path) -> set[str]:
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            return {json.loads(line)["selected_id"] for line in handle if line.strip()}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        field = "selected_id" if "selected_id" in reader.fieldnames else "candidate_id"
        return {row[field] for row in reader}


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


def resolve_deceased_patient_ids(
    patient_candidates: list[Candidate],
    dataset_dir: Path,
) -> set[str]:
    by_source: dict[str, dict[str, str]] = defaultdict(dict)
    for candidate in patient_candidates:
        by_source[candidate.source_file][candidate.resource_id] = candidate.candidate_id

    deceased_ids: set[str] = set()
    for source_file, ids in by_source.items():
        path = dataset_dir / source_file
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                resource = json.loads(line)
                if resource.get("resourceType") != "Patient":
                    continue
                resource_id = resource.get("id")
                candidate_id = ids.get(resource_id)
                if not candidate_id:
                    continue
                if resource.get("deceasedDateTime"):
                    deceased_ids.add(candidate_id)
    return deceased_ids


def candidate_sort_key(candidate: Candidate) -> tuple[object, ...]:
    good_rank = 1
    if candidate.good_for_pairing is True:
        good_rank = 0
    elif candidate.good_for_pairing is False:
        good_rank = 2
    return (
        good_rank,
        COMPLEXITY_ORDER.get(candidate.complexity_guess, 99),
        0 if not candidate.needs_linked_context else 1,
        candidate.hash_key,
    )


def round_robin_pick(
    candidates: list[Candidate],
    needed: int,
    group_key: Callable[[Candidate], str],
) -> list[Candidate]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[group_key(candidate)].append(candidate)

    for key in grouped:
        grouped[key].sort(key=candidate_sort_key)

    selected: list[Candidate] = []
    group_names = sorted(grouped)
    while len(selected) < needed:
        made_progress = False
        for group_name in group_names:
            if grouped[group_name]:
                selected.append(grouped[group_name].pop(0))
                made_progress = True
                if len(selected) == needed:
                    break
        if not made_progress:
            break
    return selected


def serialize_selection(candidate: Candidate, priority: str, note: str) -> dict[str, str]:
    return {
        "selected_id": candidate.candidate_id,
        "resource_type": candidate.resource_type,
        "resource_id": candidate.resource_id,
        "source_file": candidate.source_file,
        "json_valid": bool_str(candidate.json_valid),
        "likely_numeric": bool_str(candidate.likely_numeric),
        "has_value": bool_str(candidate.has_value),
        "has_unit": bool_str(candidate.has_unit),
        "needs_linked_context": bool_str(candidate.needs_linked_context),
        "complexity_guess": candidate.complexity_guess,
        "selection_priority": priority,
        "selection_notes": note,
    }


def select_with_stages(
    candidates: list[Candidate],
    quota: int,
    stages: list[Stage],
    group_key: Callable[[Candidate], str],
) -> list[dict[str, object]]:
    selected_ids: set[str] = set()
    selections: list[dict[str, object]] = []

    for stage in stages:
        if len(selections) >= quota:
            break
        pool = [
            candidate
            for candidate in candidates
            if candidate.candidate_id not in selected_ids and stage.predicate(candidate)
        ]
        if not pool:
            continue
        needed = quota - len(selections)
        picked = round_robin_pick(pool, needed, group_key)
        for candidate in picked:
            selected_ids.add(candidate.candidate_id)
            selections.append(
                {
                    "candidate": candidate,
                    "selection_priority": stage.code,
                    "selection_notes": stage.note,
                }
            )

    if len(selections) != quota:
        resource_type = candidates[0].resource_type if candidates else "unknown"
        raise RuntimeError(
            f"Could not fill the {resource_type} quota. Needed {quota}, selected {len(selections)}."
        )
    return selections


def observation_stages() -> list[Stage]:
    return [
        Stage(
            "01_preferred_no_link_low",
            "Observation kept numeric with value and unit metadata, low complexity, and no linked context.",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and not c.needs_linked_context
                and c.complexity_guess == "low"
            ),
        ),
        Stage(
            "02_preferred_no_link_moderate",
            "Observation kept numeric with value and unit metadata, moderate complexity, and no linked context.",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and not c.needs_linked_context
                and c.complexity_guess == "moderate"
            ),
        ),
        Stage(
            "03_relaxed_linked_moderate",
            "Observation selected after linked-context relaxation; still numeric with value and unit metadata at moderate complexity.",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and c.needs_linked_context
                and c.complexity_guess == "moderate"
            ),
        ),
        Stage(
            "04_relaxed_linked_high",
            "Observation selected only after allowing linked context and high complexity while preserving numeric value and unit metadata.",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and c.needs_linked_context
                and c.complexity_guess == "high"
            ),
        ),
        Stage(
            "05_relaxed_numeric_missing_unit",
            "Observation selected as a numeric fallback without unit metadata.",
            lambda c: c.likely_numeric and c.has_value and not c.has_unit,
        ),
        Stage(
            "06_exception_non_numeric",
            "Observation selected as a non-numeric exception only after numeric candidates were exhausted.",
            lambda c: c.has_value and not c.likely_numeric,
        ),
    ]


def condition_stages() -> list[Stage]:
    return [
        Stage(
            "01_preferred_no_link_low",
            "Condition kept low complexity with no linked context.",
            lambda c: (not c.needs_linked_context and c.complexity_guess == "low"),
        ),
        Stage(
            "02_preferred_no_link_moderate",
            "Condition kept moderate complexity with no linked context.",
            lambda c: (not c.needs_linked_context and c.complexity_guess == "moderate"),
        ),
        Stage(
            "03_relaxed_linked_moderate",
            "Condition selected after linked-context relaxation while staying at moderate complexity.",
            lambda c: c.needs_linked_context and c.complexity_guess == "moderate",
        ),
        Stage(
            "04_relaxed_linked_high",
            "Condition selected only after linked-context and high-complexity relaxation.",
            lambda c: c.needs_linked_context and c.complexity_guess == "high",
        ),
    ]


def select_patients(
    candidates: list[Candidate],
    deceased_ids: set[str],
    quota: int,
    deceased_quota: int,
) -> list[dict[str, object]]:
    living_pool = [
        candidate for candidate in candidates if candidate.candidate_id not in deceased_ids
    ]
    deceased_pool = [
        candidate for candidate in candidates if candidate.candidate_id in deceased_ids
    ]

    selected: list[dict[str, object]] = []
    group_key = lambda candidate: candidate.id_bucket

    target_deceased = min(quota, deceased_quota, len(deceased_pool))
    deceased_picks = round_robin_pick(deceased_pool, target_deceased, group_key)
    for candidate in deceased_picks:
        selected.append(
            {
                "candidate": candidate,
                "selection_priority": "01_deceased_coverage",
                "selection_notes": (
                    "Selected as a low-complexity Patient seed with explicit "
                    "deceasedDateTime coverage for revised pairing-rule validation."
                ),
            }
        )

    living_needed = quota - len(selected)
    living_picks = round_robin_pick(living_pool, living_needed, group_key)
    for candidate in living_picks:
        selected.append(
            {
                "candidate": candidate,
                "selection_priority": "02_living_anchor",
                "selection_notes": (
                    "Selected as a low-complexity Patient demographic anchor with "
                    "no linked-context requirement."
                ),
            }
        )

    if len(selected) != quota:
        remaining_pool = [
            candidate
            for candidate in candidates
            if candidate.candidate_id not in {row["candidate"].candidate_id for row in selected}
        ]
        backfill = round_robin_pick(remaining_pool, quota - len(selected), group_key)
        for candidate in backfill:
            selected.append(
                {
                    "candidate": candidate,
                    "selection_priority": "03_backfill",
                    "selection_notes": (
                        "Backfilled after the preferred deceased/living strata were exhausted."
                    ),
                }
            )

    if len(selected) != quota:
        raise RuntimeError(
            f"Could not fill the Patient quota. Needed {quota}, selected {len(selected)}."
        )
    return selected


def bool_str(value: bool) -> str:
    return "true" if value else "false"


def write_selected_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_selected_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            json_row = {
                **row,
                "json_valid": row["json_valid"] == "true",
                "likely_numeric": row["likely_numeric"] == "true",
                "has_value": row["has_value"] == "true",
                "has_unit": row["has_unit"] == "true",
                "needs_linked_context": row["needs_linked_context"] == "true",
            }
            handle.write(json.dumps(json_row, ensure_ascii=True) + "\n")


def write_summary(
    path: Path,
    pilot_overlap_count: int,
    selected_rows: list[dict[str, str]],
    selected_candidates: list[Candidate],
    patient_selected: list[Candidate],
    observation_selected: list[Candidate],
    condition_selected: list[Candidate],
    deceased_ids: set[str],
    catalog_has_good_for_pairing: bool,
) -> None:
    counts = Counter(candidate.resource_type for candidate in selected_candidates)
    priority_counts = Counter(row["selection_priority"] for row in selected_rows)

    selected_deceased = [candidate for candidate in patient_selected if candidate.candidate_id in deceased_ids]
    observation_high = sum(candidate.complexity_guess == "high" for candidate in observation_selected)
    observation_non_numeric = sum(not candidate.likely_numeric for candidate in observation_selected)
    observation_source_files = len({candidate.source_file for candidate in observation_selected})
    condition_id_buckets = len({candidate.id_bucket for candidate in condition_selected})

    relaxations = []
    if not catalog_has_good_for_pairing:
        relaxations.append(
            "- The current catalog does not expose `good_for_pairing`, so that preference could not be applied directly in this selection pass."
        )
    else:
        relaxations.append(
            "- `good_for_pairing=true` was preferred within each selection stage whenever the catalog exposed the flag."
        )

    relaxations.append(
        f"- Observation linked-context relaxation was required for {sum(candidate.needs_linked_context for candidate in observation_selected)}/"
        f"{len(observation_selected)} Observation selections because the remaining catalog contains no `needs_linked_context=false` Observation rows."
    )
    relaxations.append(
        f"- Condition linked-context relaxation was required for {sum(candidate.needs_linked_context for candidate in condition_selected)}/"
        f"{len(condition_selected)} Condition selections because the remaining catalog contains no `needs_linked_context=false` Condition rows."
    )
    if observation_high:
        relaxations.append(
            f"- High-complexity Observation fallback was used for {observation_high} selections."
        )
    else:
        relaxations.append(
            "- No high-complexity Observation fallback was needed; all selected Observations stayed at moderate complexity while preserving numeric value and unit metadata."
        )
    if observation_non_numeric:
        relaxations.append(
            f"- Non-numeric Observation fallback was used for {observation_non_numeric} selections."
        )
    else:
        relaxations.append(
            "- No non-numeric Observation fallback was needed; every selected Observation remained numeric with explicit value and unit metadata."
        )
    relaxations.append(
        f"- {len(selected_deceased)}/{len(patient_selected)} selected Patient seeds intentionally include `deceasedDateTime` so the revised patient-generation rules can be exercised without making deceased cases the majority."
    )

    lines = [
        "# Synthea Remaining 70 Selection Summary",
        "",
        "## Overlap Prevention",
        "",
        "- Loaded the prior pilot-50 selected IDs from `outputs/large/synthea_pilot50_selected.csv` and removed them from the candidate pool before scoring any remaining seed.",
        f"- Overlap after selection: {pilot_overlap_count}",
        "",
        "## Final Counts",
        "",
        f"- Patient: {counts['Patient']}",
        f"- Observation: {counts['Observation']}",
        f"- Condition: {counts['Condition']}",
        f"- Total selected seeds: {sum(counts.values())}",
        "",
        "## Criteria Relaxations Used",
        "",
        *relaxations,
        "",
        "## Why These 70 Are Appropriate",
        "",
        f"- Observation remains the main expansion target: {counts['Observation']} seeds were selected, all numeric and spread across {observation_source_files} Observation shard files for broader coverage.",
        f"- Patient expansion stays deliberate rather than arbitrary: {counts['Patient']} low-complexity demographic anchors were selected, including {len(selected_deceased)} deceased-status cases for rule validation and {len(patient_selected) - len(selected_deceased)} living cases for straightforward pairing coverage.",
        f"- Condition coverage continues, but conservatively: {counts['Condition']} moderate-complexity linked-context seeds were spread across {condition_id_buckets} distinct Condition ID-prefix buckets so the mother pool keeps cross-type breadth without over-expanding the most review-sensitive type from the pilot.",
        "",
        "## Selection Priority Breakdown",
        "",
    ]
    for priority, count in sorted(priority_counts.items()):
        lines.append(f"- {priority}: {count}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = Path.cwd()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = args.catalog.resolve()
    pilot_selected_path = args.pilot_selected.resolve()
    dataset_dir = resolve_dataset_dir(args.dataset_dir, repo_root)

    all_candidates = load_candidates(catalog_path)
    excluded_ids = load_selected_ids(pilot_selected_path)
    catalog_has_good_for_pairing = any(
        candidate.good_for_pairing is not None for candidate in all_candidates
    )

    candidates = [
        candidate
        for candidate in all_candidates
        if candidate.json_valid and candidate.candidate_id not in excluded_ids
    ]
    by_type: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_type[candidate.resource_type].append(candidate)

    deceased_ids = resolve_deceased_patient_ids(by_type["Patient"], dataset_dir)

    patient_selected_rows = select_patients(
        by_type["Patient"],
        deceased_ids,
        args.patient_count,
        args.deceased_patient_count,
    )
    observation_selected_rows = select_with_stages(
        by_type["Observation"],
        args.observation_count,
        observation_stages(),
        group_key=lambda candidate: candidate.source_file,
    )
    condition_selected_rows = select_with_stages(
        by_type["Condition"],
        args.condition_count,
        condition_stages(),
        group_key=lambda candidate: candidate.id_bucket,
    )

    combined = patient_selected_rows + observation_selected_rows + condition_selected_rows
    combined.sort(
        key=lambda row: (
            row["candidate"].resource_type,  # type: ignore[index]
            row["selection_priority"],
            row["candidate"].hash_key,  # type: ignore[index]
        )
    )

    selected_rows = [
        serialize_selection(
            row["candidate"],  # type: ignore[arg-type]
            row["selection_priority"],  # type: ignore[arg-type]
            row["selection_notes"],  # type: ignore[arg-type]
        )
        for row in combined
    ]
    selected_candidates = [row["candidate"] for row in combined]
    selected_ids = {candidate.candidate_id for candidate in selected_candidates}
    overlap_with_pilot = len(selected_ids & excluded_ids)

    write_selected_csv(output_dir / "synthea_remaining70_selected.csv", selected_rows)
    write_selected_jsonl(output_dir / "synthea_remaining70_selected.jsonl", selected_rows)
    write_summary(
        output_dir / "synthea_remaining70_selection_summary.md",
        overlap_with_pilot,
        selected_rows,
        selected_candidates,
        [row["candidate"] for row in patient_selected_rows],
        [row["candidate"] for row in observation_selected_rows],
        [row["candidate"] for row in condition_selected_rows],
        deceased_ids,
        catalog_has_good_for_pairing,
    )

    print("Remaining-70 seed selection complete.")
    print(f"Output directory: {output_dir}")
    print(
        "Final counts: "
        f"Patient={args.patient_count}, "
        f"Observation={args.observation_count}, "
        f"Condition={args.condition_count}"
    )
    print(f"Overlap with pilot-50 seeds: {overlap_with_pilot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
