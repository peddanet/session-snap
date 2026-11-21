#!/usr/bin/env python3
"""Create demo media for session-snap and a snapshot that refers to them.

Run as the normal user from anywhere (nothing outside the target folder is touched):

    python3 examples/make_demo_media.py [TARGET]

TARGET defaults to examples/demo-media/ next to this script (ignored by git). The script
writes there:

  * four PNG images with Lorem-ipsum placeholder text (Python standard library only),
  * two five-second test videos (only if ffmpeg is installed; otherwise they are skipped),
  * a Lorem-ipsum text file for the text editor entry,
  * demo-snapshot.json: examples/demo-snapshot.json with /home/USER/Demo replaced by TARGET.

Then try it without touching your real snapshots:

    session-snap show TARGET/demo-snapshot.json
    session-snap restore -s TARGET/demo-snapshot.json --dry-run
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "/home/USER/Demo"

# 5x7 bitmap font: one string of 7 rows per character, '#' = pixel set
FONT = {
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
}
IMAGES = [("Lorem 1.png", "LOREM IPSUM 1", (52, 101, 164)),
          ("Lorem 2.png", "DOLOR SIT 2", (78, 154, 6)),
          ("Ipsum 3.png", "AMET 3", (204, 0, 0)),
          ("Ipsum 4.png", "LOREM 4", (117, 80, 123))]
LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua.\nUt enim ad minim veniam, quis nostrud "
         "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.\n")
VIDEOS = [("Lorem Clip 1.mkv", "testsrc2"), ("Ipsum Clip 2.mkv", "smptebars")]


def text_rows(text: str, scale: int) -> list[list[bool]]:
    """Render text with the built-in bitmap font.

    Args:
        text: Text in capital letters; unknown characters become blanks.
        scale: Pixels per font dot.

    Returns:
        Rows of pixels, True where the text is.
    """
    rows: list[list[bool]] = []
    for r in range(7):
        line: list[bool] = []
        for ch in text:
            glyph = FONT.get(ch, FONT[" "])[r]
            for px in glyph + ".":
                line += [px == "#"] * scale
        for _ in range(scale):
            rows.append(list(line))
    return rows


def png(path: Path, text: str, colour: tuple[int, int, int],
        size: tuple[int, int] = (800, 500)) -> None:
    """Write an RGB PNG with white text, centred on a coloured background.

    Args:
        path: Target file.
        text: Text to draw.
        colour: Background colour (red, green, blue).
        size: Width and height in pixels.
    """
    w, h = size
    bitmap = text_rows(text.upper(), 8)
    bh, bw = len(bitmap), len(bitmap[0])
    x0, y0 = max(0, (w - bw) // 2), max(0, (h - bh) // 2)
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type "None" for this row
        for x in range(w):
            by, bx = y - y0, x - x0
            on = 0 <= by < bh and 0 <= bx < bw and bitmap[by][bx]
            raw += bytes((255, 255, 255)) if on else bytes(colour)

    def chunk(kind: bytes, data: bytes) -> bytes:
        """Build one PNG chunk.

        Args:
            kind: Chunk type, e.g. b"IHDR".
            data: Chunk data.

        Returns:
            Length, type, data and CRC.
        """
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def video(path: Path, source: str) -> bool:
    """Write a five-second test video from an ffmpeg test source.

    Args:
        path: Target file.
        source: Name of the ffmpeg lavfi source, e.g. "testsrc2".

    Returns:
        True if the video was written, False if ffmpeg is missing or failed.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    cmd = [ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"{source}=size=640x360:rate=25:duration=5", "-pix_fmt", "yuv420p", str(path)]
    return subprocess.run(cmd, check=False).returncode == 0


def main() -> int:
    """Create the media and the localised snapshot.

    Returns:
        Exit code.
    """
    target = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 \
        else HERE / "demo-media"
    (target / "Bilder").mkdir(parents=True, exist_ok=True)
    (target / "Videos").mkdir(parents=True, exist_ok=True)
    (target / "Dokumente").mkdir(parents=True, exist_ok=True)
    (target / "Dokumente" / "lorem-ipsum.txt").write_text(LOREM, encoding="utf-8")
    print(f"text   {target / 'Dokumente' / 'lorem-ipsum.txt'}")
    for name, text, colour in IMAGES:
        png(target / "Bilder" / name, text, colour)
        print(f"image  {target / 'Bilder' / name}")
    for name, source in VIDEOS:
        dest = target / "Videos" / name
        if video(dest, source):
            print(f"video  {dest}")
        else:
            print(f"video  {dest}: skipped (ffmpeg not installed or failed)")
    snap = (HERE / "demo-snapshot.json").read_text(encoding="utf-8")
    snap = snap.replace(PLACEHOLDER, str(target))
    json.loads(snap)  # still valid JSON
    (target / "demo-snapshot.json").write_text(snap, encoding="utf-8")
    print(f"snapshot {target / 'demo-snapshot.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
