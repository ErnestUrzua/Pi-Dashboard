#!/usr/bin/env python3
"""Synthwave boot splash 1024x600."""
import math, random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1024, 600
HORIZON = int(H * 0.46)

# ── sky gradient ──────────────────────────────────────────────────────────────
pixels = []
for y in range(H):
    if y <= HORIZON:
        t = y / HORIZON
        r = int(15  + t * 130)
        g = int(0   + t * 15)
        b = int(80  + t * 160)
    else:
        t = (y - HORIZON) / (H - HORIZON)
        r = int(90  - t * 70)
        g = 0
        b = int(110 - t * 85)
    for x in range(W):
        pixels.append((r, g, b))

img = Image.new("RGB", (W, H))
img.putdata(pixels)
draw = ImageDraw.Draw(img)

# ── sun glow (separate image, gaussian blur, paste) ───────────────────────────
SUN_CX, SUN_CY = W // 2, HORIZON
SUN_R = 115

glow = Image.new("RGB", (W, H), (0, 0, 0))
gd = ImageDraw.Draw(glow)
# paint concentric filled ellipses from big → small, bright → dim
for i in range(60, 0, -1):
    frac = i / 60
    r2 = int(SUN_R + i * 4.5)
    cr = int(204 * frac)
    cg = int(32  * frac)
    cb = int(168 * frac)
    gd.ellipse([SUN_CX - r2, SUN_CY - r2, SUN_CX + r2, SUN_CY + r2],
               fill=(cr, cg, cb))

glow_b = glow.filter(ImageFilter.GaussianBlur(radius=32))

# additive blend: img + glow (clamped)
img_data  = list(img.getdata())
glow_data = list(glow_b.getdata())
merged = []
for (r1,g1,b1), (r2,g2,b2) in zip(img_data, glow_data):
    merged.append((min(255, r1+r2), min(255, g1+g2), min(255, b1+b2)))
img.putdata(merged)
draw = ImageDraw.Draw(img)

# sun body stripes
for y in range(SUN_CY - SUN_R, SUN_CY + SUN_R + 1):
    if y < 0 or y >= H: continue
    dy = y - SUN_CY
    dx = int(math.sqrt(max(0, SUN_R**2 - dy**2)))
    if dx == 0: continue
    band = abs(dy)
    spacing = max(2, int(2 + band * 0.09))
    if (band // spacing) % 2 == 0:
        t = max(0, dy) / SUN_R
        draw.line([(SUN_CX - dx, y), (SUN_CX + dx, y)],
                  fill=(255, int(60 + t*20), int(240 - t*190)))

# ── perspective grid ──────────────────────────────────────────────────────────
VX, VY = W // 2, HORIZON

# verticals
for i in range(-20, 21):
    base_x = W//2 + i * (W // 40)
    xf = int(VX + (base_x - VX) * 0.01)
    xn = base_x
    fade = max(0.2, 1.0 - (abs(i) / 20) ** 0.6)
    draw.line([(xf, VY), (xn, H)],
              fill=(int(220*fade), 0, int(255*fade)), width=2)

# horizontals
for i in range(1, 15):
    t = (i / 14) ** 1.6
    y = int(HORIZON + t * (H - HORIZON))
    fade = (i / 14) ** 0.35
    tc = (y - HORIZON) / max(1, H - HORIZON)
    x0 = int(VX - tc * W * 0.51)
    x1 = int(VX + tc * W * 0.51)
    col = (int(255*fade), 0, int(220*fade)) if i%2==0 else (0, int(230*fade), int(255*fade))
    draw.line([(x0, y), (x1, y)], fill=col, width=2)

# ── stars ─────────────────────────────────────────────────────────────────────
random.seed(42)
for _ in range(260):
    sx = random.randint(0, W-1)
    sy = random.randint(0, HORIZON - 5)
    br = random.randint(180, 255)
    sz = random.choice([1,1,1,2])
    tint = random.choice([(br,br,br),(br,br//3,255),(255,br//3,br)])
    draw.ellipse([sx, sy, sx+sz, sy+sz], fill=tint)

# ── text: glow then sharp ─────────────────────────────────────────────────────
try:
    font_big = ImageFont.truetype("/home/pi/.fonts/Menlo.ttc", 80)
    font_sm  = ImageFont.truetype("/home/pi/.fonts/Menlo.ttc", 20)
except Exception:
    font_big = ImageFont.load_default()
    font_sm  = font_big

TITLE = "DASHBOARD"
SUB   = "CAR SYSTEM  //  INITIALIZING"
bb = font_big.getbbox(TITLE)
tw, th = bb[2]-bb[0], bb[3]-bb[1]
tx = (W - tw) // 2
ty = int(H * 0.70)

# subtle glow — single render, small blur, low additive weight
tg1 = Image.new("RGB", (W, H), (0,0,0))
td1 = ImageDraw.Draw(tg1)
td1.text((tx, ty), TITLE, font=font_big, fill=(120, 0, 80))
tg1_b = tg1.filter(ImageFilter.GaussianBlur(radius=8))

img_d = list(img.getdata())
tg1_d = list(tg1_b.getdata())
merged2 = [(min(255,r1+r2//3), min(255,g1+g2//3), min(255,b1+b2//3))
           for (r1,g1,b1),(r2,g2,b2) in zip(img_d, tg1_d)]
img.putdata(merged2)
draw = ImageDraw.Draw(img)

# sharp title
draw.text((tx, ty), TITLE, font=font_big, fill=(210, 230, 255))
bb2 = font_sm.getbbox(SUB)
sw = bb2[2]-bb2[0]
draw.text(((W-sw)//2, ty+th+12), SUB, font=font_sm, fill=(0, 220, 255))

# dividers
lx0, lx1 = tx-24, tx+tw+24
draw.line([(lx0, ty-14),(lx1, ty-14)], fill=(255, 0, 200), width=2)
draw.line([(lx0, ty+th+8),(lx1, ty+th+8)], fill=(0, 200, 255), width=1)

# ── scanlines ─────────────────────────────────────────────────────────────────
scan = Image.new("RGBA", (W, H), (0,0,0,0))
sd = ImageDraw.Draw(scan)
for y in range(0, H, 3):
    sd.line([(0,y),(W,y)], fill=(0,0,0,28))
img = img.convert("RGBA")
img = Image.alpha_composite(img, scan).convert("RGB")

img.save("/home/pi/projects/dashboard_splash.png")
print("Done.")
