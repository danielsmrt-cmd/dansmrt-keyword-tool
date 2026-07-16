#!/usr/bin/env python3
"""
transcribe_srt.py  —  DanSmrtCoaching

Turns filmed footage (or audio) into a clean, timestamped .srt you can paste
straight into the Edit Map tool's "timestamped" mode. faster-whisper reads the
video directly (ffmpeg pulls the audio), so point it at the mp4 as-is.

Sits next to broll_match.py and shares the same faster-whisper install — no new
dependency. broll_match.py MATCHES pre-written cue phrases to timestamps; this
just DUMPS the spoken transcript with times, which is what Edit Map wants as
input (Edit Map generates the cues itself).

SETUP (one time, already done if broll_match.py runs):
    pip install faster-whisper

USAGE:
    python transcribe_srt.py myshort.mp4
    python transcribe_srt.py myshort.mp4 --out myshort.srt --model small
    python transcribe_srt.py myshort.mp4 --model tiny        # quick pass
    python transcribe_srt.py myshort.mp4 --max-sec 6         # split long lines

BATCH (built for your weekly 7-Short rhythm — model loads once, reused for all):
    python transcribe_srt.py --batch ./this_weeks_shorts     # whole folder
    python transcribe_srt.py a.mp4 b.mp4 c.mp4               # explicit list
    # each writes <name>.srt next to its source; stdout lists the SRT paths.

MODEL: "small" (default) is good for a single close-mic voice; "tiny" is faster
and rougher; "turbo" is best if your machine can take it.
"""

import argparse
import sys
from pathlib import Path


def fmt_ts(t: float) -> str:
    """Seconds -> SRT timestamp HH:MM:SS,mmm."""
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_long(seg_start, seg_end, words, max_sec):
    """Break one long Whisper segment into <=max_sec chunks on word boundaries,
    so Edit Map's cues land tighter. Falls back to the whole segment if no
    word-level timing is available."""
    if not words or max_sec <= 0 or (seg_end - seg_start) <= max_sec:
        return None  # caller keeps the segment whole
    chunks, cur, cur_start = [], [], None
    for w in words:
        ws = getattr(w, "start", None)
        we = getattr(w, "end", None)
        wt = getattr(w, "word", "") or ""
        if ws is None or we is None:
            continue
        if cur_start is None:
            cur_start = ws
        cur.append(wt)
        if (we - cur_start) >= max_sec:
            chunks.append((cur_start, we, "".join(cur).strip()))
            cur, cur_start = [], None
    if cur and cur_start is not None:
        last_end = getattr(words[-1], "end", seg_end) or seg_end
        chunks.append((cur_start, last_end, "".join(cur).strip()))
    return [c for c in chunks if c[2]] or None


def transcribe_one(model, src: Path, out: Path, language, max_sec):
    """Transcribe one file to an SRT at `out`. Returns line count, or 0 if no speech."""
    need_words = max_sec > 0
    print(f"Transcribing {src.name}…", file=sys.stderr)
    segments, info = model.transcribe(
        str(src), beam_size=5, language=language, word_timestamps=need_words
    )
    print(f"  language: {info.language} (p={info.language_probability:.2f})", file=sys.stderr)

    lines, idx = [], 1
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        pieces = split_long(seg.start, seg.end, getattr(seg, "words", None), max_sec) if need_words else None
        if pieces:
            for (cs, ce, ct) in pieces:
                lines.append(f"{idx}\n{fmt_ts(cs)} --> {fmt_ts(ce)}\n{ct}\n")
                idx += 1
        else:
            lines.append(f"{idx}\n{fmt_ts(seg.start)} --> {fmt_ts(seg.end)}\n{text}\n")
            idx += 1

    if not lines:
        print(f"  no speech found in {src.name} — skipped", file=sys.stderr)
        return 0
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote {idx - 1} lines -> {out}", file=sys.stderr)
    return idx - 1


MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}


def main():
    ap = argparse.ArgumentParser(description="Footage -> timestamped SRT for the Edit Map tool.")
    ap.add_argument("inputs", nargs="*", help="one or more video/audio files (mp4, mov, wav, mp3, ...)")
    ap.add_argument("--batch", help="a folder — transcribe every media file inside it (one SRT each, next to each file)")
    ap.add_argument("--out", help="output .srt path (single-file only; ignored in batch/multi-file mode)")
    ap.add_argument("--model", default="small", help="faster-whisper model: tiny | small | turbo (default: small)")
    ap.add_argument("--language", default=None, help="force a language code (e.g. en); default auto-detect")
    ap.add_argument("--max-sec", type=float, default=0.0,
                    help="split segments longer than this many seconds on word boundaries (0 = keep whole)")
    args = ap.parse_args()

    # Gather the work list.
    files = []
    if args.batch:
        folder = Path(args.batch)
        if not folder.is_dir():
            sys.exit(f"--batch folder not found: {folder}")
        files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTS)
        if not files:
            sys.exit(f"No media files ({', '.join(sorted(MEDIA_EXTS))}) in {folder}")
    else:
        for a in args.inputs:
            p = Path(a)
            if not p.exists():
                sys.exit(f"File not found: {p}")
            files.append(p)
    if not files:
        sys.exit("Nothing to do. Pass a file, several files, or --batch <folder>.")

    if args.out and len(files) > 1:
        print("Note: --out ignored with multiple files; each SRT is written next to its source.", file=sys.stderr)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper not installed. Run: pip install faster-whisper")

    # Load the model ONCE and reuse it across every file — the slow part happens a single time.
    print(f"Loading model '{args.model}' (CPU, int8)…", file=sys.stderr)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    written, skipped = [], []
    for i, src in enumerate(files, 1):
        if len(files) > 1:
            print(f"[{i}/{len(files)}] {src.name}", file=sys.stderr)
        out = Path(args.out) if (args.out and len(files) == 1) else src.with_suffix(".srt")
        try:
            n = transcribe_one(model, src, out, args.language, args.max_sec)
            (written if n else skipped).append(out if n else src)
            if n:
                print(str(out))  # stdout: one SRT path per success, easy to pipe
        except Exception as e:
            print(f"  FAILED {src.name}: {e}", file=sys.stderr)
            skipped.append(src)

    if len(files) > 1:
        print(f"Done: {len(written)} written, {len(skipped)} skipped.", file=sys.stderr)
    if not written:
        sys.exit("No SRTs written.")


if __name__ == "__main__":
    main()
