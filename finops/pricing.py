"""Pricing & purchasing economics — measure in $/1M-token, not $/GPU-hr.

Figures are June-2026 as-of snapshots from the deck's RESEARCH dossier; treat
live prices as fast-moving (re-baseline before each cohort).
"""
from __future__ import annotations


def request_cost(
    input_tok: int,
    output_tok: int,
    price_in_per_m: float,
    price_out_per_m: float,
    cached_in: int = 0,
    cache_discount: float = 0.10,   # Anthropic cached-read ~0.1x (=-90%)
    batch: bool = False,
    batch_discount: float = 0.50,   # Batch API ~ -50%
) -> float:
    """USD cost of a single request. Cached input billed at cache_discount x price."""
    cached_in = min(max(0, cached_in), input_tok)
    uncached_in = input_tok - cached_in
    cost = (
        (uncached_in / 1e6) * price_in_per_m
        + (cached_in / 1e6) * price_in_per_m * cache_discount
        + (output_tok / 1e6) * price_out_per_m
    )
    if batch:
        cost *= batch_discount
    return cost


def dollars_per_million(total_cost_usd: float, total_tokens: int) -> float:
    """Aggregate unit economics: $ per 1,000,000 tokens served."""
    if total_tokens <= 0:
        return 0.0
    return total_cost_usd / (total_tokens / 1e6)


def discount_stack(
    batch: bool = False,
    cache_hit_frac: float = 0.0,
    batch_discount: float = 0.50,
    cache_discount: float = 0.10,
) -> float:
    """Effective fraction of the naive bill after stacking discounts (input-heavy view).

    Discounts MULTIPLY: cache applies to the cached share of input, batch to the
    whole bill. batch + 100% cache-hit -> 0.5 * 0.1 = 0.05 (~95% off).
    """
    cache_mult = cache_hit_frac * cache_discount + (1.0 - cache_hit_frac)
    batch_mult = batch_discount if batch else 1.0
    return cache_mult * batch_mult


def break_even_utilization(discount_frac: float) -> float:
    """Utilization at which a commitment pays off ~= 1 - discount.

    A 45% reserved discount needs ~55% utilization (~13.2h/day) to beat on-demand.
    """
    return max(0.0, min(1.0, 1.0 - discount_frac))


GPU_INTERRUPTION_RATES = {
    "H100": 0.04,
    "H200": 0.04,
    "A100": 0.06,
    "A10G": 0.12,
    "L4": 0.08,
    "B200": 0.05,
    "MI300X": 0.05,
}


def break_even_cache_reads(
    write_cost_per_m: float,
    read_discount: float = 0.10,
    base_input_price_per_m: float = 3.00,
) -> float:
    """Calculate the minimum number of cache reads required to break even."""
    unit_savings = (1.0 - read_discount) * base_input_price_per_m
    if unit_savings <= 0:
        return float("inf")
    return write_cost_per_m / unit_savings


def cache_is_worth_it(
    avg_reads: float,
    write_cost_per_m: float,
    read_discount: float = 0.10,
    base_input_price_per_m: float = 3.00,
) -> bool:
    """Cache only saves money when cumulative savings from read discounts exceed write cost.

    Anthropic/OpenAI prompt cache charges for writing/storage. If a prompt prefix is
    read fewer times than the break-even threshold, caching increases the bill.
    """
    be_reads = break_even_cache_reads(write_cost_per_m, read_discount, base_input_price_per_m)
    return avg_reads >= be_reads


def recommend_tier(
    hours_per_day: float,
    interruptible: bool,
    reserved_discount: float = 0.45,
    gpu_type: str | None = None,
    job_days: int | None = None,
    interruption_rate: float | None = None,
) -> str:
    """Pick a purchasing tier from a workload's duty cycle + interruptibility + risk.

    Enhanced policy (deck §4, rubric D.1):
      - Considers interruption rate by GPU type (e.g. A10G is 12% vs H100 4%).
      - Checks duty cycle against break-even utilization (1 - discount).
      - If interruptible & not 24/7: evaluates spot feasibility based on interruption risk.
      - If duty cycle >= break-even: recommends 'reserved'.
      - Otherwise: recommends 'on_demand'.
    """
    duty = max(0.0, hours_per_day) / 24.0
    be = break_even_utilization(reserved_discount)

    if interruptible and hours_per_day < 24:
        rate = interruption_rate if interruption_rate is not None else GPU_INTERRUPTION_RATES.get(gpu_type or "", 0.05)
        # If preemption is extremely high (>20%), spot checkpointing rework may become prohibitive
        if rate > 0.20:
            return "reserved" if duty >= be else "on_demand"
        return "spot"

    if duty >= be:
        return "reserved"
    return "on_demand"


def recommend_tier_detailed(
    hours_per_day: float,
    interruptible: bool,
    gpu_type: str,
    job_days: int = 30,
    on_demand_hr: float = 2.50,
    spot_hr: float = 1.50,
    reserved_1yr_hr: float = 2.00,
    reserved_3yr_hr: float = 1.40,
) -> dict:
    """Compare all available tiers (On-Demand, Spot, Reserved 1yr, Reserved 3yr) with realistic economics."""
    duty = max(0.0, hours_per_day) / 24.0
    total_hours = hours_per_day * job_days
    od_cost = total_hours * on_demand_hr

    rate = GPU_INTERRUPTION_RATES.get(gpu_type, 0.05)
    spot_sim = spot_checkpoint_cost(total_hours, spot_hr, on_demand_hr, interrupt_rate=rate)

    res_1yr_cost = total_hours * reserved_1yr_hr
    res_3yr_cost = total_hours * reserved_3yr_hr

    options = {
        "on_demand": od_cost,
        "reserved_1yr": res_1yr_cost,
        "reserved_3yr": res_3yr_cost,
    }
    if interruptible and hours_per_day < 24:
        options["spot"] = spot_sim["spot_cost"]

    best_tier = min(options, key=options.get)
    return {
        "best_tier": best_tier,
        "duty_cycle": round(duty, 3),
        "options_cost": {k: round(v, 2) for k, v in options.items()},
        "interruption_rate": rate,
        "spot_sim": spot_sim,
    }


def spot_checkpoint_cost(
    job_hours: float,
    spot_hr: float,
    on_demand_hr: float,
    interrupt_rate: float = 0.05,      # per-hour chance (H100 spot ~<5%)
    ckpt_overhead_frac: float = 0.03,  # steady cost of writing checkpoints
    rework_hours_per_interrupt: float = 0.5,
) -> dict:
    """Effective cost of running a checkpointable job on spot vs on-demand.

    Interruptions waste the compute since the last checkpoint (rework); checkpointing
    adds a small steady overhead. Spot still wins for interruptible jobs.
    """
    expected_interrupts = job_hours * interrupt_rate
    rework_hours = expected_interrupts * rework_hours_per_interrupt
    effective_hours = job_hours * (1.0 + ckpt_overhead_frac) + rework_hours
    spot_cost = effective_hours * spot_hr
    on_demand_cost = job_hours * on_demand_hr
    savings_pct = (1.0 - spot_cost / on_demand_cost) * 100.0 if on_demand_cost > 0 else 0.0
    return {
        "spot_effective_hours": round(effective_hours, 2),
        "spot_cost": round(spot_cost, 2),
        "on_demand_cost": round(on_demand_cost, 2),
        "savings_pct": round(savings_pct, 1),
    }
