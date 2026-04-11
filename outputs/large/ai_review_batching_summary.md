# AI Review Batching Summary

- Total number of pairs packaged: 148
- Median reviewer prompt length: 2,388 characters
- Maximum reviewer prompt length: 6,442 characters

## Suggested Batching Strategy for GPT-5.4 Thinking

- 20 pairs per batch, about 8 batches total.
- Use the JSONL request file directly and keep one model response per request.
- Leave extra headroom for longer target FHIR JSON payloads and richer rationales.

## Suggested Batching Strategy for Claude 3.7 Sonnet

- 24 pairs per batch, about 7 batches total.
- Prefer homogeneous batches by resource type when possible, especially for large Patient prompts.
- Keep the CSV request export handy for spreadsheet-driven spot checks.

## Suggested Batching Strategy for Gemini 2.5 Pro

- 28 pairs per batch, about 6 batches total.
- Gemini can usually tolerate slightly larger mixed batches, but keep retries isolated at the request level.
- Use the JSONL file as the source of truth for API submission.

## Very Large Prompts

- No prompts crossed the large-prompt threshold used here (>14,000 chars or >3,500 estimated tokens).
