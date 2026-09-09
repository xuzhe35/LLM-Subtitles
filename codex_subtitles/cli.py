from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export_service import export_job
from .local_asr_service import available_backends
from .storage import job_paths, read_json, update_manifest
from .translation_service import copy_source_to_targets, translation_status
from .workflow_service import import_source_job, plan_job, prepare_youtube_job, transcribe_job_locally


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _add_window_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-cues", type=int, default=60)
    parser.add_argument("--max-duration-sec", type=float, default=480.0)
    parser.add_argument("--context-cues", type=int, default=6)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="API-free local services for the Codex YouTube subtitle skill."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Fetch captions, or audio when captions are unavailable.")
    prepare.add_argument("url")
    prepare.add_argument("--target-language", default="Simplified Chinese")
    prepare.add_argument("--source-language")
    prepare.add_argument("--output-root", default="output/codex_native")
    prepare.add_argument("--force-audio", action="store_true")

    imported = subparsers.add_parser("import-source", help="Create a job from an existing SRT, VTT, or JSON transcript.")
    imported.add_argument("subtitle")
    imported.add_argument("--target-language", default="Simplified Chinese")
    imported.add_argument("--source-language")
    imported.add_argument("--output-root", default="output/codex_native")
    imported.add_argument("--title")

    transcribe = subparsers.add_parser("transcribe-local", help="Transcribe downloaded audio with a local Whisper runtime.")
    transcribe.add_argument("job_dir")
    transcribe.add_argument("--backend", choices=("auto", "mlx-whisper", "openai-whisper-local"), default="auto")
    transcribe.add_argument("--model")
    transcribe.add_argument("--language")
    transcribe.add_argument("--enhance", choices=("off", "mild", "strong_ffmpeg"), default="off")

    plan = subparsers.add_parser("plan", help="Create resumable translation windows.")
    plan.add_argument("job_dir")
    _add_window_options(plan)

    status = subparsers.add_parser("status", help="Report source and translation progress.")
    status.add_argument("job_dir")

    validate = subparsers.add_parser("validate", help="Validate every completed translation window.")
    validate.add_argument("job_dir")

    copy_target = subparsers.add_parser(
        "copy-source-to-target",
        help="Use source captions directly when they already match the target language.",
    )
    copy_target.add_argument("job_dir")

    finalize = subparsers.add_parser("finalize", help="Validate and generate translated and bilingual SRT files.")
    finalize.add_argument("job_dir")

    ocr = subparsers.add_parser('ocr-video', help='Extract original-language hard subtitles from a local recording into SRT.')
    ocr.add_argument('video')
    ocr.add_argument('--language', default='en', help='Visible subtitle language, not spoken language.')
    ocr.add_argument('--region', default='auto', help='auto, top, bottom, or normalized x,y,width,height')
    ocr.add_argument('--fps', type=float, default=3)
    ocr.add_argument('--refine-fps', type=float, default=10)
    ocr.add_argument('--start', type=float, default=0)
    ocr.add_argument('--end', type=float)
    ocr.add_argument('--time-offset', type=float, default=0, help='Seconds added to recording timestamps in the SRT.')
    ocr.add_argument('--output-root', default='output/recorded_subtitles')
    ocr.add_argument('--force', action='store_true', help='Recompute local detection, frames and OCR.')
    ocr_status = subparsers.add_parser('ocr-video-status', help='Read local recording OCR progress.')
    ocr_status.add_argument('job_dir')

    subparsers.add_parser("doctor", help="Show local, non-API runtime availability.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_youtube_job(
                args.url,
                target_language=args.target_language,
                source_language=args.source_language,
                output_root=args.output_root,
                force_audio=args.force_audio,
            )
        elif args.command == 'ocr-video':
            from .recorded_video_ocr import extract_recorded_subtitles
            result = extract_recorded_subtitles(args.video, language=args.language, region=args.region, fps=args.fps,
                                                refine_fps=args.refine_fps, start=args.start, end=args.end,
                                                time_offset=args.time_offset, output_root=args.output_root, force=args.force)
        elif args.command == 'ocr-video-status':
            from .recorded_video_ocr import recorded_ocr_status
            result = recorded_ocr_status(args.job_dir)
        elif args.command == "import-source":
            result = import_source_job(
                args.subtitle,
                target_language=args.target_language,
                source_language=args.source_language,
                output_root=args.output_root,
                title=args.title,
            )
        elif args.command == "transcribe-local":
            result = transcribe_job_locally(
                args.job_dir,
                backend=args.backend,
                model=args.model,
                language=args.language,
                enhance=args.enhance,
            )
        elif args.command == "plan":
            result = plan_job(
                args.job_dir,
                max_cues=args.max_cues,
                max_duration_sec=args.max_duration_sec,
                context_cues=args.context_cues,
            )
        elif args.command in {"status", "validate"}:
            paths = job_paths(args.job_dir)
            result = {
                "job": read_json(paths["manifest"]),
                "translation": translation_status(args.job_dir),
            }
            if args.command == "validate" and result["translation"]["state"] != "complete":
                _print(result)
                return 2
        elif args.command == "copy-source-to-target":
            result = {"windows_filled": copy_source_to_targets(args.job_dir)}
        elif args.command == "finalize":
            result = export_job(args.job_dir)
            update_manifest(args.job_dir, status="complete", final=result)
        else:
            result = {
                "python": sys.executable,
                "local_asr_backends": available_backends(),
                "openai_api_required": False,
            }
        _print(result)
        return 0
    except Exception as error:
        _print({"error": str(error), "command": args.command, **getattr(error, "details", {})})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
