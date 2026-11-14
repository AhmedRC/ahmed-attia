#!/usr/bin/env python3
"""
ws — Modal Structural Workstation controller.

Runs on the always-on VPS next to Hermes Agent. It starts/stops an on-demand
Ubuntu + XFCE4 desktop (noVNC in the browser) and headless solver jobs on Modal,
so you only pay while something is actually running.

Every command prints a single JSON object on stdout (logs go to stderr) so that
Hermes can parse results reliably.

Examples:
    ws build                                   # build/cache the image once
    ws desktop start --profile daily           # -> {"url": "https://...", ...}
    ws desktop stop
    ws run "python frame.py" --cwd /work/projects/tower --profile heavy
    ws run "ccx -i beam" --cwd /work/projects/beam --detach
    ws status
    ws push ./model.FCStd projects/tower/      # VPS -> volume
    ws pull projects/tower/results ./results   # volume -> VPS
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import modal

# --------------------------------------------------------------------------- #
# Configuration (override with environment variables on the VPS)
# --------------------------------------------------------------------------- #
APP_NAME = os.environ.get("WS_APP_NAME", "structural-workstation")
VOLUME_NAME = os.environ.get("WS_VOLUME_NAME", "structural-work")
WORK_DIR = "/work"  # where the volume is mounted inside every sandbox
DESKTOP_NAME = "desktop"  # unique sandbox name -> only one desktop at a time
NOVNC_PORT = 6080
HERE = Path(__file__).resolve().parent

# Modal "cpu" = PHYSICAL cores (1 core = 2 vCPU). Sandboxes are billed on
# max(request, actual usage), so we request little and allow bursting up to
# the limit: an idle desktop costs cents, a solver can still use every core.
PROFILES: dict[str, dict] = {
    "light": {"cpu": (1.0, 4.0), "memory": (2048, 16384), "desc": "4 cores / 16 GiB max"},
    "daily": {"cpu": (1.0, 8.0), "memory": (4096, 32768), "desc": "8 cores / 32 GiB max"},
    "heavy": {"cpu": (2.0, 32.0), "memory": (8192, 65536), "desc": "32 cores / 64 GiB max"},
}

# Modal Sandbox (non-preemptible) list prices, USD. Used only for estimates.
PRICE_CPU_CORE_HOUR = 0.00003942 * 3600  # ~0.142 $/physical-core/hour
PRICE_GIB_HOUR = 0.00000667 * 3600  # ~0.024 $/GiB/hour

APT_PACKAGES = [
    # --- desktop + remote access ---
    "xfce4-session", "xfwm4", "xfdesktop4", "xfce4-panel", "xfce4-settings",
    "xfconf", "xfce4-terminal", "xfce4-taskmanager", "xfce4-appfinder",
    "xfce4-notifyd", "xfce4-screenshooter", "xfce4-whiskermenu-plugin",
    "xfce4-xkb-plugin", "thunar", "thunar-archive-plugin", "mousepad",
    "ristretto", "elementary-xfce-icon-theme", "adwaita-icon-theme",
    "dbus-x11", "at-spi2-core", "x11-xserver-utils", "x11-utils", "xdotool",
    "xfonts-base", "tigervnc-standalone-server", "tigervnc-tools", "novnc", "websockify",
    "libgl1-mesa-dri", "libglu1-mesa", "libegl1", "libopengl0", "mesa-utils",
    # --- Qt / XCB / X11 runtime libs needed by FreeCAD, ParaView & Gmsh ---
    "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1",
    "libxcb-randr0", "libxcb-render-util0", "libxcb-shape0", "libxcb-xinerama0",
    "libxcb-xkb1", "libxkbcommon-x11-0", "libxcursor1", "libxft2", "libxinerama1", "libgomp1",
    # --- fonts (incl. Arabic) + locale ---
    "fonts-dejavu", "fonts-noto-core", "fonts-kacst", "fonts-arabeyes", "locales",
    # --- utilities ---
    "ca-certificates", "curl", "wget", "git", "nano", "htop", "procps",
    "unzip", "p7zip-full", "file-roller", "gnupg", "less", "tzdata", "coreutils",
    # --- structural engineering stack (FreeCAD 1.1 is installed below from official release) ---
    "librecad", "gmsh", "paraview", "calculix-ccx", "calculix-cgx",
    "openmpi-bin", "python3-numpy", "python3-scipy", "python3-matplotlib",
]

PIP_PACKAGES = [
    "numpy", "scipy", "pandas", "matplotlib", "openpyxl",
    "openseespy", "PyNiteFEA", "anastruct", "sectionproperties",
    "gmsh", "meshio", "ezdxf", "ccx2paraview", "ifcopenshell",
]

FREECAD_URL = (
    "https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/"
    "FreeCAD_1.1.4-Linux-x86_64-py311.AppImage"
)


def build_image() -> modal.Image:
    return (
        modal.Image.from_registry("ubuntu:24.04", add_python="3.11")
        .env({"DEBIAN_FRONTEND": "noninteractive", "TZ": "Africa/Cairo"})
        .run_commands(
            "apt-get update",
            "apt-get install -y --no-install-recommends " + " ".join(APT_PACKAGES),
            # Firefox (.deb from Mozilla; Ubuntu's firefox is a snap and can't run here)
            "install -d -m 0755 /etc/apt/keyrings",
            "wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg"
            " -O /etc/apt/keyrings/packages.mozilla.org.asc",
            'echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc]'
            ' https://packages.mozilla.org/apt mozilla main"'
            " > /etc/apt/sources.list.d/mozilla.list",
            "printf 'Package: *\\nPin: origin packages.mozilla.org\\nPin-Priority: 1000\\n'"
            " > /etc/apt/preferences.d/mozilla",
            "apt-get update && apt-get install -y --no-install-recommends firefox",
            # FreeCAD 1.1.4 (Ubuntu 24.04 dropped freecad from apt; extract AppImage as FUSE is unavailable)
            f"wget -q '{FREECAD_URL}' -O /tmp/freecad.AppImage"
            " && chmod +x /tmp/freecad.AppImage"
            " && cd /opt && /tmp/freecad.AppImage --appimage-extract >/dev/null"
            " && mv /opt/squashfs-root /opt/freecad && rm -f /tmp/freecad.AppImage",
            "printf '#!/usr/bin/env bash\\nexport QT_X11_NO_MITSHM=1 QTWEBENGINE_DISABLE_SANDBOX=1"
            " LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe\\nexec /opt/freecad/AppRun \"$@\"\\n'"
            " > /usr/local/bin/freecad && chmod +x /usr/local/bin/freecad"
            " && ln -sf /opt/freecad/usr/bin/freecadcmd /usr/local/bin/freecadcmd",
            "install -d /usr/share/applications /usr/share/icons/hicolor/scalable/apps"
            " && cp -f /opt/freecad/org.freecad.FreeCAD.svg"
            " /usr/share/icons/hicolor/scalable/apps/org.freecad.FreeCAD.svg 2>/dev/null || true",
            "printf '[Desktop Entry]\\nName=FreeCAD\\nComment=Parametric 3D CAD / BIM / FEM\\n"
            "Exec=/usr/local/bin/freecad %%F\\nIcon=org.freecad.FreeCAD\\nTerminal=false\\n"
            "Type=Application\\nCategories=Graphics;Science;Engineering;\\n'"
            " > /usr/share/applications/org.freecad.FreeCAD.desktop",
            "sed -i 's/# en_US.UTF-8/en_US.UTF-8/; s/# ar_EG.UTF-8/ar_EG.UTF-8/' /etc/locale.gen"
            " && locale-gen",
            "rm -rf /var/lib/apt/lists/*",
        )
        .pip_install(*PIP_PACKAGES)
        # Keep the compiled /usr/bin/gmsh GUI ahead of the pip wrapper in /usr/local/bin/gmsh
        .run_commands("ln -sf /usr/bin/gmsh /usr/local/bin/gmsh")
        .env({
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "QT_X11_NO_MITSHM": "1",
            "QTWEBENGINE_DISABLE_SANDBOX": "1",
            "OMPI_ALLOW_RUN_AS_ROOT": "1",
            "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": "1",
        })
        .add_local_dir(HERE / "rootfs" / "opt" / "ws", "/opt/ws", copy=True)
        .run_commands("chmod +x /opt/ws/*.sh /opt/ws/*.py")
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def log(*a) -> None:
    print("[ws]", *a, file=sys.stderr, flush=True)


def out(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def get_app() -> modal.App:
    return modal.App.lookup(APP_NAME, create_if_missing=True)


def get_volume() -> modal.Volume:
    # v2 volumes support `sync <mountpoint>` for immediate commits from sandboxes.
    return modal.Volume.from_name(VOLUME_NAME, create_if_missing=True, version=2)


def find_desktop() -> modal.Sandbox | None:
    try:
        sb = modal.Sandbox.from_name(APP_NAME, DESKTOP_NAME)
    except modal.exception.NotFoundError:
        return None
    return sb if sb.poll() is None else None


def profile_or_die(name: str) -> dict:
    if name not in PROFILES:
        out({"ok": False, "error": f"unknown profile '{name}'", "profiles": list(PROFILES)}, 2)
    return PROFILES[name]


def cost_hint(p: dict) -> dict:
    cpu_req, cpu_lim = p["cpu"]
    mem_req, mem_lim = p["memory"]
    idle = cpu_req * PRICE_CPU_CORE_HOUR + (mem_req / 1024) * PRICE_GIB_HOUR
    full = cpu_lim * PRICE_CPU_CORE_HOUR + (mem_lim / 1024) * PRICE_GIB_HOUR
    return {"idle_usd_per_hour": round(idle, 3), "full_load_usd_per_hour": round(full, 2)}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uptime_min(tags: dict) -> float | None:
    try:
        started = datetime.fromisoformat(tags["started"])
        return round((datetime.now(timezone.utc) - started).total_seconds() / 60, 1)
    except Exception:
        return None


def exec_text(sb: modal.Sandbox, *cmd: str, timeout: int = 60) -> tuple[int, str]:
    p = sb.exec(*cmd, timeout=timeout)
    text = p.stdout.read()
    p.wait()
    return p.returncode, text


def desktop_info(sb: modal.Sandbox) -> dict:
    tags = sb.get_tags()
    url = sb.tunnels()[NOVNC_PORT].url
    _, pw = exec_text(sb, "cat", "/run/ws/vncpass")
    pw = pw.strip()
    _, st = exec_text(sb, "cat", "/run/ws/status.json")
    try:
        status = json.loads(st)
    except Exception:
        status = {}
    return {
        "sandbox_id": sb.object_id,
        "profile": tags.get("profile"),
        "uptime_min": uptime_min(tags),
        "url": f"{url}/vnc.html?autoconnect=1&resize=remote&password={pw}",
        "vnc_password": pw,
        "watchdog": status,
    }


def reload_desktop_volume() -> None:
    sb = find_desktop()
    if sb:
        try:
            sb.reload_volumes()
            log("desktop volume reloaded")
        except Exception as e:  # not fatal
            log("could not reload desktop volume:", e)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_build(_a) -> None:
    app = get_app()
    log("building image (first time takes ~10-15 min, then it is cached)...")
    t = time.time()
    with modal.enable_output():
        build_image().build(app)
    out({"ok": True, "built_in_sec": round(time.time() - t)})


def cmd_desktop_start(a) -> None:
    existing = find_desktop()
    if existing:
        info = desktop_info(existing)
        out({"ok": True, "already_running": True, **info})

    p = profile_or_die(a.profile)
    app = get_app()
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    log(f"starting desktop ({a.profile}: {p['desc']}) ...")
    sb = modal.Sandbox.create(
        "/opt/ws/entrypoint.sh",
        app=app,
        name=DESKTOP_NAME,
        image=build_image(),
        volumes={WORK_DIR: get_volume()},
        cpu=p["cpu"],
        memory=p["memory"],
        timeout=int(a.hours * 3600),
        encrypted_ports=[NOVNC_PORT],
        readiness_probe=modal.Probe.with_tcp(NOVNC_PORT),
        env={
            "VNC_PASSWORD": pw,
            "IDLE_MINUTES": str(a.idle),
            "GEOMETRY": a.geometry,
            "WS_CPU_LIMIT": str(p["cpu"][1]),
        },
        tags={"role": "desktop", "profile": a.profile, "started": now_iso(),
              "cpu_limit": str(p["cpu"][1])},
    )
    sb.wait_until_ready(timeout=600)
    info = desktop_info(sb)
    out({
        "ok": True,
        "already_running": False,
        **info,
        "auto_stop": f"after {a.idle} min idle or {a.hours} h max",
        "cost_estimate": cost_hint(p),
    })


def cmd_desktop_stop(_a) -> None:
    sb = find_desktop()
    if not sb:
        out({"ok": True, "message": "no desktop running"})
    info = {"sandbox_id": sb.object_id, "uptime_min": uptime_min(sb.get_tags())}
    try:
        exec_text(sb, "sync", WORK_DIR, timeout=120)  # flush files to the volume
    except Exception as e:
        log("sync failed (continuing):", e)
    sb.terminate()
    out({"ok": True, "stopped": True, **info})


def cmd_desktop_url(_a) -> None:
    sb = find_desktop()
    if not sb:
        out({"ok": False, "error": "no desktop running"}, 1)
    out({"ok": True, **desktop_info(sb)})


JOB_SCRIPT = r"""
set -o pipefail
mkdir -p "$(dirname "$WS_LOG")" "$WS_CWD"
cd "$WS_CWD"
(
  while true; do
    [ -d /run/ws ] && touch /run/ws/keepalive 2>/dev/null || true
    sync /work 2>/dev/null || true
    sleep 15
  done
) &
sync_pid=$!
trap 'kill $sync_pid 2>/dev/null || true; sync /work 2>/dev/null || true' EXIT
echo "[ws] $(date -Is) start: $WS_CMD  (cwd=$WS_CWD, threads=$OMP_NUM_THREADS)" | tee -a "$WS_LOG"
sync /work 2>/dev/null || true
stdbuf -oL -eL bash -lc "$WS_CMD" 2>&1 | tee -a "$WS_LOG"
rc=$?
echo "[ws] $(date -Is) exit code: $rc" | tee -a "$WS_LOG"
exit $rc
"""


def cmd_run(a) -> None:
    job = a.name or datetime.now().strftime("job-%Y%m%d-%H%M%S")
    rel_cwd = a.cwd.lstrip("/")
    if rel_cwd == "work" or rel_cwd.startswith("work/"):
        rel_cwd = rel_cwd[5:]
    cwd = f"{WORK_DIR}/{rel_cwd}" if rel_cwd else WORK_DIR
    log_path = f"{WORK_DIR}/.ws-jobs/{job}.log"

    desktop = find_desktop()
    use_desktop = a.in_desktop or (desktop is not None and a.profile is None)
    profile_name = a.profile or "heavy"
    p = profile_or_die(profile_name)
    threads = str(int(p["cpu"][1] * 2))  # vCPUs

    env = {
        "WS_CMD": a.command,
        "WS_CWD": cwd,
        "WS_LOG": log_path,
        "OMP_NUM_THREADS": threads,
        "CCX_NPROC_EQUATION_SOLVER": threads,
        "MPLBACKEND": "Agg",
    }

    if use_desktop:
        if not desktop:
            out({"ok": False, "error": "--in-desktop given but no desktop is running"}, 1)
        log(f"running inside the open desktop ({desktop.object_id})")
        env["OMP_NUM_THREADS"] = env["CCX_NPROC_EQUATION_SOLVER"] = str(
            int(float(desktop.get_tags().get("cpu_limit", 8)) * 2)
        )
        proc = desktop.exec("bash", "-c", JOB_SCRIPT, env=env, timeout=int(a.hours * 3600))
        for line in proc.stdout:
            print(line, end="", file=sys.stderr)
        proc.wait()
        out({"ok": proc.returncode == 0, "job": job, "where": "desktop",
             "exit_code": proc.returncode, "log": log_path}, 0 if proc.returncode == 0 else 1)

    log(f"starting solver sandbox ({profile_name}: {p['desc']}) job={job}")
    sb = modal.Sandbox.create(
        "bash", "-c", JOB_SCRIPT,
        app=get_app(),
        image=build_image(),
        volumes={WORK_DIR: get_volume()},
        cpu=p["cpu"],
        memory=p["memory"],
        timeout=int(a.hours * 3600),
        env=env,
        tags={"role": "job", "job": job, "profile": profile_name, "started": now_iso()},
    )
    if a.detach:
        out({"ok": True, "job": job, "where": "sandbox", "sandbox_id": sb.object_id,
             "detached": True, "log": log_path,
             "hint": f"ws job-log {job}   |   ws status   |   ws stop --id {sb.object_id}"})

    for line in sb.stdout:
        print(line, end="", file=sys.stderr)
    sb.wait()
    rc = sb.returncode
    reload_desktop_volume()
    out({"ok": rc == 0, "job": job, "where": "sandbox", "exit_code": rc, "log": log_path},
        0 if rc == 0 else 1)


def cmd_job_log(a) -> None:
    vol = get_volume()
    path = f".ws-jobs/{a.job}.log"
    try:
        data = b"".join(vol.read_file(path)).decode(errors="replace")
    except Exception as e:
        out({"ok": False, "error": f"cannot read {path}: {e}"}, 1)
    lines = data.splitlines()
    out({"ok": True, "job": a.job, "total_lines": len(lines), "tail": lines[-a.lines:]})


def cmd_status(_a) -> None:
    app = get_app()
    items = []
    for sb in modal.Sandbox.list(app_id=app.app_id):
        if sb.poll() is not None:
            continue
        tags = sb.get_tags()
        prof = PROFILES.get(tags.get("profile", ""), PROFILES["daily"])
        items.append({
            "sandbox_id": sb.object_id,
            "role": tags.get("role"),
            "job": tags.get("job"),
            "profile": tags.get("profile"),
            "uptime_min": uptime_min(tags),
            "cost_estimate": cost_hint(prof),
        })
    out({"ok": True, "running": items, "count": len(items)})


def cmd_stop(a) -> None:
    app = get_app()
    stopped = []
    for sb in modal.Sandbox.list(app_id=app.app_id):
        if sb.poll() is not None:
            continue
        if a.all or sb.object_id == a.id:
            try:
                exec_text(sb, "sync", WORK_DIR, timeout=60)
            except Exception:
                pass
            sb.terminate()
            stopped.append(sb.object_id)
    out({"ok": True, "stopped": stopped})


def cmd_push(a) -> None:
    src = Path(a.local).expanduser()
    if not src.exists():
        out({"ok": False, "error": f"{src} not found"}, 1)
    remote = a.remote.lstrip("/")
    if remote.startswith("work/"):
        remote = remote[5:]
    vol = get_volume()
    with vol.batch_upload(force=True) as batch:
        if src.is_dir():
            batch.put_directory(str(src), "/" + remote.rstrip("/"))
            dest = remote
        else:
            dest = f"{remote.rstrip('/')}/{src.name}" if (remote == "" or remote.endswith("/")) else remote
            batch.put_file(str(src), "/" + dest)
    reload_desktop_volume()
    out({"ok": True, "uploaded": str(src), "to": f"{WORK_DIR}/{dest}"})


def _etype(e) -> str:
    t = getattr(e, "type", None)
    return getattr(t, "name", str(t))  # FileEntryType is an IntEnum: FILE / DIRECTORY


def cmd_pull(a) -> None:
    vol = get_volume()
    remote = a.remote.lstrip("/")
    if remote.startswith("work/"):
        remote = remote[5:]
    dest = Path(a.local).expanduser()
    try:
        entries = vol.listdir(remote or "/", recursive=True)
    except Exception:
        entries = []
    files = [e for e in entries if _etype(e) == "FILE" and e.path.rstrip("/") != remote.rstrip("/")]
    got = []
    if not files:  # a single file path
        if dest.is_dir() or a.local.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / Path(remote).name
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            target = dest
        with open(target, "wb") as f:
            vol.read_file_into_fileobj(remote, f)
        got.append(str(target))
    else:
        base = remote.rstrip("/")
        for e in files:
            rel = e.path[len(base):].lstrip("/") if base and e.path.startswith(base) else e.path
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as f:
                vol.read_file_into_fileobj(e.path, f)
            got.append(str(target))
    out({"ok": True, "downloaded": len(got), "files": got[:50]})


def cmd_ls(a) -> None:
    vol = get_volume()
    path = a.path.lstrip("/")
    if path.startswith("work"):
        path = path[4:].lstrip("/")
    entries = vol.listdir(path or "/", recursive=a.recursive)
    out({"ok": True, "path": f"{WORK_DIR}/{path}", "entries": [
        {"path": e.path, "type": _etype(e), "size": getattr(e, "size", None)}
        for e in entries][:500]})


def cmd_profiles(_a) -> None:
    out({"ok": True, "profiles": {k: {"desc": v["desc"], **cost_hint(v)} for k, v in PROFILES.items()}})


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(prog="ws", description="Modal structural workstation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", help="build & cache the image").set_defaults(fn=cmd_build)
    sub.add_parser("profiles", help="list hardware profiles").set_defaults(fn=cmd_profiles)
    sub.add_parser("status", help="list running sandboxes").set_defaults(fn=cmd_status)

    d = sub.add_parser("desktop", help="XFCE desktop control")
    dsub = d.add_subparsers(dest="dcmd", required=True)
    ds = dsub.add_parser("start")
    ds.add_argument("--profile", default="daily", choices=list(PROFILES))
    ds.add_argument("--hours", type=float, default=8, help="hard max lifetime (<=24)")
    ds.add_argument("--idle", type=int, default=20, help="auto-stop after N idle minutes")
    ds.add_argument("--geometry", default="1920x1080")
    ds.set_defaults(fn=cmd_desktop_start)
    dsub.add_parser("stop").set_defaults(fn=cmd_desktop_stop)
    dsub.add_parser("url").set_defaults(fn=cmd_desktop_url)

    r = sub.add_parser("run", help="run a command (solver/script) on Modal")
    r.add_argument("command", help='shell command, e.g. "python frame.py"')
    r.add_argument("--cwd", default=WORK_DIR, help="working dir (relative to /work allowed)")
    r.add_argument("--profile", default=None, choices=list(PROFILES),
                   help="hardware for a NEW sandbox (default heavy). If omitted and a "
                        "desktop is open, the command runs inside the desktop.")
    r.add_argument("--in-desktop", action="store_true", help="force running inside the desktop")
    r.add_argument("--detach", action="store_true", help="return immediately (long jobs)")
    r.add_argument("--hours", type=float, default=6, help="max runtime")
    r.add_argument("--name", default=None, help="job name (used for the log file)")
    r.set_defaults(fn=cmd_run)

    jl = sub.add_parser("job-log", help="tail a job log from the volume")
    jl.add_argument("job")
    jl.add_argument("--lines", type=int, default=40)
    jl.set_defaults(fn=cmd_job_log)

    s = sub.add_parser("stop", help="stop sandboxes")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--id")
    g.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_stop)

    pu = sub.add_parser("push", help="upload local file/dir to the volume")
    pu.add_argument("local")
    pu.add_argument("remote", nargs="?", default="inbox/")
    pu.set_defaults(fn=cmd_push)

    pl = sub.add_parser("pull", help="download from the volume")
    pl.add_argument("remote")
    pl.add_argument("local", nargs="?", default=".")
    pl.set_defaults(fn=cmd_pull)

    ls = sub.add_parser("ls", help="list files on the volume")
    ls.add_argument("path", nargs="?", default="/")
    ls.add_argument("-r", "--recursive", action="store_true")
    ls.set_defaults(fn=cmd_ls)

    a = ap.parse_args()
    if getattr(a, "hours", None) and a.hours > 24:
        out({"ok": False, "error": "Modal sandboxes are limited to 24 hours"}, 2)
    try:
        a.fn(a)
    except SystemExit:
        raise
    except Exception as e:  # always answer in JSON
        out({"ok": False, "error": f"{type(e).__name__}: {e}"}, 1)


if __name__ == "__main__":
    main()
