# Synthea Pilot 50 Pair Generation Summary

## Totals

- Total seeds processed: 50
- Total candidate pairs generated: 148

## Variants by Resource Type

- Patient: 15
- Observation: 105
- Condition: 28

## Variants by Input Style

- concise_clinical: 50
- patient_utterance: 8
- semi_structured: 50
- shorthand_note: 40

## Seeds Too Sparse for 3 Variants

- Condition/019e6247-d184-3c28-c72a-fd507eb09b4c
- Condition/05db72b7-eda9-85c9-703b-c0404c3ae8d7

## Mildly Noisy or Incomplete Usage

- None used

## Seeds Flagged for Manual Review

- Condition/00f9e3dc-c68e-7a7d-d3bb-4a79d77acb36
- Condition/019e6247-d184-3c28-c72a-fd507eb09b4c
- Condition/03d53d8d-0e08-7644-0e04-8983cefa9347
- Condition/0443254f-7306-f744-c8e4-21524d693623
- Condition/05db72b7-eda9-85c9-703b-c0404c3ae8d7
- Condition/06537fa0-88e4-a5f1-3a0a-9bb49cae9346
- Condition/076ce495-0477-ed52-8429-4a68d4e3221f
- Condition/08056bc1-b178-1d21-6eda-44840fd1882b
- Condition/095dd3d9-8eb6-e786-d7f9-686ea6fec31c
- Condition/0a84f58e-5ab1-0069-da6a-e96017dcfb89
- Patient/01e8022f-bd3e-b493-9190-3e87e8f94023

## Why These Pair Candidates Are Review-Ready

- Every pair is tied directly to an existing selected seed and references the original Large dataset source file through `target_seed_file`.
- No new target FHIR resources were generated; only source-faithful `input_text` variants were created.
- Observation variants preserve the core numeric result and unit in every generated variant.
- Patient variants use only explicit demographic or identity-related facts from the seed resource.
- Condition variants stay within the coded condition text, status fields, and explicit dates, without pulling facts from linked resources.
