#!/usr/bin/env python3
"""Funny poster: 严禁打骂老板 / 长得好看也不行 — cute hand-journal style."""
import math
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 1500
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boss_warning_fun.png")
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
IDX_BOLD, IDX_REG = 2, 0  # W6 bold / W3 regular — verified full glyph coverage

def F(size, bold=True):
    return ImageFont.truetype(FONT, size, index=IDX_BOLD if bold else IDX_REG)

img = Image.new("RGB", (W, H), "#FFF6E9")  # cream
d = ImageDraw.Draw(img)

# polka dots
for y in range(40, H, 90):
    for x in range(40, W, 90):
        d.ellipse([x, y, x + 16, y + 16], fill="#FFD9A8")

# top & bottom diagonal stripes
STRIPES = ["#FF8FA3", "#FFB26B", "#FF5D8F", "#98E4C6"]
def stripes(y0, height, slant):
    for i, c in enumerate(STRIPES):
        yy = y0 + i * height
        d.polygon([(0, yy), (W, yy - slant), (W, yy - slant + height), (0, yy + height)], fill=c)
stripes(0, 22, 10)
stripes(H - 88, 22, -10)

# white rounded card with double border
CARD = (90, 160, W - 90, H - 160)
d.rounded_rectangle(CARD, radius=48, fill="white", outline="#FFB26B", width=10)
inner = (CARD[0] + 16, CARD[1] + 16, CARD[2] - 16, CARD[3] - 16)
d.rounded_rectangle(inner, radius=38, outline="#FF8FA3", width=5)

def draw_rotated_text(center_x, y, text, font, fill, rot_deg=5, spacing=12):
    """Draw text char-by-char, each slightly rotated for playful look."""
    widths = [d.textlength(c, font=font) for c in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = center_x - total / 2
    for i, c in enumerate(text):
        rot = rot_deg if i % 2 == 0 else -rot_deg
        pad = 16
        w = int(font.size * 1.6)
        tile = Image.new("RGBA", (w, w), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        bbox = td.textbbox((0, 0), c, font=font)
        td.text(((w - (bbox[2] - bbox[0])) / 2 - bbox[0], (w - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                c, font=font, fill=fill)
        tile = tile.rotate(rot, expand=True, resample=Image.BICUBIC)
        img.paste(tile, (int(x - pad), int(y - pad + (i % 2) * 6)), tile)
        x += widths[i] + spacing

# titles
draw_rotated_text(W / 2, 300, "严禁打骂老板", F(150), "#E6396B", rot_deg=4, spacing=10)
draw_rotated_text(W / 2, 520, "长得好看也不行", F(92), "#F7931E", rot_deg=6, spacing=8)

# wavy divider
y0 = 670
pts = [(x, y0 + 10 * math.sin(x / 22)) for x in range(200, W - 200, 4)]
d.line(pts, fill="#FFB26B", width=6)

# kawaii face
cx, cy, r = W // 2, 920, 170
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#FFD93D", outline="#F4A41C", width=8)
# eyes
for ex in (-62, 62):
    d.ellipse([cx + ex - 34, cy - 60, cx + ex + 34, cy + 8], fill="#3B2F2F")
    d.ellipse([cx + ex - 22, cy - 50, cx + ex + 2, cy - 26], fill="white")  # highlight
# blush
for bx in (-120, 120):
    d.ellipse([cx + bx - 35, cy - 5, cx + bx + 35, cy + 30], fill="#FFB0A0")
# open laughing mouth
d.pieslice([cx - 70, cy + 25, cx + 70, cy + 125], 0, 180, fill="#8C4A32")
d.ellipse([cx - 40, cy + 85, cx + 20, cy + 120], fill="#FF7B8A")  # tongue
# sparkle marks
for sx, sy in [(cx - r - 60, cy - 80), (cx + r + 40, cy - 40)]:
    d.line([(sx - 20, sy), (sx + 20, sy)], fill="#F7931E", width=6)
    d.line([(sx, sy - 20), (sx, sy + 20)], fill="#F7931E", width=6)

def sticker(cx, cy, text, color, angle):
    f = F(46)
    tw = d.textlength(text, font=f)
    pw, ph = int(tw) + 70, 100
    tile = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.rounded_rectangle([20, 20, 20 + pw, 20 + ph], radius=28, fill=color,
                         outline="white", width=6)
    td.text((55, 40), text, font=f, fill="white")
    tile = tile.rotate(angle, expand=True, resample=Image.BICUBIC)
    img.paste(tile, (int(cx - tile.width / 2), int(cy - tile.height / 2)), tile)

sticker(250, 235, "笑一个", "#FF8FA3", -12)
sticker(W - 260, 1170, "和气生财", "#5BBF9B", 10)

# footer small print
f_small = F(40, bold=False)
t = "— 老板办公室标语 · 温馨提示 —"
tw = d.textlength(t, font=f_small)
d.text(((W - tw) / 2, 1255), t, font=f_small, fill="#9A7B5B")

img.save(OUT)
print("SAVED", OUT, os.path.getsize(OUT), "bytes")
