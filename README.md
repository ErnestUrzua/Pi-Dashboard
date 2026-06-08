# Pi Dashboard

A touch-friendly instrument dashboard for Raspberry Pi with a 1024×600 display. Built with Pygame on Wayland, it provides a launcher with swipe-based app switching and four real-time monitoring apps.

---

## Apps

### G-Force Monitor
Real-time XY accelerometer plot using the **MPU-6050** (I²C `0x68`). Displays a phosphor-style scatter trail that shows movement intensity and direction. Includes runtime sensitivity adjustment and a calibration system that saves offsets to `~/.config/accel_cal.json`.

### Battery Monitor
Three-channel voltage readout via **ADS1115** ADC (I²C `0x48`) with resistor divider scaling. Cycles through three visualization modes:
- **BATT** — animated battery graphic with fill level and status label
- **BARS** — segmented LED-style bar graph with per-segment voltage labels
- **VU** — analog needle meter with colored arc bands

Also monitors Raspberry Pi PSU undervolt and CPU throttle status and displays a live warning strip.

### FFT Analyzer
Audio spectrum analyzer using the **SPH0645 I2S microphone**. Renders 120 logarithmically-spaced frequency bars (20 Hz – 20 kHz) with a scrolling waveform. Supports five color themes (80s, Acid, Fire, Ice, Toxic) and includes microphone calibration to flatten the frequency response.

### GPS Tracker
Live map display using the **NEO-9M USB GPS** (`/dev/ttyACM0`). Fetches and caches OpenStreetMap tiles (Carto Dark style), plots a 500-point track history, and shows speed, altitude, satellite count, and a compass rose. Pinch-zoom supported via touch.

---

## Navigation

All apps share a **Card Switcher** — swipe up from anywhere to reveal the app carousel, swipe down or tap a card to switch apps. Swipe navigation is handled touch-natively with no buttons required.

---

## Hardware

| Component | Interface | Address / Port |
|---|---|---|
| MPU-6050 accelerometer | I²C | `0x68` |
| ADS1115 ADC (battery) | I²C | `0x48` |
| NEO-9M GPS | USB Serial | `/dev/ttyACM0` |
| SPH0645 microphone | I²S | — |
| Display | HDMI | 1024 × 600 |

---

## Dependencies

```bash
pip install pygame smbus2 numpy sounddevice scipy pyserial pynmea2
```

The display server is **Wayland** (`labwc`). Bezel margins are configured to match the `rc.xml` window decoration offsets.

---

## Running

```bash
# Launch the main dashboard
python3 launcher.py

# Or launch any app directly
python3 battery_display.py
python3 accel_display.py
python3 fft_analyzer.py
python3 gps_display.py
```

The FFT analyzer can also run as a systemd service:

```bash
sudo cp fft-analyzer.service /etc/systemd/system/
sudo systemctl enable --now fft-analyzer
```

---

## Structure

```
projects/
├── launcher.py           # Main dashboard grid
├── card_switcher.py      # Swipe-up app switcher overlay (shared)
├── accel_display.py      # G-Force monitor (MPU-6050)
├── battery_display.py    # Battery voltage monitor (ADS1115)
├── fft_analyzer.py       # Audio spectrum analyzer (SPH0645)
├── gps_display.py        # Live GPS map (NEO-9M)
├── brightness.py         # Brightness control
├── brightness_overlay.py # Brightness overlay UI
├── brightness_toggle.sh  # Shell toggle script
├── make_splash.py        # Splash screen generator
└── fft-analyzer.service  # systemd unit for FFT app
```
