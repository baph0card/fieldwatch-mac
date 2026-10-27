#!/usr/bin/env python3
"""Draw the Fieldwatch app icon (a radar scope) as a 1024x1024 PNG using only the standard library.

Usage: make_icon.py out.png
"""
import math
import struct
import sys
import zlib

N = 1024
CX = CY = N / 2
BOX, RADIUS = 824, 185  # Apple's icon grid: rounded square inset in the canvas


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def hexc(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


GREEN, AMBER, VIOLET = hexc("#3dff9a"), hexc("#ffb020"), hexc("#b388ff")
TOP, BOTTOM = hexc("#1d262c"), hexc("#0c1013")

RINGS = [(110, 0.85), (220, 0.55), (330, 0.32)]
SWEEP_A0, SWEEP_A1, SWEEP_R = math.radians(-100), math.radians(-25), 330
BLIPS = [(650, 345, 26, AMBER), (385, 640, 21, VIOLET), (625, 655, 17, GREEN)]


def mix(base, color, a):
    return tuple(b + (c - b) * a for b, c in zip(base, color))


def render():
    rows = []
    half = BOX / 2 - RADIUS
    for y in range(N):
        row = bytearray([0])
        dy = y + 0.5 - CY
        for x in range(N):
            dx = x + 0.5 - CX
            # rounded-square coverage
            qx, qy = abs(dx) - half, abs(dy) - half
            sd = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - RADIUS
            cover = clamp(0.5 - sd)
            if cover <= 0:
                row += b"\x00\x00\x00\x00"
                continue
            t = clamp((y - (CY - BOX / 2)) / BOX)
            col = tuple(a + (b - a) * t for a, b in zip(TOP, BOTTOM))
            r = math.hypot(dx, dy)
            # faint crosshair
            if abs(dx) < 1.2 or abs(dy) < 1.2:
                if r < 330:
                    col = mix(col, GREEN, 0.10)
            # sweep wedge
            if r < SWEEP_R:
                ang = math.atan2(dy, dx)
                if SWEEP_A0 <= ang <= SWEEP_A1:
                    f = (ang - SWEEP_A0) / (SWEEP_A1 - SWEEP_A0)
                    col = mix(col, GREEN, 0.38 * f * f * (1 - 0.5 * r / SWEEP_R))
            # rings
            for rad, alpha in RINGS:
                d = abs(r - rad) - 5
                a = clamp(0.5 - d) * alpha
                if a > 0:
                    col = mix(col, GREEN, a)
            # blips with a soft halo
            for bx, by, br, bc in BLIPS:
                d = math.hypot(x + 0.5 - bx, y + 0.5 - by)
                halo = clamp(1 - (d - br) / (br * 1.6)) * 0.25 if d > br else 0
                if halo > 0:
                    col = mix(col, bc, halo)
                col = mix(col, bc, clamp(br - d + 0.5))
            # centre dot
            col = mix(col, GREEN, clamp(16 - r + 0.5))
            row += bytes((int(col[0]), int(col[1]), int(col[2]), int(cover * 255)))
        rows.append(bytes(row))
    return b"".join(rows)


def png(raw):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    with open(out, "wb") as fh:
        fh.write(png(render()))
