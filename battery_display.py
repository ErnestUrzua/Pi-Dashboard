#!/usr/bin/env python3
"""Battery voltage monitor — 3 channels on ADS1115 0x48."""

import os
import sys
import time
import math
import threading
import subprocess
import pygame
from card_switcher import CardSwitcher

os.environ["SDL_TOUCH_MOUSE_EVENTS"] = "0"
os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
os.environ.setdefault("SDL_VIDEODRIVER", "wayland")

DISPLAY_W = 1024
DISPLAY_H = 600
FPS       = 60

# Bezel margins matching labwc rc.xml
MARGIN_LEFT   = 26
MARGIN_RIGHT  = 10
MARGIN_TOP    = 0
MARGIN_BOTTOM = 24

# Content area rendered at 75%
CONTENT_W = int((DISPLAY_W - MARGIN_LEFT - MARGIN_RIGHT) * 0.75)
CONTENT_H = int((DISPLAY_H - MARGIN_TOP  - MARGIN_BOTTOM) * 0.75)
# Top-left offset to center content within visible area
CONTENT_X = MARGIN_LEFT + (DISPLAY_W - MARGIN_LEFT - MARGIN_RIGHT - CONTENT_W) // 2
CONTENT_Y = MARGIN_TOP  + (DISPLAY_H - MARGIN_TOP  - MARGIN_BOTTOM - CONTENT_H) // 2

BG_COLOR     = (6, 0, 18)
BORDER_COLOR = (200, 60, 255)
TEXT_COLOR   = (215, 160, 255)
DIM_COLOR    = (90, 50, 120)

MIN_V = 13.0
MAX_V = 14.4

BATTERIES = ["BATTERY 1", "BATTERY 2", "BATTERY 3"]

# --- ADC config (unused while USE_DUMMY = True) ---
USE_DUMMY  = False
I2C_BUS    = 1
ADC_ADDR   = 0x48
R1, R2     = 10000, 3300
DIVIDER    = (R1 + R2) / R2
PGA_GAIN   = 0b001
LSB_mV     = 0.125
MUX_SINGLE = [0b100, 0b101, 0b110, 0b111]

DUMMY_VOLTS = [12.80, 11.40, 13.90]

# shared state
readings = list(DUMMY_VOLTS)
readings_lock = threading.Lock()
i2c_lock = threading.Lock()
running = True

psu_status = {'alarm': 0, 'throttled': 0}
psu_lock   = threading.Lock()


def psu_monitor_thread():
    while running:
        try:
            with open('/sys/class/hwmon/hwmon3/in0_lcrit_alarm') as f:
                alarm = int(f.read().strip())
        except Exception:
            alarm = -1
        try:
            raw = subprocess.check_output(['vcgencmd', 'get_throttled'],
                                          text=True).strip()
            throttled = int(raw.split('=')[1], 16)
        except Exception:
            throttled = -1
        with psu_lock:
            psu_status['alarm']     = alarm
            psu_status['throttled'] = throttled
        time.sleep(1.0)


def read_adc_channel(bus, ch):
    mux  = MUX_SINGLE[ch]
    high = (1 << 7) | (mux << 4) | (PGA_GAIN << 1) | 1
    low  = 0b11100011  # 860 SPS
    bus.write_i2c_block_data(ADC_ADDR, 0x01, [high, low])
    # Poll OS bit — avoids inaccurate sleep; conversion done when bit goes high
    for _ in range(30):
        cfg = bus.read_i2c_block_data(ADC_ADDR, 0x01, 2)
        if cfg[0] & 0x80:
            break
    data = bus.read_i2c_block_data(ADC_ADDR, 0x00, 2)
    raw  = (data[0] << 8) | data[1]
    if raw > 32767:
        raw -= 65536
    return (raw * LSB_mV / 1000.0) * DIVIDER


def sensor_thread(bus):
    while running:
        try:
            new = []
            for ch in (0, 1, 2):
                with i2c_lock:
                    new.append((ch, read_adc_channel(bus, ch)))
            with readings_lock:
                for ch, v in new:
                    readings[ch] = v
        except Exception as e:
            print(f"ADC error: {e}", file=sys.stderr)
        time.sleep(0.033)  # ~30Hz poll rate


