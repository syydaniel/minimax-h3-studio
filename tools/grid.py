"""Grid-artifact metrics for the tile-size A/B.

edge ratio (same metric as antirez/h3.c PR #1): mean |luma step| across
16-pixel boundaries divided by the mean step everywhere else. 1.0 means no
grid; PR #1 measured 1.24 -> 1.19 horizontally going from 320 to 256 px tiles.
Also reports raw-vs-H.264 PSNR so the encoder can be ruled in or out.

Usage: render with `h3 --frames-dir DIR -o DIR.mp4 ...`, then
    python3 tools/grid.py DIR [DIR2 ...]
DIR holds h3's raw frame-NNNN.ppm files; DIR.mp4, if present, is compared too.
"""
import math, subprocess, sys
from pathlib import Path

W, H = 1344, 768


def read_ppm_luma(path):
    data = Path(path).read_bytes()
    # P6\n<w> <h>\n255\n<rgb...>
    parts, pos = [], 0
    while len(parts) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        parts.append(data[pos:end]); pos = end
    pos += 1
    w, h = int(parts[1]), int(parts[2])
    rgb = data[pos:pos + w * h * 3]
    return w, h, bytes(min(255, (299 * rgb[i] + 587 * rgb[i + 1] + 114 * rgb[i + 2]) // 1000)
                        for i in range(0, len(rgb), 3))


def edge_ratio(w, h, y, period=16, y_range=None):
    """Horizontal: every other row, every column step. Vertical: every row
    step, every other column. Subsampling runs along the axis that is NOT
    being classified, so boundary positions are always covered."""
    y0, y1 = y_range or (0, h)
    hb = hn = vb = vn = 0.0
    hbc = hnc = vbc = vnc = 0
    for r in range(y0, y1, 2):
        row = r * w
        for x in range(1, w):
            d = abs(y[row + x] - y[row + x - 1])
            if x % period == 0: hb += d; hbc += 1
            else: hn += d; hnc += 1
    for r in range(max(1, y0), y1):
        row, prev = r * w, (r - 1) * w
        on_boundary = r % period == 0
        for x in range(0, w, 2):
            d = abs(y[row + x] - y[prev + x])
            if on_boundary: vb += d; vbc += 1
            else: vn += d; vnc += 1
    return (hb / hbc) / (hn / hnc), (vb / vbc) / (vn / vnc)


def psnr(a, b):
    mse = sum((p - q) ** 2 for p, q in zip(a, b)) / len(a)
    return float("inf") if mse == 0 else 10 * math.log10(255 ** 2 / mse)


def mp4_frame_luma(mp4, index):
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(mp4),
                          "-vf", f"select=eq(n\\,{index}),format=gray", "-frames:v", "1",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for tag in sys.argv[1:]:
        folder = Path(tag).expanduser().resolve()
        frames = sorted(folder.glob("*.ppm"))
        if not frames:
            print(f"{tag}: 没有原始帧"); continue
        picks = frames[4::8][:4]
        hs, vs, sky_h, sky_v, ps = [], [], [], [], []
        for f in picks:
            w, h, y = read_ppm_luma(f)
            a, b = edge_ratio(w, h, y); hs.append(a); vs.append(b)
            a, b = edge_ratio(w, h, y, y_range=(0, 200)); sky_h.append(a); sky_v.append(b)
            idx = int(f.stem.split("-")[-1])
            video = folder.with_suffix(".mp4")
            m = mp4_frame_luma(video, idx) if video.exists() else b""
            if len(m) == len(y): ps.append(psnr(y, m))
        avg = lambda v: sum(v) / len(v)
        print(f"{tag}: 抽 {len(picks)} 帧 | 16px 边界跳变比 全画面 横 {avg(hs):.3f} 纵 {avg(vs):.3f}"
              f" | 天空 横 {avg(sky_h):.3f} 纵 {avg(sky_v):.3f}"
              + (f" | 原始帧 vs H.264 PSNR {avg(ps):.1f} dB" if ps else ""))


if __name__ == "__main__":
    main()
