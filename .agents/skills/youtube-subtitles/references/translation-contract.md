# Codex translation contract

Read this reference after `plan` creates translation windows.

## Whole-video context

Before translating windows, inspect `source.json` and representative windows, then edit `context.json` with:

- a concise program summary;
- speaker names or roles when inferable;
- terminology entries containing source term, chosen target rendering, a short
  note, provenance, scope, and confidence;
- the desired register and any user-specified style rules.

Keep claims uncertain when the source is unclear. Revisit the context file if later windows disambiguate a name or term, then correct affected completed windows.

When project reference documents exist, read
[reference-documents.md](reference-documents.md). Use author notes and glossaries
for terminology, names, roles, concepts, and intended context within their stated
scope. Do not let them provide timestamps or add speech absent from the final
video. Preserve disagreements and provenance instead of collapsing all evidence
into an unexplained single answer.

## Window input

Each `NNNN.source.json` has `core_ids` and `cues`. A cue with `owned: true` must be translated in this target file. Non-owned cues provide surrounding read-only context and must not appear in the output.

## Window output

Edit the matching `NNNN.target.json`. Preserve its metadata and fill only `cues`:

```json
{
  "schema_version": 1,
  "job_id": "unchanged",
  "window_id": "0001",
  "target_language": "Simplified Chinese",
  "cues": [
    {"source_ids": ["c000001"], "text": "第一条字幕"},
    {"source_ids": ["c000002", "c000003"], "text": "合并后的相邻字幕"}
  ]
}
```

Never add timestamps. The exporter rebuilds them from `source_ids`.

Every core ID must occur exactly once, in source order. Adjacent cues may be merged only when this improves sentence boundaries and the combined evidence span is no more than 15 seconds. Do not merge across speaker changes. Use an empty `text` only for definite non-speech or unusable ASR hallucination; the ID must still be covered and the exporter records the deliberate drop.

## Translation quality

- Translate meaning in whole-video context rather than word by word.
- Preserve proper nouns, numbers, dates, units, URLs, product names, and code identifiers.
- Keep repeated catchphrases and terminology consistent with `context.json`.
- Preserve hedging, emotion, jokes, and speaker intent without adding explanations.
- Write compact, natural subtitle language. Avoid translator notes unless the user asks.
- Repair punctuation and sentence boundaries without inventing content.
- When audio/transcript evidence is uncertain, prefer a cautious literal rendering over guessing.

Validate every completed window immediately. A structurally valid window can still need a semantic second pass; sample names, numbers, and boundary joins across windows before finalizing.
