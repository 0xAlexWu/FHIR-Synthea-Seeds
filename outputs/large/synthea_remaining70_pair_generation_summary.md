# Synthea Remaining 70 Pair Generation Summary

## Totals

- Total new seeds processed: 70
- Total candidate pairs generated: 197

## Variants by Resource Type

- Patient: 27
- Observation: 150
- Condition: 20

## Variants by Input Style

- concise_clinical: 70
- semi_structured: 70
- shorthand_note: 57

## Revised Rules vs the Earlier Pilot

- Observation generation stayed numeric-first and still uses `concise_clinical`, `semi_structured`, and `shorthand_note`.
- Patient generation now explicitly preserves deceased status whenever it appears in the target seed; shorthand variants were suppressed for deceased patients rather than risk silent omission.
- Condition generation was tightened to `concise_clinical` plus `semi_structured` by default; routine `patient_utterance` generation was removed for this round.
- `mildly_noisy_or_incomplete` was disabled entirely for this step.

## Deceased-Status Handling

- Patient seeds containing `deceasedDateTime`: 3 / 10
- Every deceased Patient pair was flagged with `deceased_status_risk` for first-pass review.
- Deceased Patient seeds were limited to fuller variants that can explicitly carry the deceased signal rather than a shorthand form that might drop it.

## Condition Style Restrictions

- Condition seeds restricted away from `patient_utterance`: 10
- No `patient_utterance` exceptions were used in this batch.

## Seeds Flagged for Manual Review

- Patient/01867dda-3c09-6717-00e7-92fa7be373b3
- Patient/07066faf-a4af-97d3-0780-89e015322ec5
- Patient/095dd3d9-8eb6-e786-8bfe-4e37c593e014
