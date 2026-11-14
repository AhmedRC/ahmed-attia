"""
Smoke test / example: 2D reinforced-concrete portal frame in OpenSeesPy.
Linear static analysis under gravity + lateral load, then a modal analysis.

Run on Modal:
    ws push examples/opensees_portal_frame.py projects/demo/
    ws run "python opensees_portal_frame.py" --cwd projects/demo --profile light
Results -> /work/projects/demo/results/
"""
import os

import matplotlib.pyplot as plt
import openseespy.opensees as ops

OUT = os.path.join(os.getcwd(), "results")
os.makedirs(OUT, exist_ok=True)

# ---- geometry & material (kN, m) ----
H, L = 3.5, 6.0  # storey height, bay width
E = 25e6  # concrete E (kPa)
col = dict(A=0.40 * 0.40, I=0.40 * 0.40**3 / 12)
beam = dict(A=0.30 * 0.60, I=0.30 * 0.60**3 / 12)
w = -30.0  # beam UDL kN/m
P_lat = 50.0  # lateral load kN

ops.wipe()
ops.model("basic", "-ndm", 2, "-ndf", 3)
ops.node(1, 0, 0); ops.node(2, L, 0); ops.node(3, 0, H); ops.node(4, L, H)
ops.fix(1, 1, 1, 1); ops.fix(2, 1, 1, 1)
ops.mass(3, 40, 40, 0); ops.mass(4, 40, 40, 0)
ops.geomTransf("Linear", 1)
ops.element("elasticBeamColumn", 1, 1, 3, col["A"], E, col["I"], 1)
ops.element("elasticBeamColumn", 2, 2, 4, col["A"], E, col["I"], 1)
ops.element("elasticBeamColumn", 3, 3, 4, beam["A"], E, beam["I"], 1)

ops.timeSeries("Linear", 1)
ops.pattern("Plain", 1, 1)
ops.eleLoad("-ele", 3, "-type", "-beamUniform", w)
ops.load(3, P_lat, 0, 0)

ops.system("BandGeneral"); ops.numberer("RCM"); ops.constraints("Plain")
ops.integrator("LoadControl", 1.0); ops.algorithm("Linear"); ops.analysis("Static")
ops.analyze(1)

dx = ops.nodeDisp(3, 1)
f_beam = ops.eleForce(3)
f_c1, f_c2 = ops.eleForce(1), ops.eleForce(2)

ops.wipeAnalysis()
periods = [2 * 3.141592653589793 / (lam**0.5) for lam in ops.eigen(2)]

# ---- plot deformed shape ----
scale = 0.25 * H / max(abs(dx), 1e-9)
xy = {n: (ops.nodeCoord(n)[0], ops.nodeCoord(n)[1]) for n in (1, 2, 3, 4)}
d = {n: (ops.nodeDisp(n, 1), ops.nodeDisp(n, 2)) for n in (1, 2, 3, 4)}
fig, ax = plt.subplots(figsize=(6, 4))
for a, b in [(1, 3), (2, 4), (3, 4)]:
    ax.plot([xy[a][0], xy[b][0]], [xy[a][1], xy[b][1]], "k-", lw=2)
    ax.plot([xy[a][0] + scale * d[a][0], xy[b][0] + scale * d[b][0]],
            [xy[a][1] + scale * d[a][1], xy[b][1] + scale * d[b][1]], "r--")
ax.set_aspect("equal"); ax.set_title(f"Deformed shape (x{scale:.0f}) - drift {dx*1000:.2f} mm")
fig.savefig(os.path.join(OUT, "portal_deformed.png"), dpi=150, bbox_inches="tight")

summary = (
    f"Top drift            : {dx*1000:.3f} mm  (drift ratio 1/{H/abs(dx):.0f})\n"
    f"Beam end moments     : {f_beam[2]:.1f} / {f_beam[5]:.1f} kN.m\n"
    f"Column base moments  : {f_c1[2]:.1f} / {f_c2[2]:.1f} kN.m\n"
    f"Periods T1, T2       : {periods[0]:.3f} s, {periods[1]:.3f} s\n"
)
open(os.path.join(OUT, "summary.txt"), "w").write(summary)
print(summary)
print("saved:", os.path.join(OUT, "portal_deformed.png"))