def volt_color(v):
    pct = (v - MIN_V) / (MAX_V - MIN_V)
    if pct >= 0.65:
        return (0, 230, 120)
    elif pct >= 0.30:
        return (255, 180, 0)
    else:
        return (255, 40, 40)


def draw_battery_panel(surface, fonts, rect, label, volts):
    font_lg, font_md, font_sm = fonts
    pct   = max(0.0, min(1.0, (volts - MIN_V) / (MAX_V - MIN_V)))
    color = volt_color(volts)
    cx    = rect.x + rect.w // 2

    pygame.draw.rect(surface, (14, 0, 26), rect, border_radius=14)
    pygame.draw.rect(surface, color, rect, 2, border_radius=14)

    # Label
    lbl = font_md.render(label, True, DIM_COLOR)
    surface.blit(lbl, (cx - lbl.get_width() // 2, rect.y + 20))

    # Voltage readout
    v_surf = font_lg.render(f"{volts:.2f}", True, color)
    u_surf = font_md.render("V", True, DIM_COLOR)
    total  = v_surf.get_width() + u_surf.get_width() + 4
    vx = cx - total // 2
    vy = rect.y + 50
    surface.blit(v_surf, (vx, vy))
    surface.blit(u_surf, (vx + v_surf.get_width() + 4,
                          vy + v_surf.get_height() - u_surf.get_height() - 4))

    # Battery graphic
    batt_w = 90
    batt_h = 265
    batt_x = cx - batt_w // 2
    batt_y = rect.y + 120
    term_w = 28
    term_h = 10

    # Terminal nub
    pygame.draw.rect(surface, DIM_COLOR,
                     (cx - term_w // 2, batt_y - term_h, term_w, term_h),
                     border_radius=3)

    # Body bg
    pygame.draw.rect(surface, (25, 0, 42),
                     (batt_x, batt_y, batt_w, batt_h), border_radius=10)

    # Fill from bottom
    fill_h = int(batt_h * pct)
    if fill_h > 0:
        fill_y = batt_y + batt_h - fill_h
        pygame.draw.rect(surface, color,
                         (batt_x + 3, fill_y, batt_w - 6, fill_h),
                         border_radius=8)

    # Body border
    pygame.draw.rect(surface, BORDER_COLOR,
                     (batt_x, batt_y, batt_w, batt_h), 2, border_radius=10)

    # Percentage inside battery
    pct_surf = font_md.render(f"{pct * 100:.0f}%", True, TEXT_COLOR)
    surface.blit(pct_surf,
                 (cx - pct_surf.get_width() // 2,
                  batt_y + batt_h // 2 - pct_surf.get_height() // 2))

    # Range labels alongside battery
    mx_s = font_sm.render(f"{MAX_V:.1f}V", True, DIM_COLOR)
    mn_s = font_sm.render(f"{MIN_V:.1f}V", True, DIM_COLOR)
    surface.blit(mx_s, (batt_x + batt_w + 8, batt_y))
    surface.blit(mn_s, (batt_x + batt_w + 8, batt_y + batt_h - mn_s.get_height()))

    # Status
    status = "GOOD" if pct >= 0.65 else ("LOW" if pct >= 0.30 else "CRITICAL")
    st_surf = font_md.render(status, True, color)
    surface.blit(st_surf, (cx - st_surf.get_width() // 2,
                           batt_y + batt_h + 14))


SEGS    = 10
SEG_VMIN = 14.0
SEG_VMAX = 14.4


def draw_seg_panel(surface, fonts, rect, label, volts):
    font_lg, font_md, font_sm = fonts
    cx = rect.x + rect.w // 2
    color = volt_color(volts)

    # Header: label + voltage
    lbl_s = font_md.render(label, True, DIM_COLOR)
    surface.blit(lbl_s, (cx - lbl_s.get_width() // 2, rect.y + 10))
    v_s = font_lg.render(f"{volts:.2f}", True, color)
    u_s = font_md.render("V", True, DIM_COLOR)
    vx = cx - (v_s.get_width() + u_s.get_width() + 4) // 2
    vy = rect.y + 32
    surface.blit(v_s, (vx, vy))
    surface.blit(u_s, (vx + v_s.get_width() + 4,
                        vy + v_s.get_height() - u_s.get_height() - 2))

    step = (SEG_VMAX - SEG_VMIN) / SEGS
    lit_count = int(min(SEGS, max(0, (volts - SEG_VMIN) / (SEG_VMAX - SEG_VMIN) * SEGS)))

    top_h   = 78
    seg_area_y = rect.y + top_h
    seg_area_h = rect.h - top_h
    seg_gap = 3
    seg_h   = (seg_area_h - (SEGS - 1) * seg_gap) // SEGS
    label_w = 34
    seg_w   = rect.w - 14 - label_w
    seg_x   = rect.x + 7

    for i in range(SEGS):
        seg_y = seg_area_y + seg_area_h - (i + 1) * (seg_h + seg_gap) + seg_gap
        t = i / (SEGS - 1)
        r = int(255 * (1.0 - t))
        g = int(220 * t)
        lit = i < lit_count
        col = (r, g, 20) if lit else (r // 7, g // 7, 5)
        pygame.draw.rect(surface, col, (seg_x, seg_y, seg_w, seg_h), border_radius=3)
        seg_v = SEG_VMIN + i * step
        v_lbl = font_sm.render(f"{seg_v:.1f}", True, TEXT_COLOR if lit else DIM_COLOR)
        surface.blit(v_lbl, (seg_x + seg_w + 4,
                              seg_y + seg_h // 2 - v_lbl.get_height() // 2))


def draw_vu_panel(surface, fonts, rect, label, volts):
    font_lg, font_md, font_sm = fonts
    cx    = rect.x + rect.w // 2
    color = volt_color(volts)
    pct   = max(0.0, min(1.0, (volts - MIN_V) / (MAX_V - MIN_V)))

    # Panel shell
    pygame.draw.rect(surface, (4, 0, 8), rect, border_radius=14)
    pygame.draw.rect(surface, (200, 60, 255), rect, 2, border_radius=14)

    # Circular face — center slightly above mid to leave room for label
    face_r = rect.w // 2 - 8
    cy     = rect.y + face_r + 10
    pygame.draw.circle(surface, (4, 0, 10), (cx, cy), face_r)
    pygame.draw.circle(surface, (50, 20, 70), (cx, cy), face_r, 2)

    # Arc geometry — 210° sweep, bottom-left to bottom-right through top
    N_LEDS  = 10
    SWEEP   = 210.0
    A_START = -SWEEP / 2.0        # degrees from straight up
    led_r   = int(face_r * 0.76)
    arc_length = 2 * math.pi * led_r * (SWEEP / 360.0)
    spacing    = arc_length / (N_LEDS - 1)
    dot_outer  = max(4, int(spacing * 0.30 * 0.75))
    dot_inner  = max(2, dot_outer - 2)
    lit_count  = int(round(pct * N_LEDS))

    # Thin arc track
    arc_rect = pygame.Rect(cx - led_r, cy - led_r, led_r * 2, led_r * 2)
    pg_s = math.radians(90 - (A_START + SWEEP))
    pg_e = math.radians(90 - A_START)
    pygame.draw.arc(surface, (0, 60, 25), arc_rect, pg_s, pg_e, 1)

    label_r = led_r - dot_outer - 13

    for i in range(N_LEDS):
        t       = i / (N_LEDS - 1)
        ang_deg = A_START + t * SWEEP
        ang_rad = math.radians(ang_deg)
        lx      = cx + int(led_r * math.sin(ang_rad))
        ly      = cy - int(led_r * math.cos(ang_rad))

        base = (0, 255, 90)

        lit = i < lit_count

        # Short radial line from track inward to LED
        line_r = led_r - dot_outer - 3
        lline_x = cx + int(line_r * math.sin(ang_rad))
        lline_y = cy - int(line_r * math.cos(ang_rad))
        dim  = (base[0]//2, base[1]//2, base[2]//2)
        col  = base if lit else dim
        halo = (base[0]//4, base[1]//4, base[2]//4) if lit else (base[0]//8, base[1]//8, base[2]//8)

        # halo glow behind dot
        pygame.draw.circle(surface, halo, (lx, ly), dot_outer + 4)
        pygame.draw.line(surface, col, (lx, ly), (lline_x, lline_y), 1)
        pygame.draw.circle(surface, col, (lx, ly), dot_outer, 2)
        pygame.draw.circle(surface, col, (lx, ly), dot_inner)

        # Radial voltage label — every other LED
        if True:
            v     = MIN_V + t * (MAX_V - MIN_V)
            lbx   = cx + int(label_r * math.sin(ang_rad))
            lby   = cy - int(label_r * math.cos(ang_rad))
            tc    = (0, 200, 70)
            lbl_s = font_sm.render(f"{v:.2f}", True, tc)
            rot_s = pygame.transform.rotate(lbl_s, -ang_deg)
            surface.blit(rot_s, (lbx - rot_s.get_width() // 2,
                                 lby - rot_s.get_height() // 2))

    # Voltage + label stacked below the arc
    v_s  = font_lg.render(f"{volts:.2f}", True, color)
    u_s  = font_md.render("V", True, DIM_COLOR)
    lbl  = font_sm.render(label, True, DIM_COLOR)
    below_y = cy + face_r + 6
    vx   = cx - (v_s.get_width() + u_s.get_width() + 3) // 2
    surface.blit(v_s,  (vx, below_y))
    surface.blit(u_s,  (vx + v_s.get_width() + 3,
                         below_y + v_s.get_height() - u_s.get_height() - 2))
    surface.blit(lbl,  (cx - lbl.get_width() // 2,
                         below_y + v_s.get_height() + 2))


def main():
    global running

    threading.Thread(target=psu_monitor_thread, daemon=True).start()

    if not USE_DUMMY:
        import smbus2
        bus = smbus2.SMBus(I2C_BUS)
        threading.Thread(target=sensor_thread, args=(bus,), daemon=True).start()

    pygame.init()
    pygame.key.stop_text_input()
    screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), pygame.FULLSCREEN)
    canvas = pygame.Surface((CONTENT_W, CONTENT_H))
    pygame.display.set_caption("Battery Monitor")
    clock = pygame.time.Clock()

    try:
        font_lg = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 48)
        font_md = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 16)
        font_sm = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 12)
    except Exception:
        font_lg = pygame.font.SysFont("monospace", 48)
        font_md = pygame.font.SysFont("monospace", 16)
        font_sm = pygame.font.SysFont("monospace", 12)

    fonts = (font_lg, font_md, font_sm)
    switcher = CardSwitcher(__file__, DISPLAY_W, DISPLAY_H)

    HEADER_H      = 40
    NAV_Y         = CONTENT_H - 34
    nav_home_rect = pygame.Rect(6, NAV_Y + 3, 76, 28)
    nav_mode_rect = pygame.Rect(90, NAV_Y + 3, 76, 28)
    viz_mode = 0  # 0 = battery graphic, 1 = segmented bars, 2 = VU meter

    gap     = 20
    panel_w = (CONTENT_W - gap * 4) // 3
    panel_y = HEADER_H + 10
    panel_h = NAV_Y - panel_y - 10
    panels  = [
        pygame.Rect(gap + i * (panel_w + gap), panel_y, panel_w, panel_h)
        for i in range(3)
    ]

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
                if nav_mode_rect.collidepoint(pos):
                    viz_mode = (viz_mode + 1) % 3

        with readings_lock:
            volts = list(readings)

        screen.fill(BG_COLOR)
        canvas.fill(BG_COLOR)

        # Header
        pygame.draw.line(canvas, BORDER_COLOR,
                         (0, HEADER_H - 1), (CONTENT_W, HEADER_H - 1))
        title = font_md.render(
            "// B A T T E R Y   M O N I T O R   //   A D S 1 1 1 5 //",
            True, TEXT_COLOR)
        canvas.blit(title, (CONTENT_W // 2 - title.get_width() // 2, 12))

        # PSU status strip
        with psu_lock:
            alarm     = psu_status['alarm']
            throttled = psu_status['throttled']

        uv_now  = throttled > 0 and bool(throttled & 0x1)
        uv_hist = throttled > 0 and bool(throttled & 0x10000)
        thr_now = throttled > 0 and bool(throttled & 0x4)
        thr_hist= throttled > 0 and bool(throttled & 0x40000)

        if alarm == 1 or uv_now:
            psu_col, psu_txt = (255, 40, 40),  "PSU: UNDERVOLT!"
        elif uv_hist or thr_hist:
            psu_col, psu_txt = (255, 160, 0),  "PSU: WARN (hist)"
        elif alarm == -1:
            psu_col, psu_txt = (90, 50, 120),  "PSU: N/A"
        else:
            psu_col, psu_txt = (0, 200, 100),  "PSU: OK"

        thr_col = (255, 40, 40) if thr_now else ((255, 160, 0) if thr_hist else (0, 200, 100))
        thr_txt = "THR: NOW" if thr_now else ("THR: HIST" if thr_hist else "THR: OK")


        # Panels
        for i, (rect, label) in enumerate(zip(panels, BATTERIES)):
            if viz_mode == 0:
                draw_battery_panel(canvas, fonts, rect, label, volts[i])
            elif viz_mode == 1:
                draw_seg_panel(canvas, fonts, rect, label, volts[i])
            else:
                draw_vu_panel(canvas, fonts, rect, label, volts[i])

        # Nav bar
        pygame.draw.rect(canvas, (10, 0, 20), (0, NAV_Y, CONTENT_W, 34))
        pygame.draw.line(canvas, BORDER_COLOR, (0, NAV_Y), (CONTENT_W, NAV_Y))
        pygame.draw.rect(canvas, (30, 0, 45), nav_home_rect, border_radius=5)
        pygame.draw.rect(canvas, BORDER_COLOR, nav_home_rect, 1, border_radius=5)
        home_lbl = font_sm.render("HOME", True, (255, 20, 180))
        canvas.blit(home_lbl,
                    (nav_home_rect.x + (nav_home_rect.w - home_lbl.get_width()) // 2,
                     nav_home_rect.y + (nav_home_rect.h - home_lbl.get_height()) // 2))
        mode_names = ["BATT", "BARS", "VU"]
        pygame.draw.rect(canvas, (30, 0, 45), nav_mode_rect, border_radius=5)
        pygame.draw.rect(canvas, BORDER_COLOR, nav_mode_rect, 1, border_radius=5)
        mode_lbl = font_sm.render(mode_names[viz_mode], True, (255, 20, 180))
        canvas.blit(mode_lbl,
                    (nav_mode_rect.x + (nav_mode_rect.w - mode_lbl.get_width()) // 2,
                     nav_mode_rect.y + (nav_mode_rect.h - mode_lbl.get_height()) // 2))

        switcher.update()
        switcher.draw(canvas, font_sm)

        # PSU/throttle — drawn last so switcher doesn't cover it
        psu_s = font_sm.render(psu_txt, True, psu_col)
        thr_s = font_sm.render(thr_txt, True, thr_col)
        canvas.blit(thr_s, (CONTENT_W - thr_s.get_width() - 8,
                             NAV_Y + (34 - thr_s.get_height()) // 2))
        canvas.blit(psu_s, (CONTENT_W - thr_s.get_width() - psu_s.get_width() - 16,
                             NAV_Y + (34 - psu_s.get_height()) // 2))

        screen.blit(canvas, (CONTENT_X, CONTENT_Y))
        pygame.display.flip()
        clock.tick(FPS)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        with open("/home/pi/battery_crash.log", "a") as f:
            traceback.print_exc(file=f)
        raise
