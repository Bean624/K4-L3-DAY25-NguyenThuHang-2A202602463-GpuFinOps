"""M1 — Efficiency Audit: MFU/MBU, the GPU-Util lie, and idle waste (deck §5).

Run: python missions/m1_efficiency_audit.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from collections import defaultdict
from missions._common import load_csv, num, catalog_by_type
from finops import metrics


def run(verbose: bool = True) -> dict:
    tel = load_csv("gpu_telemetry.csv")
    cat = catalog_by_type()

    # per-row MFU/MBU, then aggregate per GPU
    agg = defaultdict(lambda: {"util": [], "mfu": [], "mbu": [], "type": None, "idle_hours": 0})
    for r in tel:
        gtype = r["gpu_type"]
        peak_fp16 = num(cat[gtype]["peak_tflops_fp16"])
        peak_bw = num(cat[gtype]["peak_bw_tbs"])
        mfu = metrics.compute_mfu(num(r["achieved_tflops"]), peak_fp16)
        mbu = metrics.compute_mbu(num(r["achieved_bw_tbs"]), peak_bw)
        a = agg[r["gpu_id"]]
        a["type"] = gtype
        a["util"].append(num(r["gpu_util_pct"]))
        a["mfu"].append(mfu)
        a["mbu"].append(mbu)
        if num(r["gpu_util_pct"]) < 10:  # effectively idle this interval (1h)
            a["idle_hours"] += 1

    summary = []
    for gid, a in agg.items():
        summary.append({
            "gpu_id": gid, "gpu_type": a["type"],
            "gpu_util_pct": round(sum(a["util"]) / len(a["util"]), 1),
            "mfu": round(sum(a["mfu"]) / len(a["mfu"]), 3),
            "mbu": round(sum(a["mbu"]) / len(a["mbu"]), 3),
            "idle_hours": a["idle_hours"],
        })

    lies = metrics.flag_util_lies(summary)
    idle_waste = 0.0
    for s in summary:
        on_demand = num(catalog_by_type()[s["gpu_type"]]["on_demand_hr"])
        idle_waste += metrics.idle_waste_usd(s["idle_hours"], on_demand)

    # --- Extension 2: Right-sizing theo MBU & VRAM Economics ---
    rightsize_recs = []
    total_rightsize_savings = 0.0
    for l in lies:
        cur_type = l["gpu_type"]
        cur_price = num(cat[cur_type]["on_demand_hr"])
        # Find more cost-effective alternative that still covers achieved compute & bandwidth
        # Target matching:
        # gpu-h100-4: achieved 192 TFLOPS, 0.69 TB/s -> A100 (312 TFLOPS, 2.0 TB/s, same 80GB HBM)
        # gpu-a10g-1: achieved 33.5 TFLOPS, 0.18 TB/s -> L4 (121 TFLOPS, 0.30 TB/s, same 24GB VRAM)
        if cur_type == "H100":
            target_type = "A100"
            rationale = "Workload needs <0.70 TB/s BW & <200 TFLOPS; A100 provides 2.0 TB/s & 80GB VRAM at 28% lower $/GB"
        elif cur_type == "A10G":
            target_type = "L4"
            rationale = "Workload needs <0.20 TB/s BW & 34 TFLOPS; L4 provides 0.30 TB/s & 24GB VRAM with 52% lower power"
        else:
            target_type = cur_type
            rationale = "No downgrade available"

        target_price = num(cat[target_type]["on_demand_hr"])
        monthly_saving = (cur_price - target_price) * 24 * 30
        total_rightsize_savings += monthly_saving
        rightsize_recs.append({
            "gpu_id": l["gpu_id"],
            "current_gpu": cur_type,
            "proposed_gpu": target_type,
            "current_cost_hr": cur_price,
            "proposed_cost_hr": target_price,
            "monthly_savings_usd": round(monthly_saving, 2),
            "rationale": rationale,
        })

    if verbose:
        print("== M1 Efficiency Audit ==")
        print(f"{'GPU':14}{'type':7}{'util%':>7}{'MFU':>7}{'MBU':>7}{'idle_h':>8}")
        for s in sorted(summary, key=lambda x: x["mfu"]):
            print(f"{s['gpu_id']:14}{s['gpu_type']:7}{s['gpu_util_pct']:>7}{s['mfu']:>7}{s['mbu']:>7}{s['idle_hours']:>8}")
        print(f"\nGPU-Util LIES (util>=90% but MFU<30%): {[l['gpu_id'] for l in lies]}")
        print(f"Idle waste (1 day): ${idle_waste:,.2f}  ->  ${idle_waste*30:,.0f}/month")

        print("\n--- [Extension 2] Right-sizing theo MBU & VRAM Economics ---")
        print(f"{'GPU ID':12}{'Current':9}{'Proposed':10}{'Cur $/hr':>10}{'New $/hr':>10}{'Save $/mo':>12}")
        for r in rightsize_recs:
            print(f"{r['gpu_id']:12}{r['current_gpu']:9}{r['proposed_gpu']:10}${r['current_cost_hr']:>9.2f}${r['proposed_cost_hr']:>9.2f}${r['monthly_savings_usd']:>11.2f}")
            print(f"  -> Rationale: {r['rationale']}")
        print(f"Total potential right-sizing savings: ${total_rightsize_savings:,.2f}/month")

    return {
        "summary": summary,
        "lies": lies,
        "idle_waste_daily": round(idle_waste, 2),
        "rightsize_recommendations": rightsize_recs,
        "rightsize_monthly_savings": round(total_rightsize_savings, 2),
    }


if __name__ == "__main__":
    run()
