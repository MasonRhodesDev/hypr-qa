"""profiles/plain-hyprland/check-pointer on synthetic screendumps and Xcursor files."""
import os
import struct
import subprocess
import tempfile
import unittest
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "profiles", "plain-hyprland", "check-pointer")
BG = (17, 17, 17)
W, H = 120, 90


def xcursor(images):
    """images: [(nominal, w, h, xhot, yhot, [argb...])] -> Xcursor file bytes"""
    head = struct.pack("<4sIII", b"Xcur", 16, 0x10000, len(images))
    off = 16 + 12 * len(images)
    toc, body = b"", b""
    for nominal, w, h, xh, yh, px in images:
        toc += struct.pack("<III", 0xFFFD0002, nominal, off + len(body))
        body += struct.pack("<9I", 36, 0xFFFD0002, nominal, 1, w, h, xh, yh, 0) + struct.pack("<%dI" % len(px), *px)
    return head + toc + body


def arrow(w=7, h=11):
    """a right-angled white triangle with a black 1 px edge, premultiplied ARGB"""
    px = []
    for y in range(h):
        for x in range(w):
            if x > y * w // h:
                px.append(0)
            elif x == 0 or x == y * w // h or y == h - 1:
                px.append(0xFF000000)
            else:
                px.append(0xFFFFFFFF)
    return px


def paint(canvas, left, top, w, h, px):
    for j in range(h):
        for i in range(w):
            p = px[j * w + i]
            a = p >> 24
            c = tuple(((p >> s) & 255) + canvas[top + j][left + i][k] * (255 - a) // 255 for k, s in enumerate((16, 8, 0)))
            canvas[top + j][left + i] = c


def png(rows):
    """8-bit RGB PNG, cycling through all five filter types"""
    raw, prev = b"", bytes(W * 3)
    for y, row in enumerate(rows):
        line = bytes(v for p in row for v in p)
        f = y % 5
        out = bytearray()
        for i in range(len(line)):
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            pred = [0, a, b, (a + b) // 2, None][f]
            if f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((line[i] - pred) & 255)
        raw += bytes([f]) + bytes(out)
        prev = line

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def ppm(rows):
    return b"P6\n# test\n%d %d\n255\n" % (W, H) + bytes(v for row in rows for p in row for v in p)


class CheckPointer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hqa-ptr-")
        self.addCleanup(self.tmp.cleanup)
        self.cur = self.write("left_ptr", xcursor([(32, 1, 1, 0, 0, [0xFF00FF00]),
                                                   (24, 7, 11, 1, 2, arrow())]))

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def canvas(self):
        return [[BG] * W for _ in range(H)]

    def run_check(self, shot, x=50, y=40, cur=None):
        return subprocess.run([CHECK, shot, str(x), str(y), cur or self.cur, "24"],
                              capture_output=True, text=True, timeout=60)

    def test_the_theme_pointer_matches_in_png_and_ppm(self):
        c = self.canvas()
        paint(c, 50 - 1, 40 - 2, 7, 11, arrow())
        for shot in (self.write("s.png", png(c)), self.write("s.ppm", ppm(c))):
            r = self.run_check(shot)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("100% at offset +0,+0", r.stdout)

    def test_a_one_pixel_rounding_offset_still_matches(self):
        c = self.canvas()
        paint(c, 50 - 1 + 1, 40 - 2 - 1, 7, 11, arrow())
        r = self.run_check(self.write("s.png", png(c)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("offset +1,-1", r.stdout)

    def test_the_hyprland_fallback_shape_fails_and_is_named(self):
        c = self.canvas()
        for j in range(31):                      # a 25x31 blob at hotspot - (3, 2)
            for i in range(25):
                c[40 - 2 + j][50 - 3 + i] = (200, 200, 200)
        r = self.run_check(self.write("s.png", png(c)))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("shape 25x31 at -3,-2", r.stdout)
        self.assertIn("built-in fallback pointer", r.stdout)

    def test_no_pointer_fails(self):
        r = self.run_check(self.write("s.png", png(self.canvas())))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no pointer near (50, 40)", r.stdout)

    def test_another_theme_pointer_fails(self):
        c = self.canvas()
        other = [0xFFFF0000] * (7 * 11)          # a red block, same size and hotspot
        paint(c, 50 - 1, 40 - 2, 7, 11, other)
        r = self.run_check(self.write("s.png", png(c)))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not match", r.stdout)
        self.assertNotIn("fallback", r.stdout)

    def test_unreadable_inputs_exit_2(self):
        shot = self.write("s.png", png(self.canvas()))
        self.assertEqual(self.run_check(self.write("x.txt", b"hello")).returncode, 2)
        self.assertEqual(self.run_check(shot, cur=self.write("bad", b"nope" * 8)).returncode, 2)
        self.assertEqual(subprocess.run([CHECK, shot], capture_output=True, timeout=60).returncode, 2)


if __name__ == "__main__":
    unittest.main()
