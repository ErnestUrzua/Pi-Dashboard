import os
import sys

APPS = [
    '/home/pi/projects/accel_display.py',
    '/home/pi/projects/fft_analyzer.py',
    '/home/pi/projects/gps_display.py',
]

SWIPE_MIN_X = 120   # minimum horizontal distance to register as swipe
SWIPE_MAX_Y = 80    # maximum vertical drift allowed


class SwipeDetector:
    def __init__(self, current_script, display_w, display_h):
        self.display_w   = display_w
        self.display_h   = display_h
        self.current_idx = next((i for i, a in enumerate(APPS) if a == current_script), 0)
        self._start      = None

    def handle_event(self, event):
        import pygame
        if event.type == pygame.FINGERDOWN:
            self._start = (event.x * self.display_w, event.y * self.display_h)

        elif event.type == pygame.FINGERUP and self._start is not None:
            ex = event.x * self.display_w
            ey = event.y * self.display_h
            dx = ex - self._start[0]
            dy = abs(ey - self._start[1])
            self._start = None

            if abs(dx) >= SWIPE_MIN_X and dy <= SWIPE_MAX_Y:
                step   = -1 if dx > 0 else 1   # swipe right = go back, left = go forward
                target = APPS[(self.current_idx + step) % len(APPS)]
                import pygame as pg
                pg.quit()
                os.execv(sys.executable, [sys.executable, target])
