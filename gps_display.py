#!/usr/bin/env python3
"""GPS Tracker with OpenStreetMap tile map."""

import os
import sys
import pygame
import threading
import queue
import urllib.request
import time
import math
import serial
import pynmea2
from collections import deque
from card_switcher import CardSwitcher

os.environ["SDL_TOUCH_MOUSE_EVENTS"] = "0"
os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
os.environ.setdefault("SDL_VIDEODRIVER", "wayland")

# --- Display config ---
DISPLAY_W    = 1024
DISPLAY_H    = 600
MARGIN_LEFT  = 26
MARGIN_RIGHT = 10
MARGIN_TOP   = 0
MARGIN_BOTTOM= 24
CONTENT_W    = int((DISPLAY_W - MARGIN_LEFT - MARGIN_RIGHT) * 0.75)
CONTENT_H    = int((DISPLAY_H - MARGIN_TOP  - MARGIN_BOTTOM) * 0.75)
CONTENT_X    = MARGIN_LEFT + (DISPLAY_W - MARGIN_LEFT - MARGIN_RIGHT - CONTENT_W) // 2
CONTENT_Y    = MARGIN_TOP  + (DISPLAY_H - MARGIN_TOP  - MARGIN_BOTTOM - CONTENT_H) // 2
FPS          = 30
TRACK_LEN    = 500

# Map tiles — Carto Dark (personal use)
TILE_SIZE    = 256
ZOOM_DEFAULT = 15
ZOOM_MIN     = 12
ZOOM_MAX     = 18
TILE_URL     = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
TILE_DIR     = os.path.expanduser("~/.cache/map_tiles")
os.makedirs(TILE_DIR, exist_ok=True)

# --- GPS config ---
GPS_PORT  = "/dev/ttyACM0"    # USB GPS receiver
GPS_BAUD  = 9600

# Colors
BG_COLOR     = (6,   0,  18)
TEXT_COLOR   = (215,160, 255)
SPEED_COLOR  = (255, 20, 180)
SAT_COLOR    = (0,  230, 200)
ALT_COLOR    = (160, 60, 255)
BORDER_COLOR = (140, 20, 190)
DIM_COLOR    = (80,  30, 130)
TRACK_HEAD   = (255, 20, 180)
TRACK_TAIL   = (80,   0, 110)
COMPASS_BG   = (18,   0,  32)
GRID_COLOR   = (55,   8,  80)

# --- Shared GPS state ---
gps = {
    'lat': None, 'lon': None,
    'speed_kn': 0.0, 'heading': 0.0,
    'alt_m': None, 'sats': 0,
    'fix': 0, 'valid': False,
    'pdop': 99.9, 'time_utc': '',
}
track   = deque(maxlen=TRACK_LEN)
running = True

# --- Tile state ---
tile_cache   = {}
tile_pending = set()
tile_lock    = threading.Lock()
tile_queue   = queue.Queue()
tile_done_q  = queue.Queue()


# ── GPS thread ────────────────────────────────────────────────────────────────
def gps_thread():
    while running:
        try:
            with serial.Serial(GPS_PORT, GPS_BAUD, timeout=1) as port:
                while running:
                    try:
                        line = port.readline().decode('ascii', errors='replace').strip()
                        msg  = pynmea2.parse(line)
                        if isinstance(msg, pynmea2.types.talker.RMC):
                            gps['valid']    = msg.status == 'A'
                            gps['speed_kn'] = float(msg.spd_over_grnd or 0)
                            if msg.true_course:
                                gps['heading'] = float(msg.true_course)
                            if gps['valid'] and msg.latitude and msg.longitude:
                                gps['lat'] = msg.latitude
                                gps['lon'] = msg.longitude
                                if not track or (
                                    abs(msg.latitude  - track[-1][0]) > 0.00001 or
                                    abs(msg.longitude - track[-1][1]) > 0.00001
                                ):
                                    track.append((msg.latitude, msg.longitude))
                            if msg.datetime:
                                gps['time_utc'] = msg.datetime.strftime('%H:%M:%S UTC')
                        elif isinstance(msg, pynmea2.types.talker.GGA):
                            gps['fix']  = int(msg.gps_qual or 0)
                            gps['sats'] = int(msg.num_sats or 0)
                            if msg.altitude:
                                gps['alt_m'] = float(msg.altitude)
                        elif isinstance(msg, pynmea2.types.talker.GSA):
                            if msg.pdop:
                                gps['pdop'] = float(msg.pdop)
                    except (pynmea2.ParseError, ValueError):
                        pass
        except serial.SerialException as e:
            print(f"GPS serial error: {e}", file=sys.stderr)
            time.sleep(3)


