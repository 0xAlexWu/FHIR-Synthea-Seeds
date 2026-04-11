#!/usr/bin/env python3
"""Generate faithful input-text variants for the selected Synthea pilot-50 seeds."""

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
            "Generate pairing-pilot input variants for the already selected 50 "
            "Synthea pilot seeds."
        )
    )
    parser.add_argument(
        "--selected-file",
        type=Path,
        help="Path to the selected pilot-50 seed CSV or JSONL. If omitted, auto-discover under outputs/large/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/large"),
        help="Directory where pair-candidate outputs should be written.",
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
        output_dir / "synthea_pilot50_selected.csv",
        output_dir / "synthea_pilot50_selected.jsonl",
    ]
    for path in candidates:
        if path.exists():
            return path.resolve()

    patterns = [
        "*pilot50*selected*.csv",
        "*pilot50*selected*.jsonl",
        "*selected*50*.csv",
        "*selected*50*.jsonl",
    ]
    for pattern in patterns:
        matches = sorted(output_dir.glob(pattern))
        if matches:
            return matches[0].resolve()
    raise FileNotFoundError("Could not locate an existing pilot-50 selected seed file under outputs/large/.")


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
        raise RuntimeError(f"Failed to resolve {len(missing)} selected seeds from the dataset: {missing[:5]}")
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
    return (status or {}).get("text") if isinstance(status, dict) and status.get("text") else coding.get("display")


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


def generate_observation_pairs(seed: Seed, resource: dict[str, Any], case_id: str) -> list[PairCandidate]:
    label = observation_label(resource)
    value, unit = observation_value_and_unit(resource)
    status = resource.get("status")
    category = first_coding(resource.get("category", [{}])[0] if isinstance(resource.get("category"), list) and resource.get("category") else {}).get("display")
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
        pair(case_id, seed, "concise_clinical", concise, "Uses only Observation code, numeric value, unit, status, and date from the seed resource."),
        pair(case_id, seed, "semi_structured", semi, "Uses only Observation code, numeric value, unit, category, status, and date from the seed resource."),
        pair(case_id, seed, "shorthand_note", shorthand, "Short clinical note using only the Observation label, numeric value, unit, and optional seed date/status."),
    ]


def generate_patient_pairs(seed: Seed, resource: dict[str, Any], case_id: str) -> list[PairCandidate]:
    name = patient_name(resource)
    gender = resource.get("gender")
    birth_date = resource.get("birthDate")
    location = patient_location(resource)
    marital = patient_marital_status(resource)

    sentence_parts = []
    if name:
        sentence_parts.append(f"Patient {name}.")
    if gender:
        sentence_parts.append(f"Gender: {gender}.")
    if birth_date:
        sentence_parts.append(f"DOB: {birth_date}.")
    if location:
        sentence_parts.append(f"Location: {location}.")
    if marital:
        sentence_parts.append(f"Marital status: {marital}.")
    concise = " ".join(sentence_parts)

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
    semi = " | ".join(semi_parts)

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

    return [
        pair(case_id, seed, "concise_clinical", concise, "Uses only explicit Patient demographics and identity fields from the seed resource."),
        pair(case_id, seed, "semi_structured", semi, "Uses only explicit Patient demographics and identity fields from the seed resource."),
        pair(case_id, seed, "shorthand_note", shorthand, "Short demographic note using only explicit Patient seed fields."),
    ]


def condition_patient_utterance_allowed(label: str) -> bool:
    text = label.lower()
    blocked = ["review due", "employment"]
    return not any(term in text for term in blocked)


