import os
import math
import pygame
import sys

os.environ["SDL_TOUCH_MOUSE_EVENTS"] = "0"
os.environ.setdefault("XDG_RUNTIME_DIR",  "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY",  "wayland-0")
os.environ.setdefault("SDL_VIDEODRIVER",  "wayland")

DISPLAY_W = 1024
DISPLAY_H = 600
FPS       = 30

BG_COLOR     = (6, 0, 18)
BORDER_COLOR = (140, 20, 190)
TEXT_COLOR   = (215, 160, 255)
DIM_COLOR    = (90, 50, 120)

APPS = [
    {
        'label':  'G-FORCE',
        'sub':    'MONITOR 2000',
        'detail': 'MPU-6050  ·  XY Plot',
        'script': '/home/pi/projects/accel_display.py',
        'icon':   '/home/pi/projects/accel_icon.png',
        'color':  (255, 20, 180),
    },
    {
        'label':  'BATTERY',
        'sub':    'MONITOR 2000',
        'detail': 'ADS1115  ·  3 Channels',
        'script': '/home/pi/projects/battery_display.py',
        'icon':   '/home/pi/projects/battery_icon.png',
        'color':  (0, 230, 120),
    },
    {
        'label':  'FFT',
        'sub':    'ANALYZZZER',
        'detail': 'Audio Spectrum  ·  Waveform',
        'script': '/home/pi/projects/fft_analyzer.py',
        'icon':   '/home/pi/projects/fft_icon.png',
        'color':  (0, 230, 200),
    },
    {
        'label':  'MR. GPS',
        'sub':    '',
        'detail': 'NEO-9M  ·  Live Position',
        'script': '/home/pi/projects/gps_display.py',
        'icon':   '/home/pi/projects/gps_icon.png',
        'color':  (140, 20, 190),
    },
]


def main():
    pygame.init()
    pygame.key.stop_text_input()
    screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H))
    pygame.display.set_caption("Dashboard")
    clock = pygame.time.Clock()

    try:
        font_lg = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 30)
        font_md = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 16)
        font_sm = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 12)
    except Exception:
        font_lg = pygame.font.SysFont("monospace", 30)
        font_md = pygame.font.SysFont("monospace", 16)
        font_sm = pygame.font.SysFont("monospace", 12)

    # load and scale icons
    icon_size = 72
    for app in APPS:
        try:
            img = pygame.image.load(app['icon']).convert_alpha()
            app['img'] = pygame.transform.smoothscale(img, (icon_size, icon_size))
        except Exception:
            app['img'] = None

    HEADER_H = 46
    gap   = 20
    btn_w = 220
    btn_h = 175
    n     = len(APPS)

    cols = max(1, (DISPLAY_W - gap) // (btn_w + gap))
    rows = math.ceil(n / cols)

    grid_h = rows * btn_h + (rows - 1) * gap
    start_y = HEADER_H + ((DISPLAY_H - HEADER_H) - grid_h) // 2

    btn_rects = []
    for i in range(n):
        row = i // cols
        col = i % cols
        row_count = min(cols, n - row * cols)
        row_w = row_count * btn_w + (row_count - 1) * gap
        rx = (DISPLAY_W - row_w) // 2 + col * (btn_w + gap)
        ry = start_y + row * (btn_h + gap)
        btn_rects.append(pygame.Rect(rx, ry, btn_w, btn_h))

    pressed = None

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit()
                sys.exit(0)
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                pos = (int(event.x * DISPLAY_W), int(event.y * DISPLAY_H)) \
                      if event.type == pygame.FINGERDOWN else event.pos
                for i, rect in enumerate(btn_rects):
                    if rect.collidepoint(pos):
                        pressed = i
            if event.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP):
                if pressed is not None:
                    pygame.quit()
                    os.execv(sys.executable, [sys.executable, APPS[pressed]['script']])

        screen.fill(BG_COLOR)

        # header
        pygame.draw.line(screen, BORDER_COLOR, (0, 36), (DISPLAY_W, 36))
        title = font_sm.render("// D A S H B O A R D //", True, TEXT_COLOR)
        screen.blit(title, (DISPLAY_W // 2 - title.get_width() // 2, 13))

        # app buttons
        for i, (app, rect) in enumerate(zip(APPS, btn_rects)):
            bg = (28, 0, 45) if pressed == i else (14, 0, 26)
            pygame.draw.rect(screen, bg,         rect, border_radius=12)
            pygame.draw.rect(screen, app['color'], rect, 1, border_radius=12)

            cx = rect.x + rect.w // 2
            if app['img']:
                screen.blit(app['img'], (cx - icon_size // 2, rect.y + 14))
            lbl = font_md.render(app['label'],          True, app['color'])
            sub = font_md.render(app.get('sub', ''),   True, app['color'])
            det = font_sm.render(app.get('detail', ''), True, DIM_COLOR)
            screen.blit(lbl, (cx - lbl.get_width() // 2, rect.y + 94))
            screen.blit(sub, (cx - sub.get_width() // 2, rect.y + 114))
            screen.blit(det, (cx - det.get_width() // 2, rect.y + 140))

        pygame.display.flip()
        clock.tick(FPS)


if __name__ == "__main__":
    main()