# ── Tile helpers ──────────────────────────────────────────────────────────────
def lat_lon_to_tile_float(lat, lon, zoom):
    n     = 2 ** zoom
    x     = (lon + 180) / 360 * n
    lat_r = math.radians(lat)
    y     = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    return x, y


def request_tile(z, tx, ty):
    key = (z, tx, ty)
    with tile_lock:
        if key in tile_cache or key in tile_pending:
            return
        tile_pending.add(key)
    tile_queue.put(key)


def tile_loader_thread():
    while running:
        try:
            key = tile_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        z, tx, ty = key
        n  = 2 ** z
        tx = tx % n
        ty = max(0, min(n - 1, ty))
        cache_path = os.path.join(TILE_DIR, f"{z}_{tx}_{ty}.png")
        path = None
        try:
            if os.path.exists(cache_path):
                path = cache_path
            else:
                url = TILE_URL.format(z=z, x=tx, y=ty)
                req = urllib.request.Request(
                    url, headers={'User-Agent': 'RPiCarDashboard/1.0'})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = resp.read()
                with open(cache_path, 'wb') as f:
                    f.write(data)
                path = cache_path
        except Exception as e:
            print(f"Tile error {key}: {e}", file=sys.stderr)
        tile_done_q.put((key, path))


def process_tile_done():
    count = 0
    while not tile_done_q.empty() and count < 6:
        key, path = tile_done_q.get_nowait()
        surf = None
        try:
            if path:
                surf = pygame.image.load(path).convert()
        except Exception:
            pass
        with tile_lock:
            tile_cache[key] = surf
            tile_pending.discard(key)
        count += 1


# ── Drawing ───────────────────────────────────────────────────────────────────
def lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def panel_bg(surface, rect, alpha=210):
    x, y, w, h = rect
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((6, 0, 18, alpha))
    surface.blit(bg, (x, y))
    pygame.draw.rect(surface, BORDER_COLOR, rect, 1, border_radius=8)


def draw_map(surface, lat, lon, zoom):
    cx = CONTENT_W // 2
    cy = CONTENT_H // 2  # map center = canvas center

    tx_f, ty_f = lat_lon_to_tile_float(lat, lon, zoom)
    tx_int = int(tx_f)
    ty_int = int(ty_f)
    origin_x = cx - int((tx_f - tx_int) * TILE_SIZE)
    origin_y = cy - int((ty_f - ty_int) * TILE_SIZE)

    for dy in range(-3, 4):
        for dx in range(-3, 5):
            tx = tx_int + dx
            ty = ty_int + dy
            sx = origin_x + dx * TILE_SIZE
            sy = origin_y + dy * TILE_SIZE
            if sx + TILE_SIZE < 0 or sx > CONTENT_W:
                continue
            if sy + TILE_SIZE < 0 or sy > CONTENT_H:
                continue
            key = (zoom, tx, ty)
            with tile_lock:
                surf = tile_cache.get(key, 'MISSING')
            if surf == 'MISSING':
                request_tile(zoom, tx, ty)
                pygame.draw.rect(surface, (14, 6, 28),
                                 (sx, sy, TILE_SIZE, TILE_SIZE))
            elif surf is None:
                pygame.draw.rect(surface, (28, 0, 0),
                                 (sx, sy, TILE_SIZE, TILE_SIZE))
            else:
                surface.blit(surf, (sx, sy))

    # breadcrumb trail
    def to_screen(lt, ln):
        ptx, pty = lat_lon_to_tile_float(lt, ln, zoom)
        return (cx + int((ptx - tx_f) * TILE_SIZE),
                cy + int((pty - ty_f) * TILE_SIZE))

    pts = list(track)
    n = len(pts)
    if n >= 2:
        for i in range(1, n):
            t  = i / (n - 1)
            c  = lerp_color(TRACK_TAIL, TRACK_HEAD, t ** 1.5)
            p1 = to_screen(*pts[i - 1])
            p2 = to_screen(*pts[i])
            if ((-20 < p1[0] < CONTENT_W + 20 and -20 < p1[1] < CONTENT_H + 20) or
                    (-20 < p2[0] < CONTENT_W + 20 and -20 < p2[1] < CONTENT_H + 20)):
                pygame.draw.line(surface, c, p1, p2, 3)

    # position dot at canvas centre
    pygame.draw.circle(surface, (30, 0, 50),    (cx, cy), 11)
    pygame.draw.circle(surface, SPEED_COLOR,     (cx, cy),  8)
    pygame.draw.circle(surface, (255, 200, 240), (cx, cy),  3)

    h_rad = math.radians(gps['heading'])
    al    = 24
    tip   = (cx + int(al * math.sin(h_rad)), cy - int(al * math.cos(h_rad)))
    pygame.draw.line(surface, SPEED_COLOR, (cx, cy), tip, 3)


