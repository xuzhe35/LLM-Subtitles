import argparse
import json
import os
from collections import Counter

from utils.segments import normalize_segments


def load_segments_json(path):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        payload = payload.get("segments", [])
    return normalize_segments(payload)


def transcript_metrics(segments):
    normalized = normalize_segments(segments)
    texts = [seg.get("text", "").strip() for seg in normalized]
    non_empty_texts = [text for text in texts if text]
    counts = Counter(non_empty_texts)
    repeated_text_count = sum(count - 1 for count in counts.values() if count > 1)

    durations = [
        max(0.0, float(seg["end"]) - float(seg["start"]))
        for seg in normalized
        if seg.get("start") is not None and seg.get("end") is not None
    ]
    if normalized:
        first_start = min(float(seg["start"]) for seg in normalized)
        last_end = max(float(seg["end"]) for seg in normalized)
    else:
        first_start = 0.0
        last_end = 0.0

    return {
        "segment_count": len(normalized),
        "empty_segment_count": len(texts) - len(non_empty_texts),
        "repeated_text_count": repeated_text_count,
        "unique_text_count": len(counts),
        "speech_seconds": round(sum(durations), 3),
        "span_seconds": round(max(0.0, last_end - first_start), 3),
    }


def render_markdown_report(results, title="Audio Pipeline Evaluation"):
    lines = [
        f"# {title}",
        "",
        "| Mode | Segments | Empty | Repeated | Unique Text | Speech Seconds | Span Seconds |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for mode, metrics in results.items():
        lines.append(
            "| {mode} | {segment_count} | {empty_segment_count} | "
            "{repeated_text_count} | {unique_text_count} | "
            "{speech_seconds:.3f} | {span_seconds:.3f} |".format(
                mode=mode,
                **metrics,
            )
        )

    lines.extend([
        "",
        "Notes:",
        "- Lower empty/repeated counts are usually better, but inspect the transcript before trusting the numbers.",
        "- Strong denoise can reduce noise while damaging weak speech details.",
    ])
    return "\n".join(lines) + "\n"


def parse_transcript_arg(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("Transcript inputs must be formatted as mode=path")
    mode, path = value.split("=", 1)
    mode = mode.strip()
    path = path.strip()
    if not mode or not path:
        raise argparse.ArgumentTypeError("Transcript inputs must include both mode and path")
    return mode, path


def evaluate_transcript_files(transcript_files):
    results = {}
    for mode, path in transcript_files:
        results[mode] = transcript_metrics(load_segments_json(path))
    return results


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Compare transcript outputs from audio enhancement modes.")
    parser.add_argument(
        "--transcript",
        action="append",
        type=parse_transcript_arg,
        required=True,
        help="Transcript JSON input formatted as mode=path. May be repeated.",
    )
    parser.add_argument("--title", default="Audio Pipeline Evaluation", help="Report title.")
    parser.add_argument("--output", help="Markdown report output path. Prints to stdout if omitted.")
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    report = render_markdown_report(
        evaluate_transcript_files(args.transcript),
        title=args.title,
    )
    if args.output:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
