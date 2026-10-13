"""Unit tests for Lab 25 Extensions (Rubric D.1, D.2, D.3, D.4, D.5)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from finops import pricing, sustainability


def test_cache_is_worth_it_logic():
    # Break-even reads with 10% read discount and 1.25x write cost
    be_large = pricing.break_even_cache_reads(write_cost_per_m=3.75, read_discount=0.10, base_input_price_per_m=3.00)
    assert abs(be_large - 1.388888) < 1e-4

    # 1 read is not enough (lose money)
    assert pricing.cache_is_worth_it(avg_reads=1.0, write_cost_per_m=3.75, read_discount=0.10, base_input_price_per_m=3.00) is False

    # 2 or more reads is profitable
    assert pricing.cache_is_worth_it(avg_reads=2.0, write_cost_per_m=3.75, read_discount=0.10, base_input_price_per_m=3.00) is True
    assert pricing.cache_is_worth_it(avg_reads=3.5, write_cost_per_m=3.75, read_discount=0.10, base_input_price_per_m=3.00) is True


def test_break_even_cache_reads_edge_case():
    # Zero or negative savings
    assert pricing.break_even_cache_reads(write_cost_per_m=1.0, read_discount=1.0, base_input_price_per_m=3.0) == float("inf")


def test_enhanced_recommend_tier_compatibility():
    # Must preserve baseline logic when called without extra kwargs
    assert pricing.recommend_tier(2, True) == "spot"
    assert pricing.recommend_tier(24, False) == "reserved"
    assert pricing.recommend_tier(4, False) == "on_demand"

    # Extreme interruption rate (> 20%) should avoid spot
    assert pricing.recommend_tier(2, True, interruption_rate=0.25) == "on_demand"
    assert pricing.recommend_tier(20, True, interruption_rate=0.25) == "reserved"


def test_recommend_tier_detailed():
    details = pricing.recommend_tier_detailed(
        hours_per_day=24, interruptible=False, gpu_type="A100", job_days=30,
        on_demand_hr=1.79, spot_hr=1.10, reserved_1yr_hr=1.40, reserved_3yr_hr=1.00
    )
    assert details["best_tier"] == "reserved_3yr"
    assert details["options_cost"]["reserved_3yr"] < details["options_cost"]["on_demand"]
    assert "spot" not in details["options_cost"]  # non-interruptible 24h cannot be spot


def test_carbon_and_energy_calculations():
    # Reasoning multiplier check
    tokens = 1000
    wh_standard = sustainability.wh_per_query(tokens, is_reasoning=False)
    wh_reasoning = sustainability.wh_per_query(tokens, is_reasoning=True)
    assert abs(wh_reasoning / wh_standard - 80.0) < 1e-5

    # Carbon in cleanest region europe-north1 vs dirtiest europe-central2
    carb_clean = sustainability.carbon_g(wh_standard, region="europe-north1")
    carb_dirty = sustainability.carbon_g(wh_standard, region="europe-central2")
    assert carb_dirty > carb_clean * 20  # 660 vs 30 gCO2/kWh
