#!/usr/bin/env python3
"""Generate revised pairing candidates for the remaining 70 Synthea seeds."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


PAIR_FIELDS = [
    "pair_id",
    "case_id",
    "seed_id",
    "resource_type",
    "input_style",
    "input_text",
    "target_seed_file",
    "unsupported_fact_added",
    "missingness_preserved",
    "review_status",
    "notes",
]


@dataclass(frozen=True)
class Seed:
    seed_id: str
    resource_type: str
    resource_id: str
    source_file: str


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
class ReviewFlag:
    pair_id: str
    seed_id: str
    resource_type: str
    flag_type: str
    flag_reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate revised pairing candidates for the non-overlapping "
            "remaining-70 Synthea seeds."
        )
    )
    parser.add_argument(
        "--selected-file",
        type=Path,
        help="Path to the selected remaining-70 seed CSV or JSONL. If omitted, auto-discover under outputs/large/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Directory where remaining-70 pair outputs should be written.",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        help="Path to the extracted Large dataset directory used to resolve the existing target seed JSON.",
    )
    return parser.parse_args()


def resolve_selected_file(explicit: Path | None, output_dir: Path) -> Path:
    if explicit:
        return explicit.resolve()

    candidates = [
        output_dir / "synthea_remaining70_selected.csv",
        output_dir / "synthea_remaining70_selected.jsonl",
    ]
    for path in candidates:
        if path.exists():
            return path.resolve()

    patterns = [
        "*remaining70*selected*.csv",
        "*remaining70*selected*.jsonl",
        "*selected*remaining70*.csv",
        "*selected*remaining70*.jsonl",
    ]
    for pattern in patterns:
        matches = sorted(output_dir.glob(pattern))
        if matches:
            return matches[0].resolve()
    raise FileNotFoundError(
        "Could not locate an existing remaining-70 selected seed file under outputs/large/."
    )


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


def load_seeds(path: Path) -> list[Seed]:
    if path.suffix == ".jsonl":
        seeds = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                seeds.append(
                    Seed(
                        seed_id=row["selected_id"],
                        resource_type=row["resource_type"],
                        resource_id=row["resource_id"],
                        source_file=row["source_file"],
                    )
                )
        return seeds

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            Seed(
                seed_id=row["selected_id"],
                resource_type=row["resource_type"],
                resource_id=row["resource_id"],
                source_file=row["source_file"],
            )
            for row in reader
        ]


def resolve_seed_resources(seeds: list[Seed], dataset_dir: Path) -> dict[str, dict[str, Any]]:
    wanted: dict[str, set[str]] = defaultdict(set)
    for seed in seeds:
        wanted[seed.source_file].add(seed.resource_id)

    resolved: dict[str, dict[str, Any]] = {}
    for source_file, ids in wanted.items():
        path = dataset_dir / source_file
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                resource = json.loads(line)
                resource_id = resource.get("id")
                if resource_id in ids:
                    resolved[f"{resource.get('resourceType')}/{resource_id}"] = resource

    missing = [seed.seed_id for seed in seeds if seed.seed_id not in resolved]
    if missing:
        raise RuntimeError(
            f"Failed to resolve {len(missing)} selected seeds from the dataset: {missing[:5]}"
        )
    return resolved


def date_only(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("T", 1)[0]


def clean_condition_label(text: str) -> str:
    return re.sub(r"\s*\((disorder|finding|situation)\)\s*$", "", text, flags=re.IGNORECASE).strip()


def first_coding(resource_part: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(resource_part, dict):
        return {}
    codings = resource_part.get("coding")
    if isinstance(codings, list) and codings:
        first = codings[0]
        if isinstance(first, dict):
            return first
    return {}


def observation_label(resource: dict[str, Any]) -> str:
    code = resource.get("code", {})
    coding = first_coding(code)
    return code.get("text") or coding.get("display") or coding.get("code") or "Observation"


def observation_value_and_unit(resource: dict[str, Any]) -> tuple[str, str]:
    quantity = resource.get("valueQuantity", {})
    value = quantity.get("value")
    unit = quantity.get("unit") or quantity.get("code") or ""
    return str(value), str(unit)


def patient_name(resource: dict[str, Any]) -> str | None:
    names = resource.get("name")
    if not isinstance(names, list):
        return None
    for name in names:
        if not isinstance(name, dict):
            continue
        given = " ".join(name.get("given", [])) if isinstance(name.get("given"), list) else ""
        family = name.get("family", "")
        full = " ".join(part for part in [given, family] if part).strip()
        if full:
            return full
    return None


def patient_location(resource: dict[str, Any]) -> str | None:
    addresses = resource.get("address")
    if not isinstance(addresses, list) or not addresses:
        return None
    first = addresses[0]
    if not isinstance(first, dict):
        return None
    parts = [first.get("city"), first.get("state"), first.get("country")]
    clean = [part for part in parts if part]
    if clean:
        return ", ".join(clean)
    return None


def patient_marital_status(resource: dict[str, Any]) -> str | None:
    status = resource.get("maritalStatus")
    coding = first_coding(status if isinstance(status, dict) else {})
    if isinstance(status, dict) and status.get("text"):
        return status["text"]
    return coding.get("display")


def patient_deceased_date(resource: dict[str, Any]) -> str | None:
    return date_only(resource.get("deceasedDateTime"))


def patient_is_deceased(resource: dict[str, Any]) -> bool:
    if resource.get("deceasedDateTime"):
        return True
    return bool(resource.get("deceasedBoolean"))


def condition_text(resource: dict[str, Any]) -> str:
    code = resource.get("code", {})
    coding = first_coding(code)
    return code.get("text") or coding.get("display") or coding.get("code") or "Condition"


def condition_status(resource: dict[str, Any]) -> str | None:
    status = resource.get("clinicalStatus")
    coding = first_coding(status if isinstance(status, dict) else {})
    return coding.get("code") or coding.get("display")


def verification_status(resource: dict[str, Any]) -> str | None:
    status = resource.get("verificationStatus")
    coding = first_coding(status if isinstance(status, dict) else {})
    return coding.get("code") or coding.get("display")


def bool_str(value: bool) -> str:
    return "true" if value else "false"


def pair(
    case_id: str,
    seed: Seed,
    input_style: str,
    input_text: str,
    notes: str,
    review_status: str = "ready",
) -> PairCandidate:
    return PairCandidate(
        pair_id=f"{case_id}__{input_style}",
        case_id=case_id,
        seed_id=seed.seed_id,
        resource_type=seed.resource_type,
        input_style=input_style,
        input_text=input_text,
        target_seed_file=f"sample-bulk-fhir-datasets-1000-patients/{seed.source_file}",
        unsupported_fact_added=False,
        missingness_preserved=True,
        review_status=review_status,
        notes=notes,
    )


def generate_observation_pairs(
    seed: Seed,
    resource: dict[str, Any],
    case_id: str,
) -> list[PairCandidate]:
    label = observation_label(resource)
    value, unit = observation_value_and_unit(resource)
    status = resource.get("status")
    category = None
    categories = resource.get("category")
    if isinstance(categories, list) and categories:
        category = first_coding(categories[0]).get("display")
    observed_date = date_only(resource.get("effectiveDateTime") or resource.get("issued"))

    concise = f"{label}: {value} {unit}."
    if status:
        concise += f" Status: {status}."
    if observed_date:
        concise += f" Date: {observed_date}."

    semi = f"Observation={label}; Result={value} {unit}"
    if category:
        semi += f"; Category={category}"
    if status:
        semi += f"; Status={status}"
    if observed_date:
        semi += f"; Date={observed_date}"

    shorthand = f"{label} {value} {unit}"
    if observed_date:
        shorthand += f" on {observed_date}"
    if status:
        shorthand += f", status {status}"

    return [
        pair(
            case_id,
            seed,
            "concise_clinical",
            concise,
            "Uses only Observation code, numeric value, unit, status, and date from the seed resource.",
        ),
        pair(
            case_id,
            seed,
            "semi_structured",
            semi,
            "Uses only Observation code, numeric value, unit, category, status, and date from the seed resource.",
        ),
        pair(
            case_id,
            seed,
            "shorthand_note",
            shorthand,
            "Short clinical note using only the Observation label, numeric value, unit, and optional seed date/status.",
        ),
    ]


def generate_patient_pairs(
    seed: Seed,
    resource: dict[str, Any],
    case_id: str,
) -> tuple[list[PairCandidate], bool]:
    name = patient_name(resource)
    gender = resource.get("gender")
    birth_date = resource.get("birthDate")
    location = patient_location(resource)
    marital = patient_marital_status(resource)
    deceased_date = patient_deceased_date(resource)
    deceased = patient_is_deceased(resource)

    concise_parts = []
    if name:
        concise_parts.append(f"Patient {name}.")
    if gender:
        concise_parts.append(f"Gender: {gender}.")
    if birth_date:
        concise_parts.append(f"DOB: {birth_date}.")
    if location:
        concise_parts.append(f"Location: {location}.")
    if marital:
        concise_parts.append(f"Marital status: {marital}.")
    if deceased_date:
        concise_parts.append(f"Deceased: yes. Deceased date: {deceased_date}.")
    elif deceased:
        concise_parts.append("Deceased: yes.")
    concise = " ".join(concise_parts)

    semi_parts = []
    if name:
        semi_parts.append(f"Name: {name}")
    if gender:
        semi_parts.append(f"Gender: {gender}")
    if birth_date:
        semi_parts.append(f"DOB: {birth_date}")
    if location:
        semi_parts.append(f"Location: {location}")
    if marital:
        semi_parts.append(f"Marital status: {marital}")
    if deceased_date:
        semi_parts.append("Deceased: yes")
        semi_parts.append(f"DeceasedDate: {deceased_date}")
    elif deceased:
        semi_parts.append("Deceased: yes")
    semi = " | ".join(semi_parts)

    review_status = "needs_manual_review" if deceased else "ready"
    pairs = [
        pair(
            case_id,
            seed,
            "concise_clinical",
            concise,
            "Uses only explicit Patient demographics and identity fields from the seed resource, including deceased status when present.",
            review_status=review_status,
        ),
        pair(
            case_id,
            seed,
            "semi_structured",
            semi,
            "Uses only explicit Patient demographics and identity fields from the seed resource, including deceased status when present.",
            review_status=review_status,
        ),
    ]

    if not deceased:
        shorthand_parts = []
        if name:
            shorthand_parts.append(name)
        if gender:
            shorthand_parts.append(gender)
        if birth_date:
            shorthand_parts.append(f"DOB {birth_date}")
        if location:
            shorthand_parts.append(location)
        shorthand = "Pt " + ", ".join(shorthand_parts)
        if marital:
            shorthand += f", marital status {marital}"
        pairs.append(
            pair(
                case_id,
                seed,
                "shorthand_note",
                shorthand,
                "Short demographic note using only explicit Patient seed fields.",
            )
        )

    return pairs, deceased


def generate_condition_pairs(
    seed: Seed,
    resource: dict[str, Any],
    case_id: str,
) -> list[PairCandidate]:
    raw_label = condition_text(resource)
    label = clean_condition_label(raw_label)
    clinical = condition_status(resource)
    verification = verification_status(resource)
    onset = date_only(resource.get("onsetDateTime"))
    abatement = date_only(resource.get("abatementDateTime"))
    recorded = date_only(resource.get("recordedDate"))

    concise_parts = [f"Condition: {label}."]
    if clinical:
        concise_parts.append(f"Clinical status: {clinical}.")
    if verification:
        concise_parts.append(f"Verification: {verification}.")
    if onset:
        concise_parts.append(f"Onset: {onset}.")
    if abatement:
        concise_parts.append(f"Abatement: {abatement}.")
    if recorded:
        concise_parts.append(f"Recorded: {recorded}.")
    concise = " ".join(concise_parts)

    semi_parts = [f"Condition={label}"]
    if clinical:
        semi_parts.append(f"ClinicalStatus={clinical}")
    if verification:
        semi_parts.append(f"Verification={verification}")
    if onset:
        semi_parts.append(f"Onset={onset}")
    if abatement:
        semi_parts.append(f"Abatement={abatement}")
    if recorded:
        semi_parts.append(f"Recorded={recorded}")
    semi = "; ".join(semi_parts)

    return [
        pair(
            case_id,
            seed,
            "concise_clinical",
            concise,
            "Uses only the Condition text, status fields, and explicit seed dates.",
        ),
        pair(
            case_id,
            seed,
            "semi_structured",
            semi,
            "Uses only the Condition text, status fields, and explicit seed dates.",
        ),
    ]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def build_pairs(
    seeds: list[Seed],
    resources: dict[str, dict[str, Any]],
) -> tuple[list[PairCandidate], list[ReviewFlag], list[str], int]:
    pairs: list[PairCandidate] = []
    flags: list[ReviewFlag] = []
    deceased_seed_ids: list[str] = []
    counters: Counter[str] = Counter()
    condition_restricted_count = 0

    for seed in seeds:
        counters[seed.resource_type] += 1
        case_id = f"remaining70-{seed.resource_type.lower()}-{counters[seed.resource_type]:03d}"
        resource = resources[seed.seed_id]

        if seed.resource_type == "Observation":
            seed_pairs = generate_observation_pairs(seed, resource, case_id)
        elif seed.resource_type == "Patient":
            seed_pairs, deceased = generate_patient_pairs(seed, resource, case_id)
            if deceased:
                deceased_seed_ids.append(seed.seed_id)
                for pair_candidate in seed_pairs:
                    flags.append(
                        ReviewFlag(
                            pair_id=pair_candidate.pair_id,
                            seed_id=seed.seed_id,
                            resource_type=seed.resource_type,
                            flag_type="deceased_status_risk",
                            flag_reason=(
                                "Patient seed includes deceased status; confirm every generated variant preserves "
                                "the clinically important deceased signal from the target JSON."
                            ),
                        )
                    )
        else:
            seed_pairs = generate_condition_pairs(seed, resource, case_id)
            condition_restricted_count += 1

        pairs.extend(seed_pairs)

        for idx, left in enumerate(seed_pairs):
            for right in seed_pairs[idx + 1 :]:
                if similarity(left.input_text, right.input_text) > 0.92:
                    flags.append(
                        ReviewFlag(
                            pair_id=right.pair_id,
                            seed_id=seed.seed_id,
                            resource_type=seed.resource_type,
                            flag_type="near_duplicate_variant",
                            flag_reason="Variant wording is very close to another variant for the same seed.",
                        )
                    )

    deduped_flags: list[ReviewFlag] = []
    seen = set()
    for flag in flags:
        key = (flag.pair_id, flag.flag_type, flag.flag_reason)
        if key in seen:
            continue
        seen.add(key)
        deduped_flags.append(flag)

    return pairs, deduped_flags, sorted(set(deceased_seed_ids)), condition_restricted_count


def write_pairs_jsonl(path: Path, pairs: list[PairCandidate]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for pair_candidate in pairs:
            handle.write(json.dumps(pair_candidate.__dict__, ensure_ascii=True) + "\n")


def write_pairs_csv(path: Path, pairs: list[PairCandidate]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAIR_FIELDS)
        writer.writeheader()
        for pair_candidate in pairs:
            writer.writerow(
                {
                    **pair_candidate.__dict__,
                    "unsupported_fact_added": bool_str(pair_candidate.unsupported_fact_added),
                    "missingness_preserved": bool_str(pair_candidate.missingness_preserved),
                }
            )


def write_review_flags(path: Path, flags: list[ReviewFlag]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pair_id", "seed_id", "resource_type", "flag_type", "flag_reason"],
        )
        writer.writeheader()
        for flag in flags:
            writer.writerow(flag.__dict__)


def write_summary(
    path: Path,
    seeds: list[Seed],
    pairs: list[PairCandidate],
    flags: list[ReviewFlag],
    deceased_seed_ids: list[str],
    condition_restricted_count: int,
) -> None:
    variants_by_type = Counter(pair_candidate.resource_type for pair_candidate in pairs)
    variants_by_style = Counter(pair_candidate.input_style for pair_candidate in pairs)
    manual_review_seeds = sorted({flag.seed_id for flag in flags})
    patient_seed_count = sum(seed.resource_type == "Patient" for seed in seeds)

    lines = [
        "# Synthea Remaining 70 Pair Generation Summary",
        "",
        "## Totals",
        "",
        f"- Total new seeds processed: {len(seeds)}",
        f"- Total candidate pairs generated: {len(pairs)}",
        "",
        "## Variants by Resource Type",
        "",
    ]
    for resource_type in ("Patient", "Observation", "Condition"):
        lines.append(f"- {resource_type}: {variants_by_type[resource_type]}")

    lines.extend(["", "## Variants by Input Style", ""])
    for input_style, count in sorted(variants_by_style.items()):
        lines.append(f"- {input_style}: {count}")

    lines.extend(
        [
            "",
            "## Revised Rules vs the Earlier Pilot",
            "",
            "- Observation generation stayed numeric-first and still uses `concise_clinical`, `semi_structured`, and `shorthand_note`.",
            "- Patient generation now explicitly preserves deceased status whenever it appears in the target seed; shorthand variants were suppressed for deceased patients rather than risk silent omission.",
            "- Condition generation was tightened to `concise_clinical` plus `semi_structured` by default; routine `patient_utterance` generation was removed for this round.",
            "- `mildly_noisy_or_incomplete` was disabled entirely for this step.",
            "",
            "## Deceased-Status Handling",
            "",
            f"- Patient seeds containing `deceasedDateTime`: {len(deceased_seed_ids)} / {patient_seed_count}",
            "- Every deceased Patient pair was flagged with `deceased_status_risk` for first-pass review.",
            "- Deceased Patient seeds were limited to fuller variants that can explicitly carry the deceased signal rather than a shorthand form that might drop it.",
            "",
            "## Condition Style Restrictions",
            "",
            f"- Condition seeds restricted away from `patient_utterance`: {condition_restricted_count}",
            "- No `patient_utterance` exceptions were used in this batch.",
            "",
            "## Seeds Flagged for Manual Review",
            "",
        ]
    )

    if manual_review_seeds:
        for seed_id in manual_review_seeds:
            lines.append(f"- {seed_id}")
    else:
        lines.append("- None")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = Path.cwd()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_file = resolve_selected_file(args.selected_file, output_dir)
    dataset_dir = resolve_dataset_dir(args.dataset_dir, repo_root)

    seeds = load_seeds(selected_file)
    resources = resolve_seed_resources(seeds, dataset_dir)
    pairs, flags, deceased_seed_ids, condition_restricted_count = build_pairs(seeds, resources)

    write_pairs_jsonl(output_dir / "synthea_remaining70_pair_candidates.jsonl", pairs)
    write_pairs_csv(output_dir / "synthea_remaining70_pair_candidates.csv", pairs)
    write_review_flags(output_dir / "synthea_remaining70_pair_review_flags.csv", flags)
    write_summary(
        output_dir / "synthea_remaining70_pair_generation_summary.md",
        seeds,
        pairs,
        flags,
        deceased_seed_ids,
        condition_restricted_count,
    )

    print("Remaining-70 pair candidate generation complete.")
    print(f"Selected seeds processed: {len(seeds)}")
    print(f"Candidate pairs generated: {len(pairs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
