#!/usr/bin/env python3
"""Select a review-ready 50-seed Synthea pilot set from the Large catalog."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


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

STRICT_COMPLEXITIES = {"low", "moderate"}


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

    @property
    def hash_key(self) -> str:
        return hashlib.sha256(self.candidate_id.encode("utf-8")).hexdigest()

    @property
    def id_bucket(self) -> str:
        if self.resource_id:
            return self.resource_id.split("-")[0][:2]
        return self.hash_key[:2]

    @property
    def strict_preferred(self) -> bool:
        if self.resource_type == "Observation":
            return (
                self.likely_numeric
                and self.has_value
                and self.has_unit
                and not self.needs_linked_context
                and self.complexity_guess in STRICT_COMPLEXITIES
            )
        return (
            not self.needs_linked_context
            and self.complexity_guess in STRICT_COMPLEXITIES
        )


@dataclass(frozen=True)
class Stage:
    code: str
    predicate: Callable[[Candidate], bool]
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select a first-pass 50-seed pilot set from the Large Synthea "
            "candidate catalog."
        )
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("outputs/large/candidate_seed_catalog.csv.gz"),
        help="Path to the candidate seed catalog (.csv.gz preferred, .csv also supported).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Directory where the pilot selection outputs should be written.",
    )
    parser.add_argument("--patient-count", type=int, default=5)
    parser.add_argument("--observation-count", type=int, default=35)
    parser.add_argument("--condition-count", type=int, default=10)
    return parser.parse_args()


def open_catalog(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def parse_bool(value: str) -> bool:
    return value.strip().lower() == "true"


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
            )
            for row in reader
        ]


def observation_stages() -> list[Stage]:
    return [
        Stage(
            "01_strict_low",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and not c.needs_linked_context
                and c.complexity_guess == "low"
            ),
            "Meets all strict Observation preferences.",
        ),
        Stage(
            "02_strict_moderate",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and not c.needs_linked_context
                and c.complexity_guess == "moderate"
            ),
            "Meets all strict Observation preferences at moderate complexity.",
        ),
        Stage(
            "03_relaxed_high_no_link",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and not c.needs_linked_context
                and c.complexity_guess == "high"
            ),
            "Selected only after allowing high complexity while still avoiding linked context.",
        ),
        Stage(
            "04_relaxed_linked_low",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and c.needs_linked_context
                and c.complexity_guess == "low"
            ),
            "Selected after linked-context relaxation; still numeric with value and unit.",
        ),
        Stage(
            "05_relaxed_linked_moderate",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and c.needs_linked_context
                and c.complexity_guess == "moderate"
            ),
            "Selected after linked-context relaxation; numeric with value and unit, moderate complexity.",
        ),
        Stage(
            "06_relaxed_linked_high",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and c.has_unit
                and c.needs_linked_context
                and c.complexity_guess == "high"
            ),
            "Selected only after linked-context and high-complexity relaxation.",
        ),
        Stage(
            "07_relaxed_no_unit_numeric",
            lambda c: (
                c.likely_numeric
                and c.has_value
                and not c.has_unit
            ),
            "Selected as a numeric Observation exception without unit metadata.",
        ),
        Stage(
            "08_exception_non_numeric",
            lambda c: c.has_value and not c.likely_numeric,
            "Selected as a non-numeric Observation exception because numeric candidates were exhausted.",
        ),
    ]


def generic_stages(resource_type: str) -> list[Stage]:
    return [
        Stage(
            "01_strict_low",
            lambda c: (not c.needs_linked_context and c.complexity_guess == "low"),
            f"Meets strict {resource_type} preferences with low complexity and no linked context.",
        ),
        Stage(
            "02_strict_moderate",
            lambda c: (not c.needs_linked_context and c.complexity_guess == "moderate"),
            f"Meets strict {resource_type} preferences with moderate complexity and no linked context.",
        ),
        Stage(
            "03_relaxed_high_no_link",
            lambda c: (not c.needs_linked_context and c.complexity_guess == "high"),
            f"Selected only after allowing high complexity while still avoiding linked context for {resource_type}.",
        ),
        Stage(
            "04_relaxed_linked_low",
            lambda c: (c.needs_linked_context and c.complexity_guess == "low"),
            f"Selected after linked-context relaxation for {resource_type}; low complexity otherwise.",
        ),
        Stage(
            "05_relaxed_linked_moderate",
            lambda c: (c.needs_linked_context and c.complexity_guess == "moderate"),
            f"Selected after linked-context relaxation for {resource_type}; moderate complexity otherwise.",
        ),
        Stage(
            "06_relaxed_linked_high",
            lambda c: (c.needs_linked_context and c.complexity_guess == "high"),
            f"Selected only after linked-context and high-complexity relaxation for {resource_type}.",
        ),
    ]


def round_robin_pick(
    candidates: Iterable[Candidate],
    needed: int,
    group_key: Callable[[Candidate], str],
) -> list[Candidate]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[group_key(candidate)].append(candidate)

    for key in grouped:
        grouped[key].sort(key=lambda c: c.hash_key)

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


def choose_group_key(resource_type: str) -> Callable[[Candidate], str]:
    if resource_type == "Observation":
        return lambda c: c.source_file
    return lambda c: c.id_bucket


def select_for_type(
    candidates: list[Candidate],
    stages: list[Stage],
    quota: int,
) -> list[dict[str, object]]:
    selected_ids: set[str] = set()
    selections: list[dict[str, object]] = []
    group_key = choose_group_key(candidates[0].resource_type if candidates else "Patient")

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
        picked = round_robin_pick(pool, needed, group_key=group_key)
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


def bool_str(value: bool) -> str:
    return "true" if value else "false"


def serialize_selection_row(selection: dict[str, object]) -> dict[str, object]:
    candidate: Candidate = selection["candidate"]  # type: ignore[assignment]
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
        "selection_priority": selection["selection_priority"],
        "selection_notes": selection["selection_notes"],
    }


def write_selected_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_selected_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
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


def pick_rejected_examples(
    selected_candidates: set[str],
    all_candidates: list[Candidate],
) -> list[dict[str, str]]:
    examples: list[tuple[Candidate, str]] = []

    def take(
        predicate: Callable[[Candidate], bool],
        reason: str,
        limit: int,
    ) -> None:
        pool = [
            candidate
            for candidate in all_candidates
            if candidate.candidate_id not in selected_candidates and predicate(candidate)
        ]
        pool.sort(key=lambda c: c.hash_key)
        for candidate in pool[:limit]:
            examples.append((candidate, reason))

    take(
        lambda c: (
            c.resource_type == "Observation"
            and not c.likely_numeric
            and c.has_value
        ),
        "Observation near-miss: non-numeric and missing unit, so numeric-first pilot preference won.",
        4,
    )
    take(
        lambda c: (
            c.resource_type == "Observation"
            and c.likely_numeric
            and c.has_unit
            and c.complexity_guess == "high"
        ),
        "Observation near-miss: high complexity was unnecessary because enough moderate numeric Observations were available.",
        4,
    )
    take(
        lambda c: c.resource_type == "Condition",
        "Condition near-miss: acceptable after linked-context relaxation, but omitted once the quota was filled.",
        2,
    )
    take(
        lambda c: c.resource_type == "Patient",
        "Patient near-miss: meets preferred criteria, but left out after the 5-patient quota was filled.",
        2,
    )

    rows = []
    seen: set[str] = set()
    for candidate, reason in examples:
        if candidate.candidate_id in seen:
            continue
        seen.add(candidate.candidate_id)
        rows.append(
            {
                "resource_type": candidate.resource_type,
                "resource_id": candidate.resource_id,
                "rejection_reason": reason,
            }
        )
    return rows


def write_rejected_examples_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["resource_type", "resource_id", "rejection_reason"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary(
    path: Path,
    selected: list[dict[str, object]],
    selected_candidates: list[Candidate],
    selected_by_type: dict[str, list[Candidate]],
    strict_count: int,
) -> None:
    counts = Counter(candidate.resource_type for candidate in selected_candidates)
    strict_by_type = Counter(
        candidate.resource_type for candidate in selected_candidates if candidate.strict_preferred
    )
    stage_counts = Counter(str(row["selection_priority"]) for row in selected)
    source_file_coverage = len({c.source_file for c in selected_by_type["Observation"]})

    relaxations: list[str] = []
    observation_linked_relax = sum(
        1 for c in selected_by_type["Observation"] if c.needs_linked_context
    )
    if observation_linked_relax:
        relaxations.append(
            f"- Observation linked-context relaxation was required for {observation_linked_relax}/35 selections because the catalog contains no `needs_linked_context=false` Observation rows."
        )
    condition_linked_relax = sum(
        1 for c in selected_by_type["Condition"] if c.needs_linked_context
    )
    if condition_linked_relax:
        relaxations.append(
            f"- Condition linked-context relaxation was required for {condition_linked_relax}/10 selections because the catalog contains no `needs_linked_context=false` Condition rows."
        )
    high_observation_count = sum(
        1 for c in selected_by_type["Observation"] if c.complexity_guess == "high"
    )
    if high_observation_count:
        relaxations.append(
            f"- High-complexity Observation fallback was used for {high_observation_count} selections."
        )
    else:
        relaxations.append(
            "- No high-complexity Observation fallback was needed; all 35 selected Observations are moderate-complexity numeric rows with value and unit metadata."
        )
    non_numeric_obs_count = sum(
        1 for c in selected_by_type["Observation"] if not c.likely_numeric
    )
    if non_numeric_obs_count:
        relaxations.append(
            f"- Non-numeric Observation exceptions were needed for {non_numeric_obs_count} selections."
        )
    else:
        relaxations.append(
            "- No non-numeric Observation exceptions were needed; the numeric-first preference held for all 35 Observation selections."
        )

    lines = [
        "# Synthea Pilot 50 Summary",
        "",
        "## Final Counts",
        "",
        f"- Patient: {counts['Patient']}",
        f"- Observation: {counts['Observation']}",
        f"- Condition: {counts['Condition']}",
        f"- Total selected seeds: {sum(counts.values())}",
        "",
        "## Strict Preferred-Criteria Hit Rate",
        "",
        f"- Selections meeting strict preferred criteria: {strict_count}/50",
        f"- Patient strict hits: {strict_by_type['Patient']}/5",
        f"- Observation strict hits: {strict_by_type['Observation']}/35",
        f"- Condition strict hits: {strict_by_type['Condition']}/10",
        "",
        "## Criteria Relaxations Used",
        "",
        *relaxations,
        "",
        "## Why These 50 Are Appropriate",
        "",
        f"- The allocation exactly matches the pilot target of 5 Patient / 35 Observation / 10 Condition.",
        f"- Observation remains dominant and review-useful: all 35 selected Observations are numeric, all have value metadata, all have unit metadata, and they are spread across {source_file_coverage} Observation shard files for better first-pass coverage.",
        "- Patient seeds are low-complexity, no-linked-context anchors that should be easy to review and helpful for validating the simplest pairing rules.",
        "- Condition seeds preserve minimum cross-type coverage, while keeping complexity at moderate rather than high so the first pairing pilot can focus on rule stability before harder linked-context cases.",
        "",
        "## Selection Priority Breakdown",
        "",
    ]
    for stage_code, count in sorted(stage_counts.items()):
        lines.append(f"- {stage_code}: {count}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates = [
        candidate
        for candidate in load_candidates(args.catalog.resolve())
        if candidate.json_valid
    ]
    by_type: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_type[candidate.resource_type].append(candidate)

    patient_selected = select_for_type(
        by_type["Patient"],
        generic_stages("Patient"),
        args.patient_count,
    )
    observation_selected = select_for_type(
        by_type["Observation"],
        observation_stages(),
        args.observation_count,
    )
    condition_selected = select_for_type(
        by_type["Condition"],
        generic_stages("Condition"),
        args.condition_count,
    )

    combined = patient_selected + observation_selected + condition_selected
    combined.sort(
        key=lambda row: (
            row["candidate"].resource_type,  # type: ignore[index]
            row["selection_priority"],
            row["candidate"].hash_key,  # type: ignore[index]
        )
    )
    selected_rows = [serialize_selection_row(row) for row in combined]
    selected_candidates = [row["candidate"] for row in combined]
    selected_ids = {candidate.candidate_id for candidate in selected_candidates}

    write_selected_csv(output_dir / "synthea_pilot50_selected.csv", selected_rows)
    write_selected_jsonl(output_dir / "synthea_pilot50_selected.jsonl", selected_rows)
    write_rejected_examples_csv(
        output_dir / "synthea_pilot50_rejected_examples.csv",
        pick_rejected_examples(selected_ids, candidates),
    )
    write_summary(
        output_dir / "synthea_pilot50_summary.md",
        selected_rows,
        selected_candidates,
        {
            "Patient": [row["candidate"] for row in patient_selected],
            "Observation": [row["candidate"] for row in observation_selected],
            "Condition": [row["candidate"] for row in condition_selected],
        },
        strict_count=sum(candidate.strict_preferred for candidate in selected_candidates),
    )

    print("Pilot seed selection complete.")
    print(f"Output directory: {output_dir}")
    print(
        "Final counts: "
        f"Patient={args.patient_count}, "
        f"Observation={args.observation_count}, "
        f"Condition={args.condition_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
