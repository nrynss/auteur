"""foley CLI -- T4 render flow + T5b `direct` subcommand.

    foley <clip.mp4> [-o out.mp4] [--tier ambience|discrete|full]
          [--model <vlm>] [--timeline out.json] [--from-timeline in.json]
          [--cache-dir sfx_cache]

    foley direct <timeline.json> --notes "..." [-o out.json]
          [--model <vlm>] [--clip video.mp4]

--from-timeline skips analysis entirely (no VLM call, $0) and loads the
timeline from the given JSON instead -- this is the demo/deterministic path.
--model has no effect when --from-timeline is set, since no analysis runs.

Backward compatibility ("foley <clip> ..." must keep working unchanged):
argparse subparsers would make `direct` a formal subcommand, but that
requires a command token in the *first* position for every invocation --
`foley clip.mp4` has no such token, it's bare `foley <clip>`. Rather than
force a `foley render <clip>` rename (a breaking change to the documented
README commands), main() inspects the raw first argv token: if it is
EXACTLY the literal string "direct", it dispatches to the direct subcommand
parser; anything else (including no args, flags, or a clip path) falls
through to the original render parser unchanged. The only edge case this
creates is a clip literally named "direct" with no extension (e.g. `foley
direct` meaning "render a file called direct") -- vanishingly unlikely for
a video path, and cheap to sidestep with `./direct` or `-- direct` if it
ever comes up. Both README quickstart commands (`foley clip.mp4 ...`) are
unaffected and re-verified by tests/test_pipeline.py.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

from foley.analyze import DEFAULT_MODEL, get_video_duration
from foley.direct import apply_notes, default_output_path, diff_timelines
from foley.pipeline import run
from foley.sfx import DEFAULT_CACHE_DIR
from foley.timeline_io import load_timeline, save_timeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foley",
        description="AI Foley: silent AI video -> VLM event timeline -> "
                     "per-event generated SFX -> synced soundtrack.",
    )
    parser.add_argument("clip", help="Path to a silent input video clip (e.g. clip.mp4).")
    parser.add_argument(
        "-o", "--out", default=None,
        help="Output mp4 path (default: <clip stem>.foley.mp4 next to the clip).",
    )
    parser.add_argument(
        "--tier", choices=["ambience", "discrete", "full"], default="full",
        help="Degradation-ladder render tier (default: full).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="VLM model id for analysis (fal OpenRouter gateway). Ignored "
             "when --from-timeline is given, since analysis is skipped.",
    )
    parser.add_argument(
        "--timeline", dest="timeline_out", default=None,
        help="Save the resulting timeline JSON to this path "
             "(default: alongside the output, <out stem>.timeline.json).",
    )
    parser.add_argument(
        "--from-timeline", dest="from_timeline", default=None,
        help="Skip analysis (no paid VLM call) and load the timeline from "
             "this JSON file instead. Mutually exclusive in effect with "
             "--model (which is ignored when this is set). This is the "
             "demo path -- always used for the live terminal demo.",
    )
    parser.add_argument(
        "--cache-dir", default=DEFAULT_CACHE_DIR,
        help=f"SFX cache directory (default: {DEFAULT_CACHE_DIR}).",
    )
    return parser


def build_direct_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foley direct",
        description="Apply plain-language director's notes to an existing "
                     "timeline JSON via a text-only LLM edit (~$0.001), "
                     "validated through the same parse_timeline_json used "
                     "for a fresh analysis.",
    )
    parser.add_argument("timeline", help="Path to an existing timeline JSON (e.g. samples/timelines/clip_1.json).")
    parser.add_argument(
        "--notes", required=True,
        help='Director\'s notes in plain language, e.g. '
             '"move the match strike 200ms later and make it high intensity; '
             'drop the paper tearing event".',
    )
    parser.add_argument(
        "-o", "--out", default=None,
        help="Output timeline JSON path (default: <in stem>.directed.json "
             "next to the input; a .directedN.json input bumps to N+1 -- "
             "the input is NEVER overwritten).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="LLM model id for the edit (fal OpenRouter gateway).",
    )
    parser.add_argument(
        "--clip", default=None,
        help="Optional source video -- supplies the ffprobe clip duration "
             "so the edited timeline is clamped against it.",
    )
    return parser


def _run_render(argv) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        run(
            args.clip,
            out_path=args.out,
            tier=args.tier,
            model=args.model,
            timeline_json=args.from_timeline,
            save_timeline_to=args.timeline_out,
            cache_dir=args.cache_dir,
        )
    except Exception as e:
        print(f"foley: error: {e}", file=sys.stderr)
        return 1
    return 0


def _run_direct(argv) -> int:
    parser = build_direct_parser()
    args = parser.parse_args(argv)

    out_path = args.out or default_output_path(args.timeline)
    if os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(os.path.abspath(args.timeline)):
        print(f"foley direct: error: refusing to overwrite the input "
              f"timeline ({args.timeline!r}); pass a different -o.",
              file=sys.stderr)
        return 1

    try:
        old_timeline = load_timeline(args.timeline)
        clip_duration = get_video_duration(args.clip) if args.clip else None
        new_timeline = apply_notes(
            old_timeline, args.notes, model=args.model, clip_duration=clip_duration,
        )
        save_timeline(new_timeline, out_path)
    except Exception as e:
        print(f"foley direct: error: {e}", file=sys.stderr)
        return 1

    print(f"[direct] notes applied via {args.model}")
    print(diff_timelines(old_timeline, new_timeline))
    print(f"\nAPPLIED — saved to {out_path}")
    return 0


def main() -> int:
    load_dotenv()
    argv = sys.argv[1:]

    if argv and argv[0] == "direct":
        return _run_direct(argv[1:])
    return _run_render(argv)


if __name__ == "__main__":
    sys.exit(main())
