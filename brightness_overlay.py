#!/usr/bin/env python3
"""Brightness control overlay — slides in from top, auto-hides after 3s."""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, Gdk, GtkLayerShell, GLib
import os, sys, signal

# ── PWM brightness ────────────────────────────────────────────────────────────
PWM_CHIP    = "/sys/class/pwm/pwmchip0"
PWM_CHANNEL = 0
PWM_PATH    = f"{PWM_CHIP}/pwm{PWM_CHANNEL}"
PERIOD_NS   = 1_000_000

def _write(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
    except Exception as e:
        print(f"PWM: {path}: {e}", file=sys.stderr)

def pwm_setup():
    if not os.path.exists(PWM_PATH):
        _write(f"{PWM_CHIP}/export", PWM_CHANNEL)
    _write(f"{PWM_PATH}/period", PERIOD_NS)
    _write(f"{PWM_PATH}/enable", 1)
    _write(f"{PWM_PATH}/duty_cycle", PERIOD_NS)

def pwm_set(percent):
    duty = int(PERIOD_NS * max(5, min(100, percent)) / 100)
    _write(f"{PWM_PATH}/duty_cycle", duty)

def pwm_get():
    try:
        with open(f"{PWM_PATH}/duty_cycle") as f:
            return round(int(f.read().strip()) / PERIOD_NS * 100)
    except Exception:
        return 100

# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = b"""
window {
    background-color: rgba(255, 255, 255, 0.15);
    border-radius: 18px;
    border: 1px solid rgba(255, 255, 255, 0.35);
}
scale trough {
    background-color: rgba(255, 255, 255, 0.2);
    border-radius: 6px;
    min-width: 8px;
}
scale highlight {
    background-color: rgba(255, 255, 255, 0.75);
    border-radius: 6px;
}
scale slider {
    background-color: rgba(255, 255, 255, 0.9);
    border-radius: 50%;
    min-width: 24px;
    min-height: 24px;
    border: 2px solid rgba(255, 255, 255, 0.6);
    box-shadow: none;
    outline: none;
}
label {
    color: rgba(255, 255, 255, 0.85);
    font-family: monospace;
    font-size: 13px;
}
"""

HIDE_DELAY_MS = 5000

class BrightnessOverlay:
    def __init__(self):
        self._hide_id = None
        self._shown   = False

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_resizable(False)

        GtkLayerShell.init_for_window(self.win)
        GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_exclusive_zone(self.win, -1)
        GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.NONE)

        self.win.set_default_size(70, 380)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        self.lbl = Gtk.Label(label=f"{pwm_get()}%")
        box.pack_start(self.lbl, False, False, 0)

        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 5, 100, 1)
        self.slider.set_draw_value(False)
        self.slider.set_inverted(True)
        self.slider.set_vexpand(True)
        self.slider.set_value(pwm_get())
        self.slider.connect("value-changed", self._on_change)
        box.pack_start(self.slider, True, True, 0)


        self.win.add(box)

    def _on_change(self, scale):
        val = int(scale.get_value())
        self.lbl.set_text(f"{val:3d}%")
        pwm_set(val)
        self._reset_timer()

    def _reset_timer(self):
        if self._hide_id:
            GLib.source_remove(self._hide_id)
        self._hide_id = GLib.timeout_add(HIDE_DELAY_MS, self.hide)

    def show(self):
        self._shown = True
        self.slider.set_value(pwm_get())
        self.win.show_all()
        self._reset_timer()

    def hide(self, *_):
        self._shown = False
        self.win.hide()
        self._hide_id = None
        return False

    def toggle(self):
        if self._shown:
            self.hide()
        else:
            self.show()


def main():
    pwm_setup()
    overlay = BrightnessOverlay()

    signal.signal(signal.SIGUSR1, lambda *_: GLib.idle_add(overlay.toggle))

    with open("/tmp/brightness_overlay.pid", "w") as f:
        f.write(str(os.getpid()))

    Gtk.main()

if __name__ == "__main__":
    main()
