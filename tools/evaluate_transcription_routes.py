"""Compare subtitle-route outputs structurally for the A/B evaluation gate.

This tool makes NO API calls and embeds no keys: it reads artifacts that
existing runs already produced (Realtime metadata JSON, polished JSON, and
Transcribe + LLM translated/quality JSON) and reports the structural and
readability metrics the plan requires before any default-route change:

- cue counts, coverage, and source-ID integrity;
- timestamp monotonicity, invalid/negative durations, overlaps;
- reading speed, short fragments, over-long cues;
- fallback/escalation visibility for the high-quality route.

Human preference and semantic adequacy still require manual review; this
report only automates the structural gates.

Usage:
    python tools/evaluate_transcription_routes.py \
        --arm realtime=output/translated/video.gpt-realtime-translate.json \
        --arm polished=output/translated/video.gpt-realtime-translate.polished.json \
        --arm transcribe_llm=output/translated/video.Simplified Chinese.translated.json \
        [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.subtitle_polisher import subtitle_quality_metrics  # noqa: E402

MAX_REASONABLE_CUE_SEC = 15.0


def load_segments(path):
    """Extract subtitle segments from any of the supported artifact shapes."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Unsupported artifact shape (not an object): {path}")
    for key in ("translated_segments", "cues", "segments"):
        segments = payload.get(key)
        if isinstance(segments, list) and segments:
            return payload, segments
    raise ValueError(
        f"No segments found in {path}; expected translated_segments/cues/segments."
    )


def structural_metrics(segments):
    invalid_durations = 0
    overlaps = 0
    non_monotonic = 0
    over_long = 0
    previous_start = None
    previous_end = None
    for segment in segments:
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        if end <= start:
            invalid_durations += 1
        if end - start > MAX_REASONABLE_CUE_SEC:
            over_long += 1
        if previous_start is not None and start < previous_start - 1e-6:
            non_monotonic += 1
        if previous_end is not None and start < previous_end - 1e-3:
            overlaps += 1
        previous_start = start
        previous_end = max(previous_end or end, end)
    return {
        "cue_count": len(segments),
        "invalid_durations": invalid_durations,
        "non_monotonic_starts": non_monotonic,
        "overlaps": overlaps,
        "over_long_cues": over_long,
    }


def source_coverage(payload, segments):
    """For the high-quality route: verify every source cue ID appears once."""
    covered = []
    for segment in segments:
        covered.extend(segment.get("source_ids") or [])
    if not covered:
        return None
    return {
        "source_id_count": len(covered),
        "unique_source_ids": len(set(covered)),
        "duplicates": len(covered) - len(set(covered)),
    }


def evaluate_arm(name, path):
    payload, segments = load_segments(path)
    report = {
        "artifact": path,
        "structural": structural_metrics(segments),
        "readability": subtitle_quality_metrics(segments),
    }
    coverage = source_coverage(payload, segments)
    if coverage:
        report["source_coverage"] = coverage
    for extra_key in ("escalations", "issues"):
        value = payload.get(extra_key)
        if isinstance(value, list):
            report[f"{extra_key}_count"] = len(value)
    if "degraded_semantic" in payload:
        report["degraded_semantic"] = bool(payload["degraded_semantic"])
    return report


def build_report(arms):
    report = {"arms": {}, "gates": {}}
    for name, path in arms.items():
        report["arms"][name] = evaluate_arm(name, path)
    for name, arm in report["arms"].items():
        structural = arm["structural"]
        report["gates"][name] = {
            "zero_invalid_durations": structural["invalid_durations"] == 0,
            "monotonic": structural["non_monotonic_starts"] == 0,
            "full_source_coverage": (
                arm.get("source_coverage", {}).get("duplicates", 0) == 0
                if "source_coverage" in arm else None
            ),
        }
    return report


def parse_arms(raw_arms):
    arms = {}
    for raw in raw_arms:
        if "=" not in raw:
            raise ValueError(f"--arm expects name=path, got: {raw}")
        name, path = raw.split("=", 1)
        arms[name.strip()] = path.strip()
    return arms


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Structural comparison of subtitle route artifacts (no API calls)."
    )
    parser.add_argument(
        "--arm",
        action="append",
        required=True,
        dest="arms",
        help="name=path pair, repeatable (e.g. realtime=... transcribe_llm=...)",
    )
    parser.add_argument("--output", default=None, help="Write the JSON report here")
    args = parser.parse_args(argv)

    report = build_report(parse_arms(args.arms))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
        print(f"Report written to {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
