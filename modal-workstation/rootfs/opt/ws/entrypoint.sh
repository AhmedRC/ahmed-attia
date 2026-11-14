#!/usr/bin/env bash
# Desktop entrypoint (runs as PID 1 of the Modal sandbox).
# Starts TigerVNC (Xvnc) + XFCE4 + noVNC, then hands over to the idle watchdog.
# When the watchdog exits, this script exits and Modal stops billing.
set -u

GEOMETRY="${GEOMETRY:-1920x1080}"
VNC_PASSWORD="${VNC_PASSWORD:?VNC_PASSWORD missing}"
export DISPLAY=:1
export HOME=/root
export XDG_RUNTIME_DIR=/run/ws/xdg
export XDG_CACHE_HOME=/tmp/cache
export LIBGL_ALWAYS_SOFTWARE=1          # llvmpipe: CPU OpenGL for FreeCAD/ParaView/Gmsh
export GALLIUM_DRIVER=llvmpipe
export QT_X11_NO_MITSHM=1               # avoid Qt shared-memory errors over VNC
export QTWEBENGINE_DISABLE_SANDBOX=1    # FreeCAD Start/AddonManager runs as root in container
export MOZ_DISABLE_CONTENT_SANDBOX=1    # Firefox sandbox is not available under gVisor
export NO_AT_BRIDGE=1

mkdir -p /run/ws /run/dbus "$XDG_RUNTIME_DIR" "$XDG_CACHE_HOME" /root/.vnc /work
chmod 700 "$XDG_RUNTIME_DIR"
echo -n "$VNC_PASSWORD" > /run/ws/vncpass
chmod 600 /run/ws/vncpass
printf '{"vnc_clients":0,"busy_cores":0.0,"idle_minutes":0.0,"auto_stop_after_min":%s}\n' "${IDLE_MINUTES:-20}" > /run/ws/status.json

# System D-Bus (prevents warnings in XFCE/Thunar/Qt apps)
rm -f /run/dbus/pid
dbus-daemon --system --fork 2>/dev/null || true

# ---------- persistent user settings on the volume ----------
# ~/.config and ~/.local/share live on /work so XFCE/FreeCAD settings survive restarts.
PERSIST=/work/.ws-home
mkdir -p "$PERSIST/config" "$PERSIST/local-share" /root/.local
for pair in ".config:config" ".local/share:local-share"; do
  dst="/root/${pair%%:*}"; src="$PERSIST/${pair##*:}"
  [ -L "$dst" ] || { rm -rf "$dst"; ln -s "$src" "$dst"; }
done
# First run: seed XFCE defaults (panel layout, no compositor, no screensaver)
if [ ! -d "$PERSIST/config/xfce4" ]; then
  cp -r /opt/ws/skel/xfce4 "$PERSIST/config/xfce4"
fi
mkdir -p /work/projects /work/inbox /root/Desktop
ln -sfn /work /root/Desktop/work
ln -sfn /work/projects /root/Desktop/projects

# ---------- VNC server ----------
echo -n "$VNC_PASSWORD" | vncpasswd -f > /root/.vnc/passwd
chmod 600 /root/.vnc/passwd
Xvnc :1 -geometry "$GEOMETRY" -depth 24 -rfbport 5901 -localhost \
     -SecurityTypes VncAuth -PasswordFile /root/.vnc/passwd \
     -AlwaysShared -AcceptSetDesktopSize -dpi 96 >/run/ws/xvnc.log 2>&1 &

for _ in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.2; done
if ! xdpyinfo >/dev/null 2>&1; then
  echo "[ws] ERROR: Xvnc failed to start:" >&2
  cat /run/ws/xvnc.log >&2 || true
  exit 1
fi
xset s off -dpms 2>/dev/null || xset s off 2>/dev/null || true

# ---------- XFCE session (restarts automatically if you log out) ----------
(
  while true; do
    setxkbmap -layout us,ara -option grp:alt_shift_toggle 2>/dev/null || true
    dbus-launch --exit-with-session startxfce4 >/run/ws/xfce.log 2>&1
    sleep 2
  done
) &

# ---------- noVNC (browser client) on :6080 ----------
websockify --web /usr/share/novnc 6080 127.0.0.1:5901 >/run/ws/novnc.log 2>&1 &

# ---------- idle watchdog (foreground) ----------
python3 /opt/ws/watchdog.py
code=$?

echo "[ws] shutting down desktop (watchdog exit $code)"
sync /work 2>/dev/null || true
exit 0
