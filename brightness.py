"""PWM backlight brightness control — GPIO12 via /sys/class/pwm."""

import os

PWM_CHIP    = "/sys/class/pwm/pwmchip0"
PWM_CHANNEL = 0          # GPIO12 = channel 0
PWM_PATH    = f"{PWM_CHIP}/pwm{PWM_CHANNEL}"
PERIOD_NS   = 1_000_000  # 1kHz PWM frequency


def _write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def setup():
    if not os.path.exists(PWM_PATH):
        _write(f"{PWM_CHIP}/export", PWM_CHANNEL)
    _write(f"{PWM_PATH}/period", PERIOD_NS)
    _write(f"{PWM_PATH}/duty_cycle", PERIOD_NS)  # start full brightness
    _write(f"{PWM_PATH}/enable", 1)


def set_brightness(percent):
    """Set brightness 0–100."""
    percent = max(0, min(100, percent))
    duty = int(PERIOD_NS * percent / 100)
    _write(f"{PWM_PATH}/duty_cycle", duty)


def get_brightness():
    """Return current brightness 0–100."""
    with open(f"{PWM_PATH}/duty_cycle") as f:
        duty = int(f.read().strip())
    return round(duty / PERIOD_NS * 100)


def teardown():
    _write(f"{PWM_PATH}/enable", 0)
    _write(f"{PWM_CHIP}/unexport", PWM_CHANNEL)
