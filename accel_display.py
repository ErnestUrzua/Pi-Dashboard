import os
import json
import pygame
import sys
import subprocess
from card_switcher import CardSwitcher
import smbus2
import threading
import time
import numpy as np
from collections import deque

os.environ["SDL_TOUCH_MOUSE_EVENTS"] = "0"
os.environ.setdefault("XDG_RUNTIME_DIR",  "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY",  "wayland-0")
os.environ.setdefault("SDL_VIDEODRIVER",  "wayland")

# --- Config ---
I2C_BUS   = 1
MPU_ADDR  = 0x68
SAMPLE_HZ = 100
DISPLAY_W = 1024
DISPLAY_H = 600
FPS       = 60

# Bezel margins matching labwc rc.xml
MARGIN_LEFT   = 26
MARGIN_RIGHT  = 10
MARGIN_TOP    = 0
MARGIN_BOTTOM = 24

# Content area at 75%
CONTENT_W = int((DISPLAY_W - MARGIN_LEFT - MARGIN_RIGHT) * 0.95)
CONTENT_H = int((DISPLAY_H - MARGIN_TOP  - MARGIN_BOTTOM) * 0.95)
CONTENT_X = MARGIN_LEFT + (DISPLAY_W - MARGIN_LEFT - MARGIN_RIGHT - CONTENT_W) // 2
CONTENT_Y = MARGIN_TOP  + (DISPLAY_H - MARGIN_TOP  - MARGIN_BOTTOM - CONTENT_H) // 2
HISTORY      = 100    # trail length (~1s at 100Hz)
G_SCALE      = 16384.0
G_MIN        = -1.0
G_MAX        =  1.0
CAL_FILE     = '/home/pi/.config/accel_cal.json'
MARGIN       = 55
PLOT_SIZE    = min(CONTENT_W, CONTENT_H) - MARGIN * 2   # square plot
PLOT_X_OFF   = (CONTENT_W - MARGIN * 2 - PLOT_SIZE) // 2  # centre horizontally
TRAIL_ALPHA  = 0.05   # EMA applied before storing — smooths trail and dot
DOT_SMOOTH_N = 30     # additional moving-average window on dot position

# Colors — phosphor radar
BG_COLOR     = (2,   8,  2)
GRID_COLOR   = (0,  45, 10)
AXIS_COLOR   = (0, 220, 55)
TEXT_COLOR   = (0, 255, 70)
X_COLOR      = (0, 255, 70)     # bright phosphor
Y_COLOR      = (0, 200, 50)     # mid phosphor
Z_COLOR      = (0, 140, 35)     # dim phosphor
TRAIL_HEAD   = (0, 255, 70)
TRAIL_TAIL   = (0,  30,  8)
BORDER_COLOR = (0, 180, 45)
RING_COLOR   = (0, 130, 35)     # g-force rings
GLOW_R       = 36                # bloom radius in pixels

# MPU-6050 registers
PWR_MGMT_1   = 0x6B
ACCEL_XOUT_H = 0x3B

# --- Shared state ---
accel_data = {
    'x': deque([0.0] * HISTORY, maxlen=HISTORY),
    'y': deque([0.0] * HISTORY, maxlen=HISTORY),
    'z': deque([0.0] * HISTORY, maxlen=HISTORY),
}
latest     = {'x': 0.0, 'y': 0.0, 'z': 0.0}
peak_g          = [0.0]   # peak magnitude in current window
peak_g_reset_ms = [0]     # timestamp of last window reset
def _load_cal():
    try:
        with open(CAL_FILE) as f:
            d = json.load(f)
        return {'x': float(d['x']), 'y': float(d['y']), 'z': float(d['z'])}
    except Exception:
        return {'x': 0.0, 'y': 0.0, 'z': 0.0}

cal_offset = _load_cal()
running    = True
bus        = None
glow_surf  = None   # pre-baked bloom surface, initialised in main()
data_lock  = threading.Lock()


def mpu_init():
    global bus
    bus = smbus2.SMBus(I2C_BUS)
    bus.write_byte_data(MPU_ADDR, PWR_MGMT_1, 0)


def read_word_2c(reg):
    high = bus.read_byte_data(MPU_ADDR, reg)
    low  = bus.read_byte_data(MPU_ADDR, reg + 1)
    val  = (high << 8) | low
    return val - 65536 if val >= 0x8000 else val


