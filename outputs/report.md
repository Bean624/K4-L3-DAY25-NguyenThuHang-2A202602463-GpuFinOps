# NimbusAI — GPU Cost Optimization Report

**Period:** monthly  
**Baseline spend:** $27,133  
**Optimized spend:** $14,626  
**Projected savings:** $12,507  (**46%**)

## Savings by lever

| Lever | Savings (USD) |
|---|---|
| Inference (cascade/cache/batch) | $1,212 |
| Purchasing (spot/reserved) | $10,040 |
| Right-size util-lies | $655 |
| Kill idle GPUs | $600 |

## Sustainability

- Energy per query: 0.24 Wh
- Carbon per query: 0.091 gCO2e
- Cheapest+cleanest region: europe-north1

## Deep Engineering Analysis & Insights

### 1. GPU-Util "Lie": Root Cause & Financial Leakage
- **Measurement Reality**: `nvidia-smi` reports GPU Utilization based on the fraction of time a kernel is actively executing on the GPU core clock, NOT the computational throughput or mathematical work (FLOPs).
- **Audit Findings**: `gpu-h100-4` reports **98.2% GPU-Util** but its MFU is only **0.194 (19.4%)**, while `gpu-a10g-1` shows **96.9% GPU-Util** with an MFU of **0.268 (26.8%)**.
- **Underlying Mechanism**: This discrepancy is caused by memory stalls (HBM bandwidth bottlenecks during autoregressive token decode), kernel launch overhead for small/uncoalesced operations, and pipeline bubbles/all-reduce communication synchronization in distributed setups. NimbusAI pays 100% of the hardware rental fee ($2.50/hr for H100) while obtaining only ~1/5 of the rented compute capacity.

### 2. Prioritized Action Plan (Ranked by ROI)
1. **P1 — Purchasing Commitments & Spot Strategy ($10,040/month - 80.3% of total savings)**:
   - *Action*: Transition all 5 interruptible training and batch jobs to **Spot Instances** with automated checkpointing; secure **Reserved 3yr** commitments for steady-state 24/7 inference workloads (`job-infer-chat`, `job-infer-rag`, `job-infer-search`) exceeding the 55% break-even utilization threshold.
   - *Rationale*: Largest absolute dollar impact with zero degradation to model latency or quality.
2. **P2 — Inference Serving Levers ($1,212/month - unit economics drop from $6.488 to $1.126/1M-token)**:
   - *Action*: Deploy **Cascade routing** (directing 79.3% of simpler traffic to small model tier), enable **Prompt Caching** (90% discount on repeated prompt prefixes), and leverage **Batch API** (50% discount on non-real-time workloads).
   - *Rationale*: Slashes unit serving cost by 82.6%, ensuring sustainable unit economics as query volume scales.
3. **P3 — Right-sizing & MBU Optimization ($655/month)**:
   - *Action*: Downgrade `gpu-h100-4` to A100 (saving $511/mo) and `gpu-a10g-1` to L4 (saving $144/mo) because achieved memory bandwidth and TFLOPs easily fit in lower-tier GPUs with better $/GB-VRAM.
4. **P4 — Terminate Idle Resources ($600/month)**:
   - *Action*: Implement automated lifecycle policies to terminate instances with utilization < 10% sustained over 1 hour (such as `gpu-h100-5`).

### 3. Sustainability & Regional Carbon-Aware Optimization
- **Energy Concentration**: Reasoning traffic accounts for only 8.4% of queries but consumes **94.0% of total inference energy** due to multi-step recursive reasoning loops (~80x energy multiplier). Enforcing a 10% traffic cap prevents sudden power and cost surges.
- **Regional Placement**:
   - *Cleanest Region*: **europe-north1** (30 gCO2/kWh, cuts carbon by **92.1%**).
   - *Cheapest Power*: **us-east-wa** ($0.055/kWh, cuts electricity costs by **54.2%** and carbon by **76.3%**).
   - *Zero-Latency Tradeoff*: Because interruptible training jobs are asynchronous, migrating them to clean hydro regions incurs zero latency penalty on production users.

_Figures are June-2026 as-of snapshots; re-baseline before acting._