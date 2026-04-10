# Synthea Pilot 50 Summary

## Final Counts

- Patient: 5
- Observation: 35
- Condition: 10
- Total selected seeds: 50

## Strict Preferred-Criteria Hit Rate

- Selections meeting strict preferred criteria: 5/50
- Patient strict hits: 5/5
- Observation strict hits: 0/35
- Condition strict hits: 0/10

## Criteria Relaxations Used

- Observation linked-context relaxation was required for 35/35 selections because the catalog contains no `needs_linked_context=false` Observation rows.
- Condition linked-context relaxation was required for 10/10 selections because the catalog contains no `needs_linked_context=false` Condition rows.
- No high-complexity Observation fallback was needed; all 35 selected Observations are moderate-complexity numeric rows with value and unit metadata.
- No non-numeric Observation exceptions were needed; the numeric-first preference held for all 35 Observation selections.

## Why These 50 Are Appropriate

- The allocation exactly matches the pilot target of 5 Patient / 35 Observation / 10 Condition.
- Observation remains dominant and review-useful: all 35 selected Observations are numeric, all have value metadata, all have unit metadata, and they are spread across 14 Observation shard files for better first-pass coverage.
- Patient seeds are low-complexity, no-linked-context anchors that should be easy to review and helpful for validating the simplest pairing rules.
- Condition seeds preserve minimum cross-type coverage, while keeping complexity at moderate rather than high so the first pairing pilot can focus on rule stability before harder linked-context cases.

## Selection Priority Breakdown

- 01_strict_low: 5
- 05_relaxed_linked_moderate: 45
