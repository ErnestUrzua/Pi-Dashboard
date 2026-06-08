import os
import sys
import subprocess
import time

os.environ.setdefault("XDG_RUNTIME_DIR",  "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY",  "wayland-0")
os.environ["QT_QPA_PLATFORM"]            = "wayland"
os.environ["QT_SCALE_FACTOR"]            = "1.5"
os.environ["QT_IM_MODULE"]               = "wayland"

# start OSM Scout Server in background if not already running
import subprocess
result = subprocess.run(["pgrep", "-f", "OSMScoutServer"], capture_output=True)
if result.returncode != 0:
    subprocess.Popen(
        ["/usr/bin/flatpak", "run", "--command=osmscout-server", "io.github.rinigus.OSMScoutServer"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

os.execv("/usr/bin/flatpak", ["flatpak", "run", "io.github.rinigus.PureMaps"])
