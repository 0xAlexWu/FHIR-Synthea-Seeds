# Synthea Remaining 70 Selection Summary

## Overlap Prevention

- Loaded the prior pilot-50 selected IDs from `outputs/large/synthea_pilot50_selected.csv` and removed them from the candidate pool before scoring any remaining seed.
- Overlap after selection: 0

## Final Counts

- Patient: 10
- Observation: 50
- Condition: 10
- Total selected seeds: 70

## Criteria Relaxations Used

- The current catalog does not expose `good_for_pairing`, so that preference could not be applied directly in this selection pass.
- Observation linked-context relaxation was required for 50/50 Observation selections because the remaining catalog contains no `needs_linked_context=false` Observation rows.
- Condition linked-context relaxation was required for 10/10 Condition selections because the remaining catalog contains no `needs_linked_context=false` Condition rows.
- No high-complexity Observation fallback was needed; all selected Observations stayed at moderate complexity while preserving numeric value and unit metadata.
- No non-numeric Observation fallback was needed; every selected Observation remained numeric with explicit value and unit metadata.
- 3/10 selected Patient seeds intentionally include `deceasedDateTime` so the revised patient-generation rules can be exercised without making deceased cases the majority.

## Why These 70 Are Appropriate

- Observation remains the main expansion target: 50 seeds were selected, all numeric and spread across 14 Observation shard files for broader coverage.
- Patient expansion stays deliberate rather than arbitrary: 10 low-complexity demographic anchors were selected, including 3 deceased-status cases for rule validation and 7 living cases for straightforward pairing coverage.
- Condition coverage continues, but conservatively: 10 moderate-complexity linked-context seeds were spread across 10 distinct Condition ID-prefix buckets so the mother pool keeps cross-type breadth without over-expanding the most review-sensitive type from the pilot.

## Selection Priority Breakdown

- 01_deceased_coverage: 3
- 02_living_anchor: 7
- 03_relaxed_linked_moderate: 60
