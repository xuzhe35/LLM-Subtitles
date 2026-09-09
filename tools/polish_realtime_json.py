"""Polish an existing gpt-realtime-translate JSON artifact without redoing audio."""

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import youtube_subtitle_trans
from utils import subtitle_formatter, subtitle_polisher


def _derived_paths(input_path, output_path=None, checkpoint_path=None):
    stem, extension = os.path.splitext(os.path.abspath(input_path))
    if extension.lower() != ".json":
        raise ValueError("Input must be a Realtime Translation .json artifact.")
    output_json = os.path.abspath(output_path or f"{stem}.polished.json")
    output_stem, _ = os.path.splitext(output_json)
    return {
        "json": output_json,
        "checkpoint": os.path.abspath(checkpoint_path or f"{stem}.polish.resume.json"),
        "srt": f"{output_stem}.srt",
        "bilingual_srt": f"{output_stem}.bilingual.srt",
    }


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Apply resumable whole-video context polishing to Realtime subtitle JSON."
    )
    parser.add_argument("input_json", help="Raw gpt-realtime-translate metadata JSON")
    parser.add_argument("--output", help="Polished metadata JSON path")
    parser.add_argument("--checkpoint", help="Resume checkpoint path")
    parser.add_argument(
        "--model",
        default=subtitle_polisher.DEFAULT_POLISH_MODEL,
        help=f"Polishing text model (default: {subtitle_polisher.DEFAULT_POLISH_MODEL})",
    )
    parser.add_argument("--target-language", help="Override target language from JSON")
    parser.add_argument(
        "--max-cues",
        type=int,
        default=subtitle_polisher.DEFAULT_WINDOW_CUES,
        help="Maximum owned cues per polishing window",
    )
    parser.add_argument(
        "--max-duration-sec",
        type=float,
        default=subtitle_polisher.DEFAULT_WINDOW_DURATION_SEC,
        help="Maximum time span per polishing window",
    )
    parser.add_argument(
        "--context-cues",
        type=int,
        default=subtitle_polisher.DEFAULT_CONTEXT_CUES,
        help="Read-only context cues before and after each window",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate JSON, window planning, and raw metrics without calling the API",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    input_path = os.path.abspath(args.input_json)
    with open(input_path, "r", encoding="utf-8") as metadata_file:
        metadata = json.load(metadata_file)

    translated = list(metadata.get("translated_segments") or [])
    source = list(metadata.get("source_segments") or [])
    cues = subtitle_polisher.build_cues(translated, source)
    windows = subtitle_polisher.plan_windows(
        cues,
        max_cues=args.max_cues,
        max_duration_sec=args.max_duration_sec,
        context_cues=args.context_cues,
    )
    if args.dry_run:
        print(json.dumps({
            "input": input_path,
            "cue_count": len(cues),
            "window_count": len(windows),
            "source_transcript_available": any(cue["source"] for cue in cues),
            "raw_quality": subtitle_polisher.subtitle_quality_metrics(translated),
        }, ensure_ascii=False, indent=2))
        return 0

    config = youtube_subtitle_trans.load_config()
    api_key = youtube_subtitle_trans._resolve_openai_api_key(config, print)
    if api_key is None:
        return 2
    client = youtube_subtitle_trans._build_openai_client(api_key)
    paths = _derived_paths(input_path, args.output, args.checkpoint)
    result, polished_metadata = subtitle_polisher.polish_realtime_metadata(
        client,
        metadata,
        target_language=args.target_language,
        model=args.model,
        checkpoint_path=paths["checkpoint"],
        max_cues=args.max_cues,
        max_duration_sec=args.max_duration_sec,
        context_cues=args.context_cues,
        progress_callback=print,
    )

    with open(paths["json"], "w", encoding="utf-8") as output_file:
        json.dump(polished_metadata, output_file, ensure_ascii=False, indent=2)
    subtitle_formatter.generate_srt(result.translated_segments, paths["srt"])
    subtitle_formatter.generate_bilingual_srt(
        source,
        result.translated_segments,
        paths["bilingual_srt"],
        progress_callback=print,
    )
    print(json.dumps({
        "outputs": paths,
        "quality_report": result.quality_report,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
