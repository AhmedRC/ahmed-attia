---
name: modal-workstation
description: Start/stop a Modal Linux XFCE desktop and run structural solvers on demand
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [modal, structural, freecad, opensees, calculix, desktop, cost]
    category: engineering
    requires_toolsets: [terminal]
---

# Modal Structural Workstation

On-demand Ubuntu + XFCE4 desktop and solver machines on Modal, controlled through the `ws` CLI that is installed on this VPS. You pay only while a sandbox is running. Stopping idle machines is part of the job.

Software in the image: FreeCAD (FEM/BIM/Rebar), LibreCAD, Gmsh, ParaView, CalculiX (`ccx`, `cgx`), Firefox, and Python 3.11 with openseespy, PyNiteFEA, anastruct, sectionproperties, gmsh, meshio, ezdxf, ccx2paraview, numpy, scipy, pandas, and matplotlib.
Persistent storage: Modal Volume mounted at `/work` (projects in `/work/projects/<name>`, uploads in `/work/inbox`).

## When to Use
- The user wants a remote Linux desktop or GUI: "شغل الديسكتوب", "افتح الجهاز", "open FreeCAD".
- The user wants a heavy analysis: an OpenSees, CalculiX, or Python FEA script, meshing, or a parametric study.
- The user asks what is running or what it costs, or asks to stop or close the machine.
- The user wants to move files between the VPS/chat and the workstation.

## Procedure
All commands print JSON on stdout. Read `ok`, `url`, `error`, `exit_code`.

1. **Open a desktop:** `ws desktop start --profile daily` (default). If it is already running, the same URL comes back.
   - Profiles: `light` (4 cores/16 GiB), `daily` (8 cores/32 GiB), `heavy` (32 cores/64 GiB). Use `heavy` only when the user asks for big FEA work in the GUI.
   - Options: `--idle 20` sets auto-stop after N idle minutes. `--hours 8` sets the hard limit (max 24).
   - Send the user the `url` field as a link. It already contains the password and auto-connects. Mention the idle auto-stop.
2. **Desktop link again:** `ws desktop url`
3. **Stop the desktop:** `ws desktop stop`. Do this whenever the user says they are done.
4. **Run a solver or script (headless, cheapest for heavy work):**
   - Write or upload the input first, under `/work/projects/<project>/`.
   - `ws run "python model.py" --cwd projects/<project> --profile heavy`
   - CalculiX: `ws run "ccx -i beam" --cwd projects/<project> --profile heavy`
   - If the job may take longer than ~10 minutes, add `--detach`, then poll with `ws job-log <job>` and `ws status`.
   - If a desktop is open and `--profile` is omitted, the command runs inside the desktop, on the same machine and files.
5. **Files:**
   - Upload from the VPS: `ws push <local_path> projects/<project>/`
   - Download results to the VPS: `ws pull projects/<project>/results ./results`. Then send the files to the user (images, PDF, CSV).
   - List files: `ws ls projects -r`
6. **Status and cost:** `ws status` lists running sandboxes, their uptime, and cost estimates. `ws profiles` shows the price table.
7. **Emergency stop:** `ws stop --all`

## Writing analysis scripts
- Use `MPLBACKEND=Agg`, which is already set for jobs. Save plots as PNG into the project folder.
- OpenSeesPy: `import openseespy.opensees as ops`. PyNite: `from Pynite import FEModel3D` (older releases: `from PyNite import FEModel3D`).
- Print a short summary (max displacement, max moment, utilisation) at the end so it shows up in the log.
- Write results to `/work/projects/<project>/results/`.

## Pitfalls
- Never leave a sandbox running after the user is done. Check `ws status` and stop it. The desktop also stops itself after the idle time.
- Modal cores are physical cores, and 1 core = 2 vCPU. Billing is max(request, actual usage). Idle costs cents, while full load on `heavy` is about $6/h.
- The first `ws desktop start` after an image change can take several minutes while the image builds. Run `ws build` ahead of time.
- Files uploaded with `ws push`, and results from a separate solver sandbox, are reloaded into an open desktop automatically. If something is missing, run `ws desktop url` and refresh the file manager.
- Sandboxes have a 24 h maximum lifetime. Anything outside `/work` is lost when they stop.
- The URL is a secret. Do not post it in group chats.

## Verification
- `ws desktop start` returns `"ok": true` and a `url` that loads noVNC.
- `ws run ...` returns `"exit_code": 0`, and `ws job-log <job>` shows the summary lines.
- `ws status` shows `"count": 0` after you stop everything.
