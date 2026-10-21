#!/usr/bin/env bash
# Usage: prep_clips.sh <clips_dir> <out_dir> [interval_seconds]
# For every video: probe metadata, sample a frame every N seconds with the
# timestamp burned in, tile them into one contact sheet, and transcribe audio
# if the clip has an audio track (faster-whisper, tiny model on CPU).
set -euo pipefail
CLIPS="$1"; OUT="$2"; STEP="${3:-2}"
mkdir -p "$OUT"
for f in "$CLIPS"/*.{mp4,mov,MP4,MOV}; do
  [ -e "$f" ] || continue
  base=$(basename "${f%.*}")
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  res=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$f")
  audio=$(ffprobe -v error -select_streams a -show_entries stream=codec_name -of csv=p=0 "$f" | head -1)
  n=$(python3 -c "import math;print(max(1,math.ceil($dur/$STEP)))")
  cols=4; rows=$(( (n + cols - 1) / cols ))
  ffmpeg -v error -y -i "$f" -vf "fps=1/$STEP,scale=480:-2,drawtext=text='%{pts\:hms}':x=10:y=10:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6,tile=${cols}x${rows}:padding=4" -frames:v 1 "$OUT/$base.sheet.jpg"
  transcript="(no audio track)"
  if [ -n "$audio" ]; then
    transcript=$(python3 - "$f" <<'PY'
import sys
from faster_whisper import WhisperModel
m = WhisperModel("tiny", device="cpu", compute_type="int8")
segs, _ = m.transcribe(sys.argv[1])
print("\n".join(f"[{s.start:05.1f}-{s.end:05.1f}] {s.text.strip()}" for s in segs) or "(no speech)")
PY
)
  fi
  printf '{"clip":"%s","duration_s":%.1f,"resolution":"%s","sample_every_s":%s,"sheet":"%s"}\n' "$(basename "$f")" "$dur" "$res" "$STEP" "$base.sheet.jpg" > "$OUT/$base.meta.json"
  printf '%s\n' "$transcript" > "$OUT/$base.transcript.txt"
  echo "prepped $(basename "$f") (${dur}s, $res, audio: ${audio:-none})"
done
