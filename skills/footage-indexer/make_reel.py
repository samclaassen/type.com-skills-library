#!/usr/bin/env python3
"""Cut moments from source clips into one vertical (1080x1920) reel with captions.

Usage: python3 make_reel.py <clips_dir> <edl.json> <out.mp4>
edl.json: {"end_card": "optional text", "cuts": [{"clip": "IMG_4204.mp4", "start": 0, "end": 3, "caption": "..."}]}
"""
import json, os, subprocess, sys, tempfile, shutil

FONT = next((f for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"] if os.path.exists(f)), None)

def text_filter(text, y, size, tmp):
    # Read the caption from a file with expansion off, so %, \, : and quotes render literally.
    fd, path = tempfile.mkstemp(suffix=".txt", dir=tmp)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    font = f"fontfile={FONT}:" if FONT else ""
    return (f"drawtext={font}textfile={path}:expansion=none:fontsize={size}:fontcolor=white:"
            f"x=(w-text_w)/2:y={y}:box=1:boxcolor=black@0.55:boxborderw=24")

def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def main(clips_dir, edl_path, out, tmp):
    edl = json.load(open(edl_path))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    parts = []
    base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1"
    for i, c in enumerate(edl["cuts"]):
        vf = base + ("," + text_filter(c["caption"], "h*0.78", 72, tmp) if c.get("caption") else "")
        p = os.path.join(tmp, f"p{i:02d}.mp4")
        run(["ffmpeg", "-y", "-ss", str(c["start"]), "-to", str(c["end"]), "-i", os.path.join(clips_dir, c["clip"]),
             "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", p])
        parts.append(p)
    if edl.get("end_card"):
        p = os.path.join(tmp, "zz_end.mp4")
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x111111:s=1080x1920:d=2:r=30",
             "-vf", text_filter(edl["end_card"], "(h-text_h)/2", 84, tmp), "-c:v", "libx264", "-pix_fmt", "yuv420p", p])
        parts.append(p)
    lst = os.path.join(tmp, "list.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    joined = os.path.join(tmp, "reel.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", joined])
    shutil.move(joined, out)
    print(f"wrote {out}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.stderr.write("ERROR: " + __doc__.strip() + "\n")
        sys.exit(2)
    tmp = tempfile.mkdtemp()
    try:
        main(*sys.argv[1:4], tmp)
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        sys.stderr.write(f"ERROR: ffmpeg failed: {msg[-1] if msg else e}\n")
        sys.exit(2)
    except Exception as e:
        sys.stderr.write(f"ERROR: {type(e).__name__}: {e}\n")
        sys.exit(2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
