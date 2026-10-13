"""M3 — Purchasing Strategy: break-even, tier choice, spot-checkpoint sim (deck §4).

Run: python missions/m3_purchasing.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from missions._common import load_csv, num, catalog_by_type
from finops import pricing

DAYS = 30


def run(verbose: bool = True) -> dict:
    jobs = load_csv("workloads.csv")
    cat = catalog_by_type()
    on_demand_monthly = optimized_monthly = 0.0
    recs = []
    for j in jobs:
        gtype = j["gpu_type"]
        ngpu = int(num(j["num_gpus"]))
        hpd = num(j["hours_per_day"])
        interruptible = bool(int(num(j["interruptible"])))
        c = cat[gtype]
        gpu_hours = hpd * DAYS * ngpu
        od = num(c["on_demand_hr"])
        on_demand_cost = gpu_hours * od

        tier = pricing.recommend_tier(hpd, interruptible)
        if tier == "spot":
            sim = pricing.spot_checkpoint_cost(gpu_hours, num(c["spot_hr"]), od)
            opt_cost = sim["spot_cost"]
        elif tier == "reserved":
            opt_cost = gpu_hours * num(c["reserved_3yr_hr"])
        else:
            opt_cost = on_demand_cost

        on_demand_monthly += on_demand_cost
        optimized_monthly += opt_cost
        recs.append({"job_id": j["job_id"], "gpu_type": gtype, "tier": tier,
                     "on_demand": round(on_demand_cost), "optimized": round(opt_cost)})

    # --- Extension 1: Tier Recommendation Matrix & Detailed Evaluation ---
    matrix_recs = []
    enhanced_optimized_monthly = 0.0
    for j in jobs:
        gtype = j["gpu_type"]
        ngpu = int(num(j["num_gpus"]))
        hpd = num(j["hours_per_day"])
        interruptible = bool(int(num(j["interruptible"])))
        c = cat[gtype]
        details = pricing.recommend_tier_detailed(
            hpd, interruptible, gtype, job_days=DAYS,
            on_demand_hr=num(c["on_demand_hr"]),
            spot_hr=num(c["spot_hr"]),
            reserved_1yr_hr=num(c["reserved_1yr_hr"]),
            reserved_3yr_hr=num(c["reserved_3yr_hr"]),
        )
        opt_tier = details["best_tier"]
        # Multiply single-GPU job cost by num_gpus
        opt_cost = details["options_cost"][opt_tier] * ngpu
        enhanced_optimized_monthly += opt_cost
        matrix_recs.append({
            "job_id": j["job_id"],
            "gpu_type": gtype,
            "num_gpus": ngpu,
            "hpd": hpd,
            "interruptible": interruptible,
            "interruption_rate": details["interruption_rate"],
            "best_tier": opt_tier,
            "best_cost": round(opt_cost),
            "options": {k: round(v * ngpu) for k, v in details["options_cost"].items()},
        })

    # --- Extension 5: Carbon-Aware Scheduling for Interruptible Workloads ---
    from finops.sustainability import REGION_CARBON, REGION_PRICE_KWH

    total_interruptible_kwh = 0.0
    for j in jobs:
        if bool(int(num(j["interruptible"]))):
            gtype = j["gpu_type"]
            ngpu = int(num(j["num_gpus"]))
            hpd = num(j["hours_per_day"])
            gpu_hours = hpd * DAYS * ngpu
            watts = num(cat[gtype]["watts"])
            total_interruptible_kwh += (watts * gpu_hours) / 1000.0

    base_region = "us-east-1"
    base_carbon_kg = (total_interruptible_kwh * REGION_CARBON[base_region]) / 1000.0
    region_comparison = {}
    for reg, intensity in REGION_CARBON.items():
        price = REGION_PRICE_KWH[reg]
        cost = total_interruptible_kwh * price
        carb_kg = (total_interruptible_kwh * intensity) / 1000.0
        pct_saved = ((base_carbon_kg - carb_kg) / base_carbon_kg) * 100.0
        region_comparison[reg] = {
            "price_kwh": price,
            "gco2_kwh": intensity,
            "elec_cost_usd": round(cost, 2),
            "carbon_kg": round(carb_kg, 1),
            "carbon_saved_pct": round(pct_saved, 1),
        }

    cleanest_reg = min(REGION_CARBON, key=REGION_CARBON.get)
    cheapest_reg = min(REGION_PRICE_KWH, key=REGION_PRICE_KWH.get)

    savings = on_demand_monthly - optimized_monthly
    savings_pct = savings / on_demand_monthly * 100 if on_demand_monthly else 0.0

    if verbose:
        print("== M3 Purchasing Strategy ==")
        print(f"break-even utilization @ 45% reserved discount = {pricing.break_even_utilization(0.45):.0%}")
        print(f"{'job':18}{'gpu':7}{'tier':11}{'on-demand':>12}{'optimized':>12}")
        for r in recs:
            print(f"{r['job_id']:18}{r['gpu_type']:7}{r['tier']:11}${r['on_demand']:>11,}${r['optimized']:>11,}")
        print(f"\nmonthly: on-demand ${on_demand_monthly:,.0f} -> optimized ${optimized_monthly:,.0f}  ({savings_pct:.1f}% saved)")

        print("\n--- [Extension 1] Matrix Tier Evaluation (with GPU-specific Preemption) ---")
        print(f"{'Job':18}{'GPU':7}{'Preempt%':>9}{'Best Tier':>13}{'On-Demand':>12}{'Res-1yr':>11}{'Res-3yr':>11}{'Spot':>11}")
        for m in matrix_recs:
            opt = m["options"]
            spot_str = f"${opt.get('spot', 0):,}" if 'spot' in opt else "N/A"
            print(f"{m['job_id']:18}{m['gpu_type']:7}{m['interruption_rate']*100:>8.0f}%{m['best_tier']:>13}${opt['on_demand']:>11,}${opt['reserved_1yr']:>10,}${opt['reserved_3yr']:>10,}{spot_str:>11}")

        print("\n--- [Extension 5] Carbon-Aware Scheduling for Interruptible Workloads ---")
        print(f"Total Interruptible Compute: {total_interruptible_kwh:,.1f} kWh/month (5 interruptible jobs)")
        print(f"{'Region':16}{'$/kWh':>8}{'gCO2/kWh':>10}{'Elec Cost $/mo':>16}{'Carbon kg CO2e':>16}{'Carbon Reduction':>18}")
        for reg, d in region_comparison.items():
            print(f"{reg:16}${d['price_kwh']:>7.3f}{d['gco2_kwh']:>10}${d['elec_cost_usd']:>15.2f}{d['carbon_kg']:>16.1f}{d['carbon_saved_pct']:>17.1f}%")
        print(f"Cleanest Region: {cleanest_reg} (reduces carbon by {region_comparison[cleanest_reg]['carbon_saved_pct']}%)")
        print(f"Cheapest Electricity: {cheapest_reg} (electricity cost: ${region_comparison[cheapest_reg]['elec_cost_usd']}/mo, saves {100*(1 - region_comparison[cheapest_reg]['elec_cost_usd']/region_comparison[base_region]['elec_cost_usd']):.1f}%)")
        print("Latency Trade-off: Interruptible training & batch jobs have no real-time latency requirement; shifting to europe-north1 or us-east-wa incurs 0% user SLA degradation.")

    return {
        "recommendations": recs,
        "on_demand_monthly": round(on_demand_monthly),
        "optimized_monthly": round(optimized_monthly),
        "savings_pct": round(savings_pct, 1),
        "matrix_recommendations": matrix_recs,
        "enhanced_optimized_monthly": round(enhanced_optimized_monthly),
        "region_comparison": region_comparison,
        "cleanest_region": cleanest_reg,
        "cheapest_region": cheapest_reg,
    }


if __name__ == "__main__":
    run()