def generate_condition_pairs(seed: Seed, resource: dict[str, Any], case_id: str) -> tuple[list[PairCandidate], bool]:
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

    pairs = [
        pair(case_id, seed, "concise_clinical", concise, "Uses only the Condition text, status fields, and explicit seed dates."),
        pair(case_id, seed, "semi_structured", semi, "Uses only the Condition text, status fields, and explicit seed dates."),
    ]

    if condition_patient_utterance_allowed(label):
        if clinical and clinical.lower() == "resolved":
            utterance = f"I had {label.lower()}."
            if onset:
                utterance += f" It was noted on {onset}."
            if abatement:
                utterance += f" It had resolved by {abatement}."
        else:
            utterance = f"I have {label.lower()}."
            if onset:
                utterance += f" It was noted on {onset}."
        pairs.append(
            pair(
                case_id,
                seed,
                "patient_utterance",
                utterance,
                "First-person rendering of the Condition text with explicit seed dates/status only.",
                review_status="needs_manual_review",
            )
        )
        return pairs, False

    return pairs, True


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def build_pairs(
    seeds: list[Seed],
    resources: dict[str, dict[str, Any]],
) -> tuple[list[PairCandidate], list[ReviewFlag], list[str], list[str]]:
    pairs: list[PairCandidate] = []
    flags: list[ReviewFlag] = []
    sparse_seeds: list[str] = []
    noisy_seeds: list[str] = []
    counters: Counter[str] = Counter()

    for seed in seeds:
        counters[seed.resource_type] += 1
        case_id = f"pilot50-{seed.resource_type.lower()}-{counters[seed.resource_type]:03d}"
        resource = resources[seed.seed_id]

        if seed.resource_type == "Observation":
            seed_pairs = generate_observation_pairs(seed, resource, case_id)
        elif seed.resource_type == "Patient":
            seed_pairs = generate_patient_pairs(seed, resource, case_id)
        else:
            seed_pairs, sparse = generate_condition_pairs(seed, resource, case_id)
            if sparse:
                sparse_seeds.append(seed.seed_id)

        for pair_candidate in seed_pairs:
            pairs.append(pair_candidate)

        if len(seed_pairs) < 3:
            sparse_seeds.append(seed.seed_id)
            for pair_candidate in seed_pairs:
                flags.append(
                    ReviewFlag(
                        pair_id=pair_candidate.pair_id,
                        seed_id=seed.seed_id,
                        resource_type=seed.resource_type,
                        flag_type="sparse_seed",
                        flag_reason="This seed was kept to 2 variants because a third faithful style was not reliable enough.",
                    )
                )

        for pair_candidate in seed_pairs:
            if pair_candidate.input_style == "patient_utterance" and seed.resource_type == "Condition":
                flags.append(
                    ReviewFlag(
                        pair_id=pair_candidate.pair_id,
                        seed_id=seed.seed_id,
                        resource_type=seed.resource_type,
                        flag_type="style_uncertainty",
                        flag_reason="Condition text was translated into first-person phrasing; review tone and fidelity before downstream use.",
                    )
                )
            if pair_candidate.input_style == "mildly_noisy_or_incomplete":
                noisy_seeds.append(seed.seed_id)
                flags.append(
                    ReviewFlag(
                        pair_id=pair_candidate.pair_id,
                        seed_id=seed.seed_id,
                        resource_type=seed.resource_type,
                        flag_type="possible_omission",
                        flag_reason="Mildly noisy or incomplete variant intentionally omits some supported detail and should be checked first.",
                    )
                )

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

    deduped_sparse = sorted(set(sparse_seeds))
    deduped_noisy = sorted(set(noisy_seeds))
    deduped_flags: list[ReviewFlag] = []
    seen = set()
    for flag in flags:
        key = (flag.pair_id, flag.flag_type, flag.flag_reason)
        if key in seen:
            continue
        seen.add(key)
        deduped_flags.append(flag)
    return pairs, deduped_flags, deduped_sparse, deduped_noisy


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
    sparse_seeds: list[str],
    noisy_seeds: list[str],
    flags: list[ReviewFlag],
) -> None:
    variants_by_type = Counter(pair_candidate.resource_type for pair_candidate in pairs)
    variants_by_style = Counter(pair_candidate.input_style for pair_candidate in pairs)
    manual_review_seeds = sorted({flag.seed_id for flag in flags})

    lines = [
        "# Synthea Pilot 50 Pair Generation Summary",
        "",
        "## Totals",
        "",
        f"- Total seeds processed: {len(seeds)}",
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

    lines.extend(["", "## Seeds Too Sparse for 3 Variants", ""])
    if sparse_seeds:
        for seed_id in sparse_seeds:
            lines.append(f"- {seed_id}")
    else:
        lines.append("- None")

    lines.extend(["", "## Mildly Noisy or Incomplete Usage", ""])
    if noisy_seeds:
        for seed_id in noisy_seeds:
            lines.append(f"- {seed_id}")
    else:
        lines.append("- None used")

    lines.extend(["", "## Seeds Flagged for Manual Review", ""])
    if manual_review_seeds:
        for seed_id in manual_review_seeds:
            lines.append(f"- {seed_id}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Why These Pair Candidates Are Review-Ready",
            "",
            "- Every pair is tied directly to an existing selected seed and references the original Large dataset source file through `target_seed_file`.",
            "- No new target FHIR resources were generated; only source-faithful `input_text` variants were created.",
            "- Observation variants preserve the core numeric result and unit in every generated variant.",
            "- Patient variants use only explicit demographic or identity-related facts from the seed resource.",
            "- Condition variants stay within the coded condition text, status fields, and explicit dates, without pulling facts from linked resources.",
        ]
    )

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
    pairs, flags, sparse_seeds, noisy_seeds = build_pairs(seeds, resources)

    write_pairs_jsonl(output_dir / "synthea_pilot50_pair_candidates.jsonl", pairs)
    write_pairs_csv(output_dir / "synthea_pilot50_pair_candidates.csv", pairs)
    write_review_flags(output_dir / "synthea_pilot50_pair_review_flags.csv", flags)
    write_summary(
        output_dir / "synthea_pilot50_pair_generation_summary.md",
        seeds,
        pairs,
        sparse_seeds,
        noisy_seeds,
        flags,
    )

    print("Pair candidate generation complete.")
    print(f"Selected seeds processed: {len(seeds)}")
    print(f"Candidate pairs generated: {len(pairs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
