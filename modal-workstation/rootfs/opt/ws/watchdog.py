#!/usr/bin/env python3
"""
Idle watchdog for the Modal desktop sandbox.

The desktop counts as ACTIVE when any of these is true:
  * a browser is connected through noVNC (an ESTABLISHED TCP connection on :5901)
  * the CPU is busy (>= BUSY_CORES cores used, e.g. a solver running in the background)
  * /run/ws/keepalive was touched within the last IDLE_MINUTES

After IDLE_MINUTES of no activity the watchdog flushes the volume and exits,
which ends the sandbox (and the billing).
Status is written to /run/ws/status.json for `ws desktop url` / Hermes.
"""
import json
import os
import subprocess
import time

IDLE_MINUTES = float(os.environ.get("IDLE_MINUTES", "20"))
BUSY_CORES = float(os.environ.get("BUSY_CORES", "0.5"))
INTERVAL = 30
VNC_PORT = 5901
STATUS = "/run/ws/status.json"
KEEPALIVE = "/run/ws/keepalive"


def vnc_clients() -> int:
    n = 0
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                next(fh)
                for line in fh:
                    parts = line.split()
                    local, state = parts[1], parts[3]
                    if state == "01" and int(local.rsplit(":", 1)[1], 16) == VNC_PORT:
                        n += 1
        except (OSError, StopIteration, IndexError, ValueError):
            pass
    return n


def cpu_sample():
    with open("/proc/stat") as fh:
        vals = [int(x) for x in fh.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def busy_cores(prev, cur) -> float:
    total = cur[0] - prev[0]
    if total <= 0:
        return 0.0
    ncpu = os.cpu_count() or 1
    return (1 - (cur[1] - prev[1]) / total) * ncpu


def main() -> None:
    last_active = time.time()
    prev = cpu_sample()
    last_sync = time.time()
    while True:
        time.sleep(INTERVAL)
        cur = cpu_sample()
        cores = busy_cores(prev, cur)
        prev = cur
        clients = vnc_clients()
        keep = os.path.exists(KEEPALIVE) and time.time() - os.path.getmtime(KEEPALIVE) < IDLE_MINUTES * 60

        if clients > 0 or cores >= BUSY_CORES or keep:
            last_active = time.time()
        idle_min = (time.time() - last_active) / 60

        with open(STATUS, "w") as fh:
            json.dump({
                "vnc_clients": clients,
                "busy_cores": round(cores, 2),
                "idle_minutes": round(idle_min, 1),
                "auto_stop_after_min": IDLE_MINUTES,
            }, fh)

        if time.time() - last_sync > 300:  # extra safety: flush every 5 min
            subprocess.run(["sync", "/work"], check=False)
            last_sync = time.time()

        if idle_min >= IDLE_MINUTES:
            print(f"[watchdog] idle for {idle_min:.1f} min -> stopping", flush=True)
            subprocess.run(["sync", "/work"], check=False)
            return


if __name__ == "__main__":
    main()
