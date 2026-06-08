import numpy as np
import sounddevice as sd
import pygame
import sys
import os
import threading
import subprocess
from card_switcher import CardSwitcher
from scipy.signal import butter, sosfilt

def make_bandpass(low, high, fs, order=4):
    nyq = fs / 2
    return butter(order, [low / nyq, high / nyq], btype='band', output='sos')

BANDPASS_SOS = make_bandpass(20, 500, 48000)

os.environ["SDL_TOUCH_MOUSE_EVENTS"] = "0"
os.environ.setdefault("XDG_RUNTIME_DIR",  "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY",  "wayland-0")
os.environ.setdefault("SDL_VIDEODRIVER",  "wayland")

# --- Config ---
SAMPLE_RATE = 48000
CHUNK = 1024          # audio callback block size
FFT_SIZE   = 4096         # FFT window size
RING_SIZE  = 288000       # 6 seconds of audio history for waveform
DISPLAY_W = 1024
DISPLAY_H = 600
FPS = 60
BAR_COUNT = 120       # number of frequency bars
MIN_FREQ = 20         # Hz
MAX_FREQ = 20000      # Hz
DB_MIN = -40          # dB floor
DB_MAX = 0            # dB ceiling
BASS_SHELF_DB  = 18   # max dB to cut at lowest frequencies
BASS_SHELF_HZ  = 500  # frequency where bass cut tapers to zero
MIC_CAL_FILE   = '/home/pi/.config/fft_mic_cal.json'
MIC_CAL_SECS   = 5    # seconds to average during calibration

THEMES = [
    { "name": "80s",
      "BG":   (8,   4,  20), "LOW": (40,  20, 200), "MID": (180,  0, 220),
      "HIGH": (255,  0, 255), "GRID": (50, 20,  80), "TEXT": (0,  220, 255) },
    { "name": "Acid",
      "BG":   (5,  10,   0), "LOW": (0,  200,  50), "MID": (150, 220,   0),
      "HIGH": (255,200,   0), "GRID": (30, 50,  10), "TEXT": (0,  255, 100) },
    { "name": "Fire",
      "BG":   (10,  0,   0), "LOW": (180, 30,   0), "MID": (255, 140,   0),
      "HIGH": (255,255,  50), "GRID": (60, 20,  10), "TEXT": (255, 100,  50) },
    { "name": "Ice",
      "BG":   (0,   5,  15), "LOW": (0,  100, 200), "MID": (0,  200, 255),
      "HIGH": (200,240, 255), "GRID": (10, 30,  60), "TEXT": (100, 220, 255) },
    { "name": "Toxic",
      "BG":   (5,   0,  10), "LOW": (100,  0, 150), "MID": (180, 50, 255),
      "HIGH": (50, 255, 150), "GRID": (40, 10,  50), "TEXT": (180, 255,  80) },
]
_theme_idx = 0

def apply_theme(t):
    global BG_COLOR, BAR_COLOR_LOW, BAR_COLOR_MID, BAR_COLOR_HIGH, GRID_COLOR, TEXT_COLOR
    BG_COLOR       = t["BG"]
    BAR_COLOR_LOW  = t["LOW"]
    BAR_COLOR_MID  = t["MID"]
    BAR_COLOR_HIGH = t["HIGH"]
    GRID_COLOR     = t["GRID"]
    TEXT_COLOR     = t["TEXT"]

apply_theme(THEMES[0])

# --- Floating particles ---
import random, math

NUM_PARTICLES = 25

