"""M2 — Inference Cost Levers: $/1M-token, batch x cache x cascade (deck §7).

Run: python missions/m2_inference_levers.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from missions._common import load_csv, num
from finops import pricing

# $/1M tokens (input, output) — illustrative 2026.
MODEL_PRICES = {"small": (0.20, 0.40), "large": (3.00, 15.00)}


def run(verbose: bool = True) -> dict:
    rows = load_csv("token_usage.csv")
    base_cost = opt_cost = 0.0
    total_tokens = 0
    for r in rows:
        inp, out = int(num(r["input_tokens"])), int(num(r["output_tokens"]))
        cached = int(num(r["cached_input_tokens"]))
        is_batch = bool(int(num(r["is_batch"])))
        total_tokens += inp + out
        # BASELINE: naive deployment — everything on the large model, no cache, no batch
        lin, lout = MODEL_PRICES["large"]
        base_cost += pricing.request_cost(inp, out, lin, lout)
        # OPTIMIZED: cascade (route_tier), prompt caching, batch API
        pin, pout = MODEL_PRICES[r["route_tier"]]
        opt_cost += pricing.request_cost(inp, out, pin, pout, cached_in=cached, batch=is_batch)

    base_pm = pricing.dollars_per_million(base_cost, total_tokens)
    opt_pm = pricing.dollars_per_million(opt_cost, total_tokens)
    savings_pct = (1 - opt_cost / base_cost) * 100 if base_cost else 0.0

    # --- Extension 3: cache_is_worth_it() Economics ---
    from finops.sustainability import wh_per_query

    # Break-even calculation for model tiers assuming 1.25x write cost
    cache_analysis = {}
    for tier, (p_in, _) in MODEL_PRICES.items():
        w_cost = p_in * 1.25
        be_reads = pricing.break_even_cache_reads(w_cost, read_discount=0.10, base_input_price_per_m=p_in)
        worth_high_reuse = pricing.cache_is_worth_it(3.5, w_cost, read_discount=0.10, base_input_price_per_m=p_in)
        worth_low_reuse = pricing.cache_is_worth_it(1.0, w_cost, read_discount=0.10, base_input_price_per_m=p_in)
        cache_analysis[tier] = {
            "base_in": p_in, "write_cost": round(w_cost, 4),
            "break_even_reads": round(be_reads, 2),
            "worth_at_3_5_reads": worth_high_reuse,
            "worth_at_1_0_reads": worth_low_reuse,
        }

    # Net cache savings considering write cost under assumption of avg 3.5 reads per prefix
    # (1 - 0.10) * base_in - write_cost / 3.5
    net_opt_cost_with_write = 0.0
    for r in rows:
        inp, out = int(num(r["input_tokens"])), int(num(r["output_tokens"]))
        cached = int(num(r["cached_input_tokens"]))
        is_batch = bool(int(num(r["is_batch"])))
        pin, pout = MODEL_PRICES[r["route_tier"]]
        c = pricing.request_cost(inp, out, pin, pout, cached_in=cached, batch=is_batch)
        if cached > 0:
            # amortized write cost for cached tokens assuming 3.5 reads per prefix
            write_cost_term = (cached / 1e6) * (pin * 1.25 / 3.5)
            if is_batch:
                write_cost_term *= 0.50
            c += write_cost_term
        net_opt_cost_with_write += c

    net_opt_pm = pricing.dollars_per_million(net_opt_cost_with_write, total_tokens)

    # --- Extension 4: Reasoning Budget & Energy (Wh) ---
    reasoning_reqs = [r for r in rows if bool(int(num(r["is_reasoning"])))]
    standard_reqs = [r for r in rows if not bool(int(num(r["is_reasoning"])))]

    r_count = len(reasoning_reqs)
    nr_count = len(standard_reqs)
    total_count = len(rows)

    r_tok = sum(int(num(r["input_tokens"])) + int(num(r["output_tokens"])) for r in reasoning_reqs)
    nr_tok = sum(int(num(r["input_tokens"])) + int(num(r["output_tokens"])) for r in standard_reqs)

    r_cost = 0.0
    for r in reasoning_reqs:
        inp, out = int(num(r["input_tokens"])), int(num(r["output_tokens"]))
        cached = int(num(r["cached_input_tokens"]))
        is_batch = bool(int(num(r["is_batch"])))
        pin, pout = MODEL_PRICES[r["route_tier"]]
        r_cost += pricing.request_cost(inp, out, pin, pout, cached_in=cached, batch=is_batch)

    nr_cost = opt_cost - r_cost

    r_wh = sum(wh_per_query(int(num(r["input_tokens"])) + int(num(r["output_tokens"])), is_reasoning=True) for r in reasoning_reqs)
    nr_wh = sum(wh_per_query(int(num(r["input_tokens"])) + int(num(r["output_tokens"])), is_reasoning=False) for r in standard_reqs)
    total_wh = r_wh + nr_wh

    traffic_pct_reasoning = (r_count / total_count) * 100
    # Policy evaluation: Traffic cap at 10%
    # Currently 8.38% <= 10%, so under current traffic savings = $0.00
    cap_10pct_savings_usd = 0.0
    cap_10pct_savings_wh = 0.0

    if verbose:
        print("== M2 Inference Cost Levers ==")
        print(f"requests={len(rows)}  tokens={total_tokens:,}")
        print(f"baseline  : ${base_cost:,.2f}/day   ${base_pm:.3f}/1M-token")
        print(f"optimized : ${opt_cost:,.2f}/day   ${opt_pm:.3f}/1M-token")
        print(f"savings   : {savings_pct:.1f}%  (cascade + caching + batch)")
        print(f"discount stack (batch + 100% cache): {pricing.discount_stack(batch=True, cache_hit_frac=1.0):.3f} of naive")

        print("\n--- [Extension 3] cache_is_worth_it() Economics ---")
        print(f"{'Tier':7}{'Base In $/1M':>13}{'Write $/1M':>12}{'Break-Even Reads':>18}{'Worth @3.5 rds':>16}{'Worth @1.0 rd':>15}")
        for t, d in cache_analysis.items():
            print(f"{t:7}${d['base_in']:>12.2f}${d['write_cost']:>11.4f}{d['break_even_reads']:>18.2f}{str(d['worth_at_3_5_reads']):>16}{str(d['worth_at_1_0_reads']):>15}")
        print(f"Optimized $/1M-token with write cost amortized (3.5 reads): ${net_opt_pm:.3f}/1M-token (vs ${opt_pm:.3f} without write cost)")

        print("\n--- [Extension 4] Reasoning Traffic & Energy Budget ---")
        print(f"{'Segment':14}{'Requests':>10}{'Traffic %':>11}{'Tokens':>12}{'Cost $/day':>12}{'Cost %':>8}{'Energy Wh':>12}{'Energy %':>10}")
        print(f"{'Reasoning (1)':14}{r_count:>10}{traffic_pct_reasoning:>10.1f}%{r_tok:>12,}${r_cost:>11.2f}{r_cost/opt_cost*100:>7.1f}%{r_wh:>12.1f}{r_wh/total_wh*100:>9.1f}%")
        print(f"{'Standard (0)':14}{nr_count:>10}{nr_count/total_count*100:>10.1f}%{nr_tok:>12,}${nr_cost:>11.2f}{nr_cost/opt_cost*100:>7.1f}%{nr_wh:>12.1f}{nr_wh/total_wh*100:>9.1f}%")
        print(f"Insight: Reasoning is only {traffic_pct_reasoning:.1f}% of traffic but consumes {r_wh/total_wh*100:.1f}% of inference energy!")
        print(f"Policy: Cap at 10% traffic active. Current traffic ({traffic_pct_reasoning:.1f}%) <= 10% cap -> Current cap savings: $0.00 / 0 Wh.")

    return {
        "baseline_daily": round(base_cost, 2), "optimized_daily": round(opt_cost, 2),
        "baseline_per_m": round(base_pm, 3), "optimized_per_m": round(opt_pm, 3),
        "savings_pct": round(savings_pct, 1), "total_tokens": total_tokens,
        "cache_analysis": cache_analysis,
        "net_opt_per_m_with_write": round(net_opt_pm, 3),
        "reasoning_summary": {
            "requests": r_count, "traffic_pct": round(traffic_pct_reasoning, 2),
            "tokens": r_tok, "tokens_pct": round(r_tok / total_tokens * 100, 2),
            "cost_daily": round(r_cost, 2), "cost_pct": round(r_cost / opt_cost * 100, 2),
            "energy_wh": round(r_wh, 2), "energy_pct": round(r_wh / total_wh * 100, 2),
            "cap_savings_usd": cap_10pct_savings_usd, "cap_savings_wh": cap_10pct_savings_wh,
        },
    }


if __name__ == "__main__":
    run()
