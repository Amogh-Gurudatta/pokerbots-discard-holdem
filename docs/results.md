# Results and experiment history

Numbers are chips per hand, from matches against the organisers' engine on Python 3.11 unless noted. "Noise" means the difference is within the roughly ±0.3 to 0.4 per hand uncertainty described in [testing.md](testing.md). The full raw ledger is `bot/logs/results.csv`.

## Version history

| Version | What was added | Evidence |
|---|---|---|
| **v1** | Fast evaluator, preflop table, Monte Carlo equity against a weighted range, discard choice, threshold betting | Beat random, call, jam and aggro bots by thousands of chips; lost slightly to `tight_bot` |
| v1 + calibration | Range shrinkage calibrated from showdown hands | `tight_bot`: lost −123 → won +815 over 4 matches |
| **v2** | **EV-based bet or check** using the opponent's fold rate; adaptive button opens; discount for calling out of position | vs `shark_bot` +0.12 → +0.85; vs `tight_bot` +0.14 → +0.92; beats v1 by +2.5 |
| **v3** | EV-based raising facing a bet; per-street bet-frequency priors for the range model | vs `tight_bot` +0.92 → +1.59; vs `riverbluff_bot` +0.57 → +1.25 (the bot was over-folding rivers); beats v2 by +0.86 |
| **v4** | EV-based preflop decisions facing a raise (fold, call, 3-bet, jam); a 1.5× pot bet size | vs `shark_bot` +1.80 (8/8 seeds); in the real format, 21 of 24 matches won at +2.42; head-to-head with v3 about even |

Submission: **v4** (`bot/dist/bot.py`, identical to `bot/opponents/v4_bot.py`).

## What did not work

| Experiment | Outcome |
|---|---|
| **3× Monte Carlo trials and 2× sampled hands** | No improvement (panel mean +4.47 vs +4.54). The bottleneck is the opponent model, not sampling noise. So numpy and spending more of the time bank would not help. |
| Preflop call realization 0.92 (call less because of position) | Worse vs v3 (−0.44 vs −0.07 without it) |
| Check realized fully out of position | Panel mean +1.09 vs +1.17 (noise or worse) |
| Lower flop/turn call realization (0.72 / 0.85) | +1.05 vs +1.17, and lost to v3 |
| Fold-to-raise estimate ×0.5 | +1.20 vs +1.17 on the panel, but −0.70 vs v3 |
| No margin before raising | +1.08 vs +1.17 |
| Wider button opens (threshold 0.25) | +1.21 vs +1.17 (noise) |
| **Fold rate that scales with bet size** (α = 1) | +1.01 better than v4 head-to-head, but **−4.1 vs `tight_bot` and −2.6 vs `riverbluff_bot`**, which fold or call regardless of size |
| Learning the fold rate per bet-size bucket | Confounded: we overbet in spots where the opponent is already weak, so the learned rates were inflated and the bot over-bluffed. Both versions were worse in real-format validation (v4 −0.31, v3 −0.55 head-to-head). Code kept in `bot/experiments/strategy_v5_elastic.py` |

## Lessons

1. **Structural changes mattered; parameter changes mostly did not.** The jumps came from EV-based betting and from the unbiased frequency priors. After v4, nearly every tweak was inside the noise or worse.
2. **Test against independent opponents.** An equal-strength opponent written by someone else (`shark_bot`) exposed leaks that simple bots hid. Head-to-head against our own builds was too noisy to guide decisions.
3. **Tools that learn from their own actions can mislead themselves.** The per-size fold learning failed because the sample depended on when the bot chose to bet.
4. **Freeze the code under test.** An early sweep was contaminated by edits made while it ran. The tools now copy the source per run.
5. **Variance is dominated by 200-chip all-ins.** Short matches say very little.

## Timing and safety measurements (v4, Python 3.11)

| Measurement | Value |
|---|---|
| Worst case, Stage 1 (400 hands) | about 15 s of 30 s per leg |
| Worst case, Stage 2 (1000 hands) | about 34 s of 75 s per leg |
| Startup to exit | 0.22 s |
| Memory | about 23 MB |
| Engine corrections (illegal action or discard) | 0 in every test |
| Forfeits | 0 |