class Particle:
    SHAPES = ['diamond', 'triangle', 'square']
    def __init__(self):
        self.reset(random.randint(0, DISPLAY_W), random.randint(0, DISPLAY_H))

    def burst(self, x, y):
        self.x  = x + random.uniform(-20, 20)
        self.y  = y + random.uniform(-20, 20)
        angle   = random.uniform(0, math.pi * 2)
        speed   = random.uniform(1.5, 4.0)
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed

    def reset(self, x=None, y=None):
        self.x  = x if x is not None else random.randint(0, DISPLAY_W)
        self.y  = y if y is not None else random.randint(0, DISPLAY_H)
        self.vx = random.uniform(-0.4, 0.4)
        self.vy = random.uniform(-0.4, 0.4)
        self.size   = random.randint(8, 22)
        self.rot    = random.uniform(0, math.pi * 2)
        self.rot_v  = random.uniform(-0.01, 0.01)
        self.shape  = random.choice(self.SHAPES)
        self.alpha  = random.randint(180, 240)

    def update(self, energy):
        speed = 1.0 + energy * 3.0
        self.x   += self.vx * speed
        self.y   += self.vy * speed
        self.rot += self.rot_v * speed
        if self.x < -50 or self.x > DISPLAY_W + 50 or self.y < -50 or self.y > DISPLAY_H + 50:
            self.reset()

    def points(self, energy=0.0):
        cx, cy, r, a = self.x, self.y, self.size * (1.0 + energy * 5.0), self.rot
        if self.shape == 'diamond':
            return [(cx + r*math.cos(a + math.pi/2*i), cy + r*math.sin(a + math.pi/2*i)) for i in range(4)]
        elif self.shape == 'triangle':
            return [(cx + r*math.cos(a + 2*math.pi/3*i), cy + r*math.sin(a + 2*math.pi/3*i)) for i in range(3)]
        else:  # square
            return [(cx + r*math.cos(a + math.pi/2*i + math.pi/4), cy + r*math.sin(a + math.pi/2*i + math.pi/4)) for i in range(4)]

particles = [Particle() for _ in range(NUM_PARTICLES)]

def draw_particles(surface, energy, shape_mode):
    if shape_mode == 'off':
        return
    colors = [BAR_COLOR_LOW, BAR_COLOR_MID, BAR_COLOR_HIGH]
    overlay = pygame.Surface((DISPLAY_W, DISPLAY_H), pygame.SRCALPHA)
    for i, p in enumerate(particles):
        if shape_mode != 'mixed':
            p.shape = shape_mode
        p.update(energy)
        col = colors[i % len(colors)]
        alpha = min(255, int(p.alpha + energy * 170))
        pygame.draw.polygon(overlay, (*col, alpha), [(int(x), int(y)) for x, y in p.points(energy)], 2)
    surface.blit(overlay, (0, 0))

# --- Globals ---
ring_buffer = np.zeros(RING_SIZE, dtype=np.float32)
ring_lock = threading.Lock()

def audio_callback(indata, frames, time, status):
    global ring_buffer
    if status:
        print(status, file=sys.stderr)
    chunk = indata[:, 0].copy()
    with ring_lock:
        ring_buffer = np.roll(ring_buffer, -len(chunk))
        ring_buffer[-len(chunk):] = chunk

GAIN = 150.0
agc_peak = 0.001

# Per-bar mic calibration correction (dB offset per bar, 0 = flat)
import json as _json
def _load_mic_cal():
    try:
        with open(MIC_CAL_FILE) as f:
            return np.array(_json.load(f), dtype=np.float32)
    except Exception:
        return np.zeros(BAR_COUNT, dtype=np.float32)

mic_cal    = _load_mic_cal()
cal_state  = {'running': False, 'accum': None, 'frames': 0, 'done_until': 0}

def compute_fft(buffer, sample_rate):
    global agc_peak
    windowed = buffer * np.hanning(FFT_SIZE)
    fft_data = np.abs(np.fft.rfft(windowed, n=FFT_SIZE)) * GAIN
    peak = np.max(fft_data) if np.max(fft_data) > 0 else 1.0
    agc_peak = peak if peak > agc_peak else agc_peak * 0.97
    fft_data = (fft_data / agc_peak) * 1.2
    freqs = np.fft.rfftfreq(len(buffer), d=1.0 / sample_rate)
    fft_db = 20 * np.log10(np.maximum(fft_data, 1e-10))
    return freqs, fft_db

