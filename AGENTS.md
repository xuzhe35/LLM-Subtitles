# Subtitle workflow preferences

The user changed the default timing target on 2026-09-07. For subtitle projects
with a user-supplied local video, align the English OCR subtitles to that exact
local video's elapsed time. Prefer its visible subtitle transitions as timing
evidence. This preference supersedes older skill defaults that target YouTube.

- Reuse reviewed OCR text and preserved local frame timing where available.
- Do not apply a recording-to-YouTube offset to the local delivery SRT.
- Keep the target video path and checksum in the quality/alignment report.
- If the local video has been cut or changed, verify timing against that new
  file before reusing timestamps. Do not assume a constant offset across edits.
- Treat YouTube as a source/reference unless the user explicitly requests its
  timeline. When no local video exists, use the supplied media and state the
  actual timing basis.
- Keep original evidence and earlier timeline versions; create a clearly named
  local SRT and identify it as the current preferred delivery.
- When translating the local SRT, preserve its timestamps unless the user asks
  for a timing change.