def draw_compass(surface, font, cx, cy, radius, heading):
    pygame.draw.circle(surface, COMPASS_BG,   (cx, cy), radius)
    pygame.draw.circle(surface, BORDER_COLOR, (cx, cy), radius, 1)
    for label, deg in [('N', 0), ('E', 90), ('S', 180), ('W', 270)]:
        rad = math.radians(deg - heading)
        tx  = cx + int((radius - 16) * math.sin(rad))
        ty  = cy - int((radius - 16) * math.cos(rad))
        col = SPEED_COLOR if label == 'N' else DIM_COLOR
        lbl = font.render(label, True, col)
        surface.blit(lbl, (tx - lbl.get_width() // 2, ty - lbl.get_height() // 2))
    for deg in range(0, 360, 30):
        rad = math.radians(deg - heading)
        r0, r1 = radius - 7, radius - 2
        x0 = cx + int(r0 * math.sin(rad)); y0 = cy - int(r0 * math.cos(rad))
        x1 = cx + int(r1 * math.sin(rad)); y1 = cy - int(r1 * math.cos(rad))
        pygame.draw.line(surface, GRID_COLOR, (x0, y0), (x1, y1))
    nl = radius - 20
    pygame.draw.polygon(surface, SPEED_COLOR,
                        [(cx, cy - nl), (cx - 5, cy + 10), (cx + 5, cy + 10)])
    pygame.draw.polygon(surface, DIM_COLOR,
                        [(cx, cy + nl - 8), (cx - 4, cy - 6), (cx + 4, cy - 6)])
    pygame.draw.circle(surface, BORDER_COLOR, (cx, cy), 5)
    pygame.draw.circle(surface, BG_COLOR,     (cx, cy), 3)
    hdg = font.render(f"{heading:.0f}°", True, TEXT_COLOR)
    surface.blit(hdg, (cx - hdg.get_width() // 2, cy + radius + 3))


def draw_overlays(surface, font, font_lg, zoom):
    NAV_Y = CONTENT_H - 34

    # Compass — top left
    cr = 48
    cx, cy = 58, 70
    panel_bg(surface, (cx - cr - 4, cy - cr - 4, (cr + 4) * 2, cr * 2 + 22))
    draw_compass(surface, font, cx, cy, cr, gps['heading'])

    # Speed — bottom centre
    speed_mph = gps['speed_kn'] * 1.15078
    sw, sh = 120, 64
    sx = CONTENT_W // 2 - sw // 2
    sy = NAV_Y - sh - 6
    panel_bg(surface, (sx, sy, sw, sh))
    spd_s  = font_lg.render(f"{speed_mph:.0f}", True, SPEED_COLOR)
    unit_s = font.render("MPH", True, DIM_COLOR)
    surface.blit(spd_s,  (CONTENT_W // 2 - spd_s.get_width()  // 2, sy + 4))
    surface.blit(unit_s, (CONTENT_W // 2 - unit_s.get_width() // 2,
                           sy + sh - unit_s.get_height() - 4))

    # Stats — top right
    fix_labels = {0: ("NO FIX", (180, 30, 30)),
                  1: ("GPS FIX", SAT_COLOR),
                  2: ("DGPS",    (255, 200, 0))}
    fix_txt, fix_col = fix_labels.get(gps['fix'], ("NO FIX", (180, 30, 30)))
    lat_str = f"{gps['lat']:.5f}°" if gps['lat'] is not None else "---"
    lon_str = f"{gps['lon']:.5f}°" if gps['lon'] is not None else "---"
    alt_str = f"{gps['alt_m']:.0f}m"  if gps['alt_m'] is not None else "---"
    lines = [
        (fix_txt,                  fix_col),
        (f"SAT {gps['sats']:02d}", SAT_COLOR),
        (f"LAT {lat_str}",         TEXT_COLOR),
        (f"LON {lon_str}",         TEXT_COLOR),
        (f"ALT {alt_str}",         ALT_COLOR),
        (f"PDOP {gps['pdop']:.1f}",DIM_COLOR),
    ]
    stw, sth = 180, len(lines) * 18 + 8
    stx = CONTENT_W - stw - 4
    sty = 4
    panel_bg(surface, (stx, sty, stw, sth))
    for i, (txt, col) in enumerate(lines):
        s = font.render(txt, True, col)
        surface.blit(s, (stx + 5, sty + 5 + i * 18))

    # Zoom indicator
    zl = font.render(f"Z{zoom}", True, DIM_COLOR)
    surface.blit(zl, (CONTENT_W // 2 - zl.get_width() // 2, 5))


def draw_nav(surface, font, nav_home_rect, nav_zout_rect, nav_zin_rect):
    NAV_Y = CONTENT_H - 34
    pygame.draw.rect(surface, (6, 0, 12), (0, NAV_Y, CONTENT_W, 34))
    pygame.draw.line(surface, BORDER_COLOR, (0, NAV_Y), (CONTENT_W, NAV_Y))
    for rect, label in [(nav_home_rect, "HOME"),
                        (nav_zout_rect, "Z -"),
                        (nav_zin_rect,  "Z +")]:
        pygame.draw.rect(surface, (30, 0, 45), rect, border_radius=5)
        pygame.draw.rect(surface, BORDER_COLOR, rect, 1, border_radius=5)
        lbl = font.render(label, True, SPEED_COLOR)
        surface.blit(lbl, (rect.x + (rect.w - lbl.get_width())  // 2,
                           rect.y + (rect.h - lbl.get_height()) // 2))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global running

    pygame.init()
    pygame.key.stop_text_input()
    screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), pygame.FULLSCREEN)
    canvas = pygame.Surface((CONTENT_W, CONTENT_H))
    pygame.display.set_caption("GPS Tracker")
    clock  = pygame.time.Clock()

    threading.Thread(target=gps_thread,         daemon=True).start()
    threading.Thread(target=tile_loader_thread, daemon=True).start()

    try:
        font    = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 12)
        font_lg = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 44)
    except Exception:
        font    = pygame.font.SysFont("monospace", 12)
        font_lg = pygame.font.SysFont("monospace", 44)

    switcher = CardSwitcher(__file__, DISPLAY_W, DISPLAY_H)
    zoom     = ZOOM_DEFAULT

    NAV_Y         = CONTENT_H - 34
    nav_home_rect = pygame.Rect(6,   NAV_Y + 3, 70, 28)
    nav_zout_rect = pygame.Rect(84,  NAV_Y + 3, 54, 28)
    nav_zin_rect  = pygame.Rect(146, NAV_Y + 3, 54, 28)

    while True:
        for event in pygame.event.get():
            switcher.handle_event(event)
            if event.type == pygame.QUIT:
                running = False; pygame.quit(); sys.exit(0)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False; pygame.quit(); sys.exit(0)
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                raw = (int(event.x * DISPLAY_W), int(event.y * DISPLAY_H)) \
                      if event.type == pygame.FINGERDOWN else event.pos
                pos = (raw[0] - CONTENT_X, raw[1] - CONTENT_Y)
                if nav_home_rect.collidepoint(pos):
                    running = False
                    pygame.quit()
                    os.execv(sys.executable,
                             [sys.executable, '/home/pi/projects/launcher.py'])
                if nav_zout_rect.collidepoint(pos):
                    zoom = max(ZOOM_MIN, zoom - 1)
                if nav_zin_rect.collidepoint(pos):
                    zoom = min(ZOOM_MAX, zoom + 1)

        process_tile_done()

        screen.fill(BG_COLOR)
        canvas.fill(BG_COLOR)

        lat = gps['lat']
        lon = gps['lon']

        if lat is None or lon is None:
            msg  = font.render("AWAITING GPS FIX", True, (180, 30, 30))
            sats = font.render(f"SAT {gps['sats']:02d}", True, DIM_COLOR)
            canvas.blit(msg,  (CONTENT_W // 2 - msg.get_width()  // 2,
                               CONTENT_H // 2 - 14))
            canvas.blit(sats, (CONTENT_W // 2 - sats.get_width() // 2,
                               CONTENT_H // 2 + 4))
        else:
            draw_map(canvas, lat, lon, zoom)
            draw_overlays(canvas, font, font_lg, zoom)

        draw_nav(canvas, font, nav_home_rect, nav_zout_rect, nav_zin_rect)
        switcher.update()
        switcher.draw(canvas, font)

        screen.blit(canvas, (CONTENT_X, CONTENT_Y))
        pygame.display.flip()
        clock.tick(FPS)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        with open("/home/pi/gps_crash.log", "a") as f:
            traceback.print_exc(file=f)
        raise