def get_bar_values(freqs, fft_db, bar_count, min_freq, max_freq):
    """Map FFT bins to log-spaced bars with bass shelf reduction."""
    log_freqs = np.logspace(np.log10(min_freq), np.log10(max_freq), bar_count + 1)
    bars = []
    for i in range(bar_count):
        mask = (freqs >= log_freqs[i]) & (freqs < log_freqs[i + 1])
        if np.any(mask):
            bars.append(np.max(fft_db[mask]))
        else:
            bars.append(DB_MIN)
    bars = np.array(bars)

    # Bass shelf: taper cut from BASS_SHELF_DB at min_freq down to 0 at BASS_SHELF_HZ
    centers = np.sqrt(log_freqs[:-1] * log_freqs[1:])
    log_min   = np.log10(min_freq)
    log_shelf = np.log10(BASS_SHELF_HZ)
    t = np.clip((np.log10(centers) - log_min) / (log_shelf - log_min), 0.0, 1.0)
    bars -= BASS_SHELF_DB * (1.0 - t)
    bars += mic_cal   # apply per-bar mic correction

    return bars

def db_to_height(db, display_h, db_min, db_max):
    normalized = (db - db_min) / (db_max - db_min)
    return int(np.clip(normalized, 0, 1) * (display_h - 80))

def bar_color(normalized_height):
    """Color shifts low→mid→high based on bar height."""
    if normalized_height < 0.5:
        t = normalized_height / 0.5
        return tuple(int(BAR_COLOR_LOW[i] + t * (BAR_COLOR_MID[i] - BAR_COLOR_LOW[i])) for i in range(3))
    else:
        t = (normalized_height - 0.5) / 0.5
        return tuple(int(BAR_COLOR_MID[i] + t * (BAR_COLOR_HIGH[i] - BAR_COLOR_MID[i])) for i in range(3))

BAR_BASE_OFFSET = 90  # px from bottom where bars end
DISPLAY_Y_OFFSET = 30  # shift entire display down

def draw_grid(surface, font, display_w, display_h):
    pass

