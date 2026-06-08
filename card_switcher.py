import os
import sys
import pygame

APPS = [
    {
        'label':  'G-FORCE',
        'sub':    'MONITOR',
        'script': '/home/pi/projects/accel_display.py',
        'icon':   '/home/pi/projects/accel_icon.png',
        'color':  (255, 20, 180),
    },
    {
        'label':  'BATTERY',
        'sub':    'MONITOR',
        'script': '/home/pi/projects/battery_display.py',
        'icon':   '/home/pi/projects/battery_icon.png',
        'color':  (0, 230, 120),
    },
    {
        'label':  'FFT',
        'sub':    'ANALYZER',
        'script': '/home/pi/projects/fft_analyzer.py',
        'icon':   '/home/pi/projects/fft_icon.png',
        'color':  (0, 230, 200),
    },
    {
        'label':  'GPS',
        'sub':    'TRACKER',
        'script': '/home/pi/projects/gps_display.py',
        'icon':   '/home/pi/projects/gps_icon.png',
        'color':  (140, 20, 190),
    },
]

CARD_W        = 260
CARD_H        = 190
CARD_GAP      = 50
SWIPE_UP_MIN  = 80    # px upward to trigger switcher
SWIPE_UP_MAXDX = 60   # max horizontal drift for up-swipe
SNAP_SPEED    = 0.18  # card snap easing


