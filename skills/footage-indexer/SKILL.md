---
name: footage-indexer
description: |-
  Index raw video footage with AI (scenes, people, actions, emotions, transcripts, timestamps), search it in natural language, and cut matching moments into vertical ad reels.
---

# Footage Indexer

Turns a pile of raw clips into a searchable, timestamped library, then cuts ads from it.

## When to use
- "Index these clips" / "tag this footage"
- "Find me <moment>" (e.g. "creator taste test reactions")
- "Cut those into a 15s vertical ad"

## 1. Index
1. Put the clips in one folder in the sandbox (thread attachments → materialize; Drive → download).
2. Run `bash <skill_dir>/prep_clips.sh <clips_dir> <work_dir> 2` (both scripts sit next to this SKILL.md) → per clip: `<name>.sheet.jpg` (a frame every 2s with the timestamp burned in), `<name>.meta.json`, `<name>.transcript.txt` (faster-whisper when the clip has audio; `pip install faster-whisper "av<16"` if missing, since newer PyAV releases break faster-whisper).
3. Look at every contact sheet (upload it as a draft file and view it). For each clip, split it into **moments** where the action or emotion changes and record:
   `clip, start, end, people, shot, setting, action, emotion, tags[]` plus any transcript lines in that range.
4. **People:** give each distinct recurring person a stable label (`Creator A`, `Creator B`...) with a short visual description, and reuse the label across clips when it is clearly the same person. Do not guess real names; the user can rename labels.
5. Write `footage_index.json` (`{people:{}, moments:[]}`) and publish a readable **Footage Library** doc: a people table, then one row per moment (clip · timecode · what happens · emotion · tags).
6. Always tag likely ad hooks with `hook candidate`.

## 2. Search
Answer natural-language queries from the index, not from filenames. Return a ranked list: `clip @ start–end — what happens — why it matches`. Search across action, emotion, tags, people and transcript. Say so when nothing matches well.

## 3. Cut a reel
1. Write an EDL JSON: `{"end_card": "...", "cuts": [{"clip","start","end","caption"}]}`. Keep each cut 2–5s and open on the strongest hook. Captions don't wrap, so keep each line under about 24 characters and use `\n` for a second line.
2. `python3 -I <skill_dir>/make_reel.py <clips_dir> edl.json out/reel.mp4` → 1080x1920, 30fps, captions burned in, optional end card.
3. QA: tile 1 frame/sec into a contact sheet, check it, then attach the MP4 to the thread.

## Notes
- Requires `ffmpeg`/`ffprobe` and Python 3.8+. Transcription also needs `faster-whisper` on Python 3.9+ (see step 1.2).
- Stock and B-roll clips often have no audio. Say "no dialogue" instead of inventing a transcript.
- For a recurring setup, an automation can run step 1 whenever new files land in the footage folder.