def draw_freq_labels(surface, font, display_w, display_h):
    labels = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
    bar_w = display_w // BAR_COUNT
    bar_base = display_h - BAR_BASE_OFFSET + DISPLAY_Y_OFFSET
    dim = tuple(c // 3 for c in TEXT_COLOR)
    pygame.draw.line(surface, GRID_COLOR, (0, bar_base), (display_w, bar_base))
    for freq in labels:
        bar_idx = int((np.log10(freq) - np.log10(MIN_FREQ)) /
                      (np.log10(MAX_FREQ) - np.log10(MIN_FREQ)) * BAR_COUNT)
        x = bar_idx * bar_w
        pygame.draw.line(surface, dim, (x, bar_base), (x, bar_base + 5))
        label = font.render(f"{freq if freq < 1000 else f'{freq//1000}k'}", True, dim)
        lx = max(0, min(x - label.get_width() // 2, display_w - label.get_width()))
        surface.blit(label, (lx, bar_base + 7))

BTN_H        = 27
BTN_COLOR    = (30, 10, 60)
BTN_ACTIVE   = (120, 0, 180)
BTN_BORDER   = (120, 0, 180)

def draw_buttons(surface, font, buttons, active_rect=None):
    for label, rect in buttons:
        color = BTN_ACTIVE if rect == active_rect else BTN_COLOR
        pygame.draw.rect(surface, color, rect, border_radius=6)
        pygame.draw.rect(surface, BTN_BORDER, rect, 1, border_radius=6)
        txt = font.render(label, True, TEXT_COLOR)
        surface.blit(txt, (rect.x + (rect.w - txt.get_width()) // 2,
                           rect.y + (rect.h - txt.get_height()) // 2))

def hit(rect, pos):
    return rect.collidepoint(pos)

POPUP_DURATION = 1000  # ms

def draw_popup(surface, font, text, rect, start_ms):
    elapsed = pygame.time.get_ticks() - start_ms
    if elapsed >= POPUP_DURATION:
        return
    alpha = int(255 * (1.0 - elapsed / POPUP_DURATION))
    txt = font.render(text, True, (255, 255, 255))
    pop = pygame.Surface((txt.get_width() + 12, txt.get_height() + 8), pygame.SRCALPHA)
    pop.fill((80, 0, 160, alpha))
    txt.set_alpha(alpha)
    pop.blit(txt, (6, 4))
    x = rect.x - pop.get_width() - 6
    y = rect.y + (rect.h - pop.get_height()) // 2
    surface.blit(pop, (max(0, x), max(0, y)))

def start_mic_cal():
    """Begin accumulating FFT frames for mic calibration."""
    cal_state['running'] = True
    cal_state['accum']   = np.zeros(BAR_COUNT, dtype=np.float64)
    cal_state['frames']  = 0


def finish_mic_cal():
    """Compute and save the flattening correction from accumulated frames."""
    global mic_cal
    if cal_state['frames'] < 1:
        return
    avg = cal_state['accum'] / cal_state['frames']
    # Correction = -(avg - max(avg)) so quiet bars are boosted up to the peak, nothing gets cut
    # Cap at 20 dB to avoid exploding bins where mic had no signal during calibration
    correction = np.clip(-(avg - np.max(avg)), 0.0, 6.0)
    mic_cal = correction.astype(np.float32)
    try:
        with open(MIC_CAL_FILE, 'w') as f:
            _json.dump(mic_cal.tolist(), f)
    except Exception as e:
        print(f"Mic cal save failed: {e}", file=sys.stderr)
    cal_state['running'] = False
    cal_state['done_until'] = pygame.time.get_ticks() + 3000


def main():
    global GAIN

    pygame.init()
    pygame.mouse.set_visible(False)
    pygame.key.stop_text_input()
    screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), pygame.FULLSCREEN)
    pygame.display.set_caption("FFT Analyzer")
    clock = pygame.time.Clock()
    font = pygame.font.Font("/home/pi/.fonts/Menlo.ttc", 12)

    # Smoothing buffers
    smoothed_bars = np.full(BAR_COUNT, float(DB_MIN))
    smoothed_wave = np.zeros(BAR_COUNT)
    wave_agc_peak = 0.001
    wave_display  = np.zeros(DISPLAY_W)   # smoothed y values for the trace
    switcher = CardSwitcher(__file__, DISPLAY_W, DISPLAY_H)
    fft_smoothing  = 0.50
    wave_smoothing = 0.60
    smoothing = fft_smoothing

    # Touch buttons: (label, Rect) — bottom right, stacked upward
    bw = 68
    pad = 6
    x0 = DISPLAY_W - bw - pad
    labels = ["HOME", "SLOW -", "SLOW +", "FFT/WAVE", "THEME", "SHAPES", "MIC CAL", "MIRROR"]
    buttons = [
        (lbl, pygame.Rect(x0, DISPLAY_H - BAR_BASE_OFFSET - (i + 1) * (BTN_H + pad) - 20, bw, BTN_H))
        for i, lbl in enumerate(labels)
    ]
    b_home, b_smooth_dn, b_smooth_up, b_mode, b_theme, b_shapes, b_mic_cal, b_mirror = [r for _, r in buttons]
    mirrored = [True]

    # Find I2S device index (or use default)
    device_index = None
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            print(f"  [{i}] {d['name']}")
            if 'dmic' in d['name'].lower():
                device_index = i
    print(f"Using device index: {device_index if device_index is not None else 'default'}")

    try:
        stream = sd.InputStream(
            device=device_index,
            channels=2,
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK,
            dtype='float32',
            latency='high',
            callback=audio_callback
        )
    except Exception as e:
        stream = None
        print(f"Audio device unavailable: {e}", file=sys.stderr)

    no_audio_msg = stream is None

    mode = 'fft'
    wave_window = SAMPLE_RATE * 3   # default 3 seconds
    wave_peak_hold = np.zeros(DISPLAY_W)
    theme_idx = 0
    shape_modes = ['mixed', 'diamond', 'triangle', 'square', 'off']
    shape_idx = 4  # default off
    tap_pending = False
    active_btn = None
    active_btn_until = 0
    popup = None  # (text, rect, start_ms)

    ctx = stream if stream is not None else __import__('contextlib').nullcontext()
    with ctx:
        while True:
            for event in pygame.event.get():
                switcher.handle_event(event)
                if event.type == pygame.QUIT:
                    pygame.quit()
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit(0)
                    if event.key == pygame.K_SPACE:
                        mode = 'wave' if mode == 'fft' else 'fft'
                if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    if event.type == pygame.FINGERDOWN:
                        pos = (int(event.x * DISPLAY_W), int(event.y * DISPLAY_H))
                    else:
                        pos = event.pos
                    for _, r in buttons:
                        if hit(r, pos):
                            active_btn = r
                            active_btn_until = pygame.time.get_ticks() + 300
                    now = pygame.time.get_ticks()
                    if hit(b_mode, pos):
                        if mode == 'fft':
                            fft_smoothing = smoothing
                            mode = 'wave'
                            smoothing = wave_smoothing
                        else:
                            wave_smoothing = smoothing
                            mode = 'fft'
                            smoothing = fft_smoothing
                        popup = (mode.upper(), b_mode, now)
                    if hit(b_smooth_up, pos):
                        smoothing = min(smoothing + 0.05, 0.97)
                        popup = (f"SMOOTH {smoothing:.2f}", b_smooth_up, now)
                    if hit(b_smooth_dn, pos):
                        smoothing = max(smoothing - 0.05, 0.0)
                        popup = (f"SMOOTH {smoothing:.2f}", b_smooth_dn, now)
                    if hit(b_theme, pos):
                        theme_idx = (theme_idx + 1) % len(THEMES)
                        apply_theme(THEMES[theme_idx])
                        popup = (THEMES[theme_idx]["name"], b_theme, now)
                    if hit(b_shapes, pos):
                        shape_idx = (shape_idx + 1) % len(shape_modes)
                        popup = (shape_modes[shape_idx].upper(), b_shapes, now)
                    if hit(b_mic_cal, pos):
                        if not cal_state['running']:
                            start_mic_cal()
                            popup = ("MIC CAL...", b_mic_cal, now)
                    if hit(b_mirror, pos):
                        mirrored[0] = not mirrored[0]
                        popup = ("MIRROR" if mirrored[0] else "NORMAL", b_mirror, now)
                    if hit(b_home, pos):
                        pygame.quit()
                        os.execv(sys.executable, [sys.executable, '/home/pi/projects/launcher.py'])
                    if not any(hit(r, pos) for _, r in buttons):
                        burst_count = 0
                        for p in particles:
                            if burst_count >= 8:
                                break
                            p.burst(pos[0], pos[1])
                            burst_count += 1

            screen.fill(BG_COLOR)
            with ring_lock:
                energy_buf = ring_buffer[-CHUNK:].copy()
            energy = float(np.sqrt(np.mean(energy_buf ** 2))) * 10
            energy = min(energy, 1.0)
            draw_particles(screen, energy, shape_modes[shape_idx])
            center_y = (DISPLAY_H - BAR_BASE_OFFSET) // 2 + DISPLAY_Y_OFFSET

            if mode == 'fft':
                with ring_lock:
                    buf = ring_buffer[-FFT_SIZE:].copy()
                freqs, fft_db = compute_fft(buf, SAMPLE_RATE)
                bars = get_bar_values(freqs, fft_db, BAR_COUNT, MIN_FREQ, MAX_FREQ)
                smoothed_bars = smoothing * smoothed_bars + (1 - smoothing) * bars

                if cal_state['running']:
                    cal_state['accum']  += bars - mic_cal  # accumulate raw (pre-cal) bars
                    cal_state['frames'] += 1
                    frames_needed = int(MIC_CAL_SECS * FPS)
                    if cal_state['frames'] >= frames_needed:
                        finish_mic_cal()
                        popup = ("MIC CAL DONE", b_mic_cal, pygame.time.get_ticks())

                draw_grid(screen, font, DISPLAY_W, DISPLAY_H)
                bar_w = DISPLAY_W // BAR_COUNT
                bar_base_y = DISPLAY_H - BAR_BASE_OFFSET + DISPLAY_Y_OFFSET
                for i, db in enumerate(smoothed_bars):
                    norm = (db - DB_MIN) / (DB_MAX - DB_MIN)
                    color = bar_color(np.clip(norm, 0, 1))
                    x = i * bar_w
                    if mirrored[0]:
                        h = db_to_height(db, DISPLAY_H, DB_MIN, DB_MAX) // 2
                        if h < 20:
                            continue
                        pygame.draw.rect(screen, color, (x, center_y - h, bar_w, h))
                        pygame.draw.rect(screen, color, (x, center_y, bar_w, h))
                    else:
                        h = db_to_height(db, DISPLAY_H, DB_MIN, DB_MAX)
                        if h < 40:
                            continue
                        pygame.draw.rect(screen, color, (x, bar_base_y - h, bar_w, h))

                draw_freq_labels(screen, font, DISPLAY_W, DISPLAY_H)

            else:  # waveform
                with ring_lock:
                    samples = ring_buffer[-(wave_window + CHUNK):].copy()
                samples = sosfilt(BANDPASS_SOS, samples)

                # AGC normalize with noise floor so silence stays silent
                peak = max(float(np.max(np.abs(samples))), 1e-6)
                wave_agc_peak = max(peak if peak > wave_agc_peak else wave_agc_peak * 0.97, 0.02)
                samples = np.clip(samples / wave_agc_peak * 0.85, -1.0, 1.0)

                # zero-crossing trigger — locks the wave so it doesn't scroll
                trigger = 0
                for i in range(1, CHUNK):
                    if samples[i - 1] < 0 <= samples[i]:
                        trigger = i
                        break

                display = samples[trigger:trigger + DISPLAY_W]
                if len(display) < DISPLAY_W:
                    display = samples[-DISPLAY_W:]

                amp = (DISPLAY_H - BAR_BASE_OFFSET) // 2 - 10
                envelope = np.array([float(np.max(np.abs(display[max(0,x-2):x+3]))) for x in range(DISPLAY_W)])
                wave_display = smoothing * wave_display + (1 - smoothing) * envelope
                pygame.draw.line(screen, GRID_COLOR, (0, center_y), (DISPLAY_W, center_y))
                wave_peak_hold[:] = np.where(wave_display > wave_peak_hold,
                                             wave_display, wave_peak_hold * 0.99)
                pts      = [(x, center_y - int(wave_display[x]   * amp)) for x in range(DISPLAY_W)]
                pts_hold = [(x, center_y - int(wave_peak_hold[x] * amp)) for x in range(DISPLAY_W)]
                pygame.draw.lines(screen, BAR_COLOR_HIGH, False, pts,      2)
                pygame.draw.lines(screen, BAR_COLOR_MID,  False, pts_hold, 1)

            if pygame.time.get_ticks() > active_btn_until:
                active_btn = None
            if no_audio_msg:
                msg = font.render("NO AUDIO DEVICE", True, (255, 60, 60))
                screen.blit(msg, (DISPLAY_W // 2 - msg.get_width() // 2, 12))
            draw_buttons(screen, font, buttons, active_btn)
            if popup:
                draw_popup(screen, font, *popup)
            switcher.update()
            switcher.draw(screen, font)

            pygame.display.flip()
            clock.tick(FPS)

if __name__ == "__main__":
    main()