def sensor_thread():
    interval = 1.0 / SAMPLE_HZ
    ex = ey = ez = 0.0   # EMA state
    while running:
        t0 = time.monotonic()
        try:
            rx = read_word_2c(ACCEL_XOUT_H)
            ry = read_word_2c(ACCEL_XOUT_H + 2)
            rz = read_word_2c(ACCEL_XOUT_H + 4)
            with data_lock:
                ox, oy, oz = cal_offset['x'], cal_offset['y'], cal_offset['z']
            x_val = -(ry / G_SCALE) - ox
            y_val =  (rx / G_SCALE) - oy
            latest['x'] = -y_val   # 90° CCW
            latest['y'] =  x_val   # 90° CCW
            latest['z'] =  (rz / G_SCALE) - oz
            ex += TRAIL_ALPHA * (latest['x'] - ex)
            ey += TRAIL_ALPHA * (latest['y'] - ey)
            ez += TRAIL_ALPHA * (latest['z'] - ez)
            with data_lock:
                accel_data['x'].append(ex)
                accel_data['y'].append(ey)
                accel_data['z'].append(ez)
        except Exception as e:
            print(f"Sensor read error: {e}", file=sys.stderr)
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))


def calibrate(n_samples=50):
    """Average n_samples raw readings and store as the zero offset."""
    samples = {'x': [], 'y': [], 'z': []}
    for _ in range(n_samples):
        try:
            rx = read_word_2c(ACCEL_XOUT_H)
            ry = read_word_2c(ACCEL_XOUT_H + 2)
            rz = read_word_2c(ACCEL_XOUT_H + 4)
            samples['x'].append(-(ry / G_SCALE))
            samples['y'].append(  rx / G_SCALE)
            samples['z'].append(  rz / G_SCALE)
        except Exception:
            pass
        time.sleep(1.0 / SAMPLE_HZ)
    with data_lock:
        cal_offset['x'] = float(np.mean(samples['x'])) if samples['x'] else 0.0
        cal_offset['y'] = float(np.mean(samples['y'])) if samples['y'] else 0.0
        cal_offset['z'] = float(np.mean(samples['z'])) if samples['z'] else 0.0
        for key in accel_data:
            accel_data[key].clear()
            accel_data[key].extend([0.0] * HISTORY)
    try:
        with open(CAL_FILE, 'w') as f:
            json.dump(cal_offset, f)
    except Exception as e:
        print(f"Cal save failed: {e}", file=sys.stderr)


def g_to_sx(g, w):
    norm = (g - G_MIN) / (G_MAX - G_MIN)
    return int(MARGIN + PLOT_X_OFF + np.clip(norm, 0.0, 1.0) * PLOT_SIZE)


def g_to_sy(g, h):
    norm = (g - G_MIN) / (G_MAX - G_MIN)
    return int(h - MARGIN - np.clip(norm, 0.0, 1.0) * PLOT_SIZE)


def lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_grid(surface, font, w, h):
    plot_rect = pygame.Rect(MARGIN + PLOT_X_OFF, MARGIN, PLOT_SIZE, PLOT_SIZE)
    old_clip  = surface.get_clip()
    surface.set_clip(plot_rect)

    # Fine phosphor grid at 0.1g intervals
    FINE_STEP = 0.1
    g = G_MIN
    while g <= G_MAX + 0.001:
        sy = g_to_sy(g, h)
        sx = g_to_sx(g, w)
        is_major = abs(round(g, 1)) % 0.5 < 0.01
        col = (0, 110, 35) if is_major else (0, 65, 20)
        pygame.draw.line(surface, col,
                         (MARGIN + PLOT_X_OFF, sy),
                         (MARGIN + PLOT_X_OFF + PLOT_SIZE, sy))
        pygame.draw.line(surface, col,
                         (sx, MARGIN),
                         (sx, MARGIN + PLOT_SIZE))
        g = round(g + FINE_STEP, 2)

    surface.set_clip(old_clip)

    # plot border
    px = MARGIN + PLOT_X_OFF - 2
    py = MARGIN - 2
    pygame.draw.rect(surface, BORDER_COLOR,
                     (px, py, PLOT_SIZE + 4, PLOT_SIZE + 4), 1)

    for g in (G_MIN, 0.0, G_MAX):
        sy = g_to_sy(g, h)
        sx = g_to_sx(g, w)
        col = AXIS_COLOR if g == 0.0 else GRID_COLOR
        pygame.draw.line(surface, col, (MARGIN, sy), (w - MARGIN, sy))
        pygame.draw.line(surface, col, (sx, MARGIN), (sx, h - MARGIN))
        lbl = font.render(f"{g:+.1f}", True, GRID_COLOR)
        surface.blit(lbl, (2, sy - lbl.get_height() // 2))
        lbl = font.render(f"{g:+.1f}", True, GRID_COLOR)
        surface.blit(lbl, (sx - lbl.get_width() // 2, h - MARGIN + 4))

    cx0 = g_to_sx(0, w)
    cy0 = g_to_sy(0, h)
    plot_left  = MARGIN + PLOT_X_OFF
    plot_right = MARGIN + PLOT_X_OFF + PLOT_SIZE
    plot_top   = MARGIN
    plot_bot   = MARGIN + PLOT_SIZE

    roll_r = font.render("ROLL →",  True, X_COLOR)
    roll_l = font.render("← ROLL",  True, X_COLOR)
    brake  = font.render("BRAKE ↑", True, Y_COLOR)
    accel  = font.render("ACCEL ↓", True, Y_COLOR)
    surface.blit(roll_r, (plot_right - roll_r.get_width() - 4, cy0 - roll_r.get_height() - 2))
    surface.blit(roll_l, (plot_left + 4,                       cy0 - roll_l.get_height() - 2))
    surface.blit(brake,  (cx0 - brake.get_width() // 2,        plot_top + 2))
    surface.blit(accel,  (cx0 - accel.get_width() // 2,        plot_bot - accel.get_height() - 2))

    # Concentric g-force rings at 0.5g intervals — clipped to square plot boundary
    px_per_g = PLOT_SIZE / (G_MAX - G_MIN)
    plot_rect = pygame.Rect(MARGIN + PLOT_X_OFF, MARGIN, PLOT_SIZE, PLOT_SIZE)
    old_clip  = surface.get_clip()
    surface.set_clip(plot_rect)
    g_ring = 0.5
    while True:
        r = int(px_per_g * g_ring)
        if r < 2:
            break
        if r > PLOT_SIZE:
            break
        col = AXIS_COLOR if g_ring == 1.0 else RING_COLOR
        pygame.draw.ellipse(surface, col,
                            (cx0 - r, cy0 - r, r * 2, r * 2), 2)
        g_ring += 0.5
    surface.set_clip(old_clip)
    # ring labels drawn outside clip so they sit just inside the border
    g_ring = 0.5
    while True:
        r = int(px_per_g * g_ring)
        if r < 2 or r > PLOT_SIZE:
            break
        col = AXIS_COLOR if g_ring == 1.0 else RING_COLOR
        lbl = font.render(f"{g_ring:.1f}g", True, col)
        lx = cx0 + int(r * 0.707) + 3
        ly = cy0 - int(r * 0.707) - lbl.get_height()
        surface.blit(lbl, (lx, ly))
        g_ring += 0.5


def draw_header(surface, font, font_lg, w, fps):
    title = font.render("G - F O R C E   M O N I T O R   //   M P U - 6 0 5 0", True, TEXT_COLOR)
    surface.blit(title, (w // 2 - title.get_width() // 2, 7))
    fps_lbl = font.render(f"{fps:.0f} FPS", True, GRID_COLOR)
    surface.blit(fps_lbl, (w - MARGIN - fps_lbl.get_width(), 7))

    # peak G — rolling 100ms window
    now_ms = pygame.time.get_ticks()
    mag    = float(np.sqrt(latest['x']**2 + latest['y']**2))
    if now_ms - peak_g_reset_ms[0] >= 100:
        peak_g[0]          = mag
        peak_g_reset_ms[0] = now_ms
    elif mag > peak_g[0]:
        peak_g[0] = mag
    pk_lbl = font.render("PEAK G", True, TEXT_COLOR)
    pk_val = font_lg.render(f"{peak_g[0]:.2f}", True, X_COLOR)
    surface.blit(pk_lbl, (MARGIN, 30))
    surface.blit(pk_val, (MARGIN, 30 + pk_lbl.get_height()))


def draw_trail(surface, w, h):
    xs = list(accel_data['x'])
    ys = list(accel_data['y'])
    if len(xs) < DOT_SMOOTH_N:
        return

    gx = float(np.mean(xs[-DOT_SMOOTH_N:]))
    gy = float(np.mean(ys[-DOT_SMOOTH_N:]))

    cx = g_to_sx(gx, w)
    cy = g_to_sy(gy, h)
    ox = g_to_sx(0.0, w)   # plot centre (0 g)
    oy = g_to_sy(0.0, h)

    mag = min(1.0, float(np.sqrt(gx ** 2 + gy ** 2)) / max(abs(G_MAX), abs(G_MIN)))

    # gradient bar from centre → dot: dim at root, bright at tip, widens toward tip
    STEPS = 16
    for i in range(STEPS):
        t0 = i / STEPS
        t1 = (i + 1) / STEPS
        x0 = int(ox + (cx - ox) * t0);  y0 = int(oy + (cy - oy) * t0)
        x1 = int(ox + (cx - ox) * t1);  y1 = int(oy + (cy - oy) * t1)
        col    = lerp_color((0, 15, 5), TRAIL_HEAD, t1 ** 0.7 * (0.3 + mag * 0.7))
        lw     = max(2, int(2 + t1 * 5))
        pygame.draw.line(surface, col, (x0, y0), (x1, y1), lw)

    # centre anchor dot
    pygame.draw.circle(surface, TRAIL_HEAD, (ox, oy), 4)
    pygame.draw.circle(surface, (255, 200, 240), (ox, oy), 2)

    # bloom glow at tip
    if glow_surf is not None:
        surface.blit(glow_surf, (cx - GLOW_R, cy - GLOW_R),
                     special_flags=pygame.BLEND_ADD)

    # tip dot
    pygame.draw.circle(surface, (0, 20, 8),  (cx, cy), 9)
    pygame.draw.circle(surface, TRAIL_HEAD,  (cx, cy), 7)
    pygame.draw.circle(surface, (255, 200, 240), (cx, cy), 3)


def draw_telemetry(surface, font, w, h):
    """Three mini scrolling graphs for X, Y, Z in the space right of the square plot."""
    gw     = 180
    gx     = w - gw - 6
    nav_y  = h - 34
    gap    = 8
    gh     = (nav_y - MARGIN - gap * 2) // 3

    axes = [
        ('X', accel_data['x'], latest['x'], X_COLOR),
        ('Y', accel_data['y'], latest['y'], Y_COLOR),
        ('Z', accel_data['z'], latest['z'], Z_COLOR),
    ]

    for i, (label, data, val, color) in enumerate(axes):
        gy = MARGIN + i * (gh + gap)

        bg = pygame.Surface((gw, gh), pygame.SRCALPHA)
        bg.fill((2, 12, 4, 210))
        surface.blit(bg, (gx, gy))
        pygame.draw.rect(surface, color, (gx, gy, gw, gh), 1, border_radius=3)

        # zero centre line
        zero_y = gy + gh // 2
        pygame.draw.line(surface, (45, 8, 65),
                         (gx + 1, zero_y), (gx + gw - 1, zero_y))

        # data line
        pts = list(data)
        n   = len(pts)
        if n >= 2:
            screen_pts = []
            for j, v in enumerate(pts):
                px   = gx + 1 + int(j / (n - 1) * (gw - 2))
                norm = (v - G_MIN) / (G_MAX - G_MIN)
                py   = gy + gh - 2 - int(np.clip(norm, 0.0, 1.0) * (gh - 4))
                screen_pts.append((px, py))
            pygame.draw.lines(surface, color, False, screen_pts, 1)

        # axis label + current value
        lbl   = font.render(label,         True, color)
        val_s = font.render(f"{val:+.2f}g", True, color)
        surface.blit(lbl,   (gx + 4, gy + 3))
        surface.blit(val_s, (gx + gw - val_s.get_width() - 4, gy + 3))


def main():
    global running

    try:
        mpu_init()
    except Exception as e:
        print(f"Failed to init MPU-6050 on I2C bus {I2C_BUS} at 0x{MPU_ADDR:02X}: {e}")
        print("Check wiring and that I2C is enabled via raspi-config.")
        sys.exit(1)

    t = threading.Thread(target=sensor_thread, daemon=True)
    t.start()

    pygame.init()
    pygame.key.stop_text_input()
    screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), pygame.FULLSCREEN)
    canvas = pygame.Surface((CONTENT_W, CONTENT_H))
    pygame.display.set_caption("Accelerometer — MPU-6050")
    clock = pygame.time.Clock()
    try:
        font    = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 12)
        font_lg = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 36)
    except Exception:
        font    = pygame.font.SysFont("monospace", 13)
        font_lg = pygame.font.SysFont("monospace", 36)

    global glow_surf
    glow_surf = pygame.Surface((GLOW_R * 2, GLOW_R * 2))
    glow_surf.fill((0, 0, 0))
    for r in range(GLOW_R, 0, -1):
        t = (1.0 - r / GLOW_R) ** 1.6
        c = tuple(int(ch * t) for ch in TRAIL_HEAD)
        pygame.draw.circle(glow_surf, c, (GLOW_R, GLOW_R), r)

    switcher      = CardSwitcher(__file__, CONTENT_W, CONTENT_H)
    NAV_Y         = CONTENT_H - 34
    nav_home_rect = pygame.Rect(6,  NAV_Y + 3, 76, 28)
    nav_cal_rect  = pygame.Rect(90, NAV_Y + 3, 60, 28)
    cal_status    = {'msg': '', 'until': 0.0}

    def quit_app():
        global running
        running = False
        pygame.quit()
        sys.exit(0)

    def draw_nav_bar(surface):
        pygame.draw.rect(surface, (10, 0, 20), (0, NAV_Y, CONTENT_W, 34))
        pygame.draw.line(surface, BORDER_COLOR, (0, NAV_Y), (CONTENT_W, NAV_Y))
        pygame.draw.rect(surface, (0, 30, 10), nav_home_rect, border_radius=5)
        pygame.draw.rect(surface, BORDER_COLOR, nav_home_rect, 1, border_radius=5)
        lbl = font.render("HOME", True, X_COLOR)
        surface.blit(lbl, (nav_home_rect.x + (nav_home_rect.w - lbl.get_width()) // 2,
                           nav_home_rect.y + (nav_home_rect.h - lbl.get_height()) // 2))
        pygame.draw.rect(surface, (0, 30, 10), nav_cal_rect, border_radius=5)
        pygame.draw.rect(surface, BORDER_COLOR, nav_cal_rect, 1, border_radius=5)
        cal_lbl = font.render("CAL", True, Y_COLOR)
        surface.blit(cal_lbl, (nav_cal_rect.x + (nav_cal_rect.w - cal_lbl.get_width()) // 2,
                               nav_cal_rect.y + (nav_cal_rect.h - cal_lbl.get_height()) // 2))
        if time.monotonic() < cal_status['until']:
            msg = font.render(cal_status['msg'], True, Y_COLOR)
            surface.blit(msg, (nav_cal_rect.x + nav_cal_rect.w + 8, nav_cal_rect.y + 7))

    while True:
        for event in pygame.event.get():
            switcher.handle_event(event)
            if event.type == pygame.QUIT:
                quit_app()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                quit_app()
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                raw = (int(event.x * DISPLAY_W), int(event.y * DISPLAY_H)) \
                      if event.type == pygame.FINGERDOWN else event.pos
                pos = (raw[0] - CONTENT_X, raw[1] - CONTENT_Y)
                if nav_home_rect.collidepoint(pos):
                    running = False
                    pygame.quit()
                    os.execv(sys.executable, [sys.executable, '/home/pi/projects/launcher.py'])
                if nav_cal_rect.collidepoint(pos):
                    cal_status['msg'] = 'calibrating...'
                    cal_status['until'] = time.monotonic() + 999
                    threading.Thread(
                        target=lambda: (
                            calibrate(),
                            cal_status.update({'msg': 'zeroed!', 'until': time.monotonic() + 3.0})
                        ), daemon=True).start()

        screen.fill(BG_COLOR)
        canvas.fill(BG_COLOR)
        draw_header(canvas, font, font_lg, CONTENT_W, clock.get_fps())
        draw_grid(canvas, font, CONTENT_W, CONTENT_H)
        draw_trail(canvas, CONTENT_W, CONTENT_H)
        draw_telemetry(canvas, font, CONTENT_W, CONTENT_H)
        draw_nav_bar(canvas)
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
        with open("/home/pi/accel_crash.log", "a") as f:
            traceback.print_exc(file=f)
        raise