class CardSwitcher:
    def __init__(self, current_script, display_w, display_h):
        self.W   = display_w
        self.H   = display_h
        self.idx = next((i for i, a in enumerate(APPS) if a['script'] == current_script), 0)

        self.active      = False
        self.card_x      = float(self.idx * (CARD_W + CARD_GAP))
        self.target_x    = self.card_x
        self.selected    = self.idx

        self._icons       = None   # loaded lazily
        self._touch_down  = None   # (x, y) of finger-down
        self._drag_origin = None   # card_x at drag start

        # slide-up animation
        self._anim_y   = display_h   # current y offset (display_h = hidden)
        self._anim_target = 0

    # ------------------------------------------------------------------
    def _load_icons(self):
        if self._icons is not None:
            return
        self._icons = {}
        for app in APPS:
            try:
                img = pygame.image.load(app['icon']).convert_alpha()
                self._icons[app['script']] = pygame.transform.smoothscale(img, (72, 72))
            except Exception:
                self._icons[app['script']] = None

    # ------------------------------------------------------------------
    def _pos(self, event):
        """Normalise finger and mouse events to pixel (x, y)."""
        if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            return (event.x * self.W, event.y * self.H)
        if hasattr(event, 'pos'):
            return event.pos
        return None

    def handle_event(self, event):
        """Pass every pygame event here. Returns True if the switcher consumed it."""
        DOWN   = (pygame.FINGERDOWN,   pygame.MOUSEBUTTONDOWN)
        MOTION = (pygame.FINGERMOTION, pygame.MOUSEMOTION)
        UP     = (pygame.FINGERUP,     pygame.MOUSEBUTTONUP)

        if not self.active:
            if event.type in DOWN:
                self._touch_down = self._pos(event)

            elif event.type in UP and self._touch_down:
                pos = self._pos(event)
                if pos:
                    dy = self._touch_down[1] - pos[1]
                    dx = abs(pos[0] - self._touch_down[0])
                    if dy > SWIPE_UP_MIN and dx < SWIPE_UP_MAXDX:
                        self._open()
                self._touch_down = None

            return False

        # --- switcher active ---
        if event.type in DOWN:
            self._touch_down  = self._pos(event)
            self._drag_origin = self.card_x

        elif event.type in MOTION and self._drag_origin is not None:
            pos = self._pos(event)
            if pos:
                dx = pos[0] - self._touch_down[0]
                self.card_x = self._drag_origin - dx
                lo = 0.0
                hi = (len(APPS) - 1) * (CARD_W + CARD_GAP)
                if self.card_x < lo:
                    self.card_x = lo + (self.card_x - lo) * 0.3
                if self.card_x > hi:
                    self.card_x = hi + (self.card_x - hi) * 0.3

        elif event.type in UP and self._touch_down:
            pos = self._pos(event)
            if pos:
                dx = pos[0] - self._touch_down[0]
                dy = pos[1] - self._touch_down[1]

                if dy > SWIPE_UP_MIN and abs(dx) < SWIPE_UP_MAXDX:
                    self._close()
                else:
                    spacing = CARD_W + CARD_GAP
                    snapped = round(self.card_x / spacing)
                    snapped = max(0, min(len(APPS) - 1, snapped))
                    self.selected = snapped
                    self.target_x = snapped * spacing
                    if abs(dx) < 20 and abs(dy) < 20:
                        self._launch(self.selected)

            self._touch_down  = None
            self._drag_origin = None

        return True

    # ------------------------------------------------------------------
    def update(self):
        if not self.active and self._anim_y >= self.H:
            return
        # smooth card scroll
        self.card_x += (self.target_x - self.card_x) * SNAP_SPEED
        # slide animation
        self._anim_y += (self._anim_target - self._anim_y) * 0.2

    # ------------------------------------------------------------------
    def draw(self, surface, font):
        if not self.active and self._anim_y >= self.H - 2:
            return

        offset_y = int(self._anim_y)
        self._load_icons()

        # dim background
        dim = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        alpha = int(200 * max(0.0, 1.0 - offset_y / self.H))
        dim.fill((0, 0, 10, alpha))
        surface.blit(dim, (0, 0))

        center_x = self.W // 2
        card_y   = self.H // 2 - CARD_H // 2 + offset_y

        spacing = CARD_W + CARD_GAP

        for i, app in enumerate(APPS):
            rel_x = i * spacing - self.card_x
            cx    = center_x + int(rel_x) - CARD_W // 2

            if cx < -CARD_W - 20 or cx > self.W + 20:
                continue

            # scale + fade cards away from center
            dist  = abs(rel_x) / spacing
            scale = max(0.78, 1.0 - dist * 0.18)
            cw    = int(CARD_W * scale)
            ch    = int(CARD_H * scale)
            cx    = center_x + int(rel_x) - cw // 2
            cy    = self.H // 2 - ch // 2 + offset_y

            # card surface
            card = pygame.Surface((cw, ch), pygame.SRCALPHA)
            a    = int(220 * scale)
            r, g, b = app['color']
            pygame.draw.rect(card, (r // 5, g // 5, b // 5, a), (0, 0, cw, ch), border_radius=14)
            pygame.draw.rect(card, (*app['color'], int(200 * scale)), (0, 0, cw, ch), 1, border_radius=14)
            surface.blit(card, (cx, cy))

            # icon
            icon = self._icons.get(app['script'])
            if icon:
                isz  = int(72 * scale)
                icon = pygame.transform.smoothscale(icon, (isz, isz))
                surface.blit(icon, (cx + cw // 2 - isz // 2, cy + int(22 * scale)))

            # label
            name_s = font.render(app['label'], True, app['color'])
            sub_s  = font.render(app['sub'],   True, tuple(c // 2 for c in app['color']))
            surface.blit(name_s, (cx + cw // 2 - name_s.get_width() // 2, cy + int(104 * scale)))
            surface.blit(sub_s,  (cx + cw // 2 - sub_s.get_width()  // 2, cy + int(122 * scale)))

        # hint
        hint = font.render("↓  swipe down to close", True, (80, 50, 120))
        surface.blit(hint, (self.W // 2 - hint.get_width() // 2, self.H - 28 + offset_y))

    # ------------------------------------------------------------------
    def _open(self):
        self.active       = True
        self.selected     = self.idx
        self.card_x       = float(self.idx * (CARD_W + CARD_GAP))
        self.target_x     = self.card_x
        self._anim_y      = self.H
        self._anim_target = 0

    def _close(self):
        self._anim_target = self.H
        # delay deactivation until animation finishes — handled in update
        self.active = False

    def _launch(self, idx):
        script = APPS[idx]['script']
        pygame.quit()
        os.execv(sys.executable, [sys.executable, script])
