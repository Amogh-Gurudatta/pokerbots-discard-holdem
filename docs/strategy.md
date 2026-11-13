# Strategy design

The implementation is `bot/src/strategy.py`. This document explains the ideas and why they are there.

## Design principle

The game is small enough for sampling and too large to solve. Within a leg of 200 to 500 hands the opponent never changes, so the bot **estimates its opponent as it plays** and picks the action with the highest expected value against that estimate. Every number below that is learned during a match comes from the current opponent only; nothing carries between legs or matches.

## Equity with a range model

All equity is Monte Carlo against a **weighted list of candidate opponent hands** (`RangeSampler`):

1. Candidates are sampled as full 3-card hands from the cards we cannot see.
2. Postflop, each candidate keeps the pair a sensible player would keep on that flop (`best_keep`).
3. After the discard, the candidate must be consistent with the opponent's **revealed discard** (a candidate is mostly rejected if it would not have thrown that card).
4. The list is shrunk to the top `keep_frac` by current made-hand strength. This is the "action-weighted" part: every opponent bet, raise or call narrows the range.

How much each action narrows the range is learned (`OppModel`):

- **Frequency prior.** How often this opponent bets when checked to and raises when facing our bet, per street. A frequent bettor shrinks the range little. A rare raiser shrinks it a lot. This avoids selection bias.
- **Showdown calibration.** When hands are shown down, we measure the percentile of the cards they showed on each street where they bet or called. That adjusts the prior, weighted by the number of samples seen.

Equity comparisons between candidate plays (for example the three possible discards) use **common random numbers**, so the same opponent hands and runouts are used for every option and noise cancels.

## Decisions

**Preflop.**
- Each 3-card hand has an equity from a precomputed table (1755 suit-equivalent classes against a random hand, both players keeping the best 2 after the flop). The button's open threshold adapts to how often the opponent folds to raises.
- Facing a raise, the bot compares the EV of fold, call, 3-bet to 3× and jam. Equity comes from Monte Carlo against the opponent's estimated preflop range, and against their *continuing* range for the raises. The all-in EV is exact.

**Discard.**
- Compare the three possible keeps by Monte Carlo equity against the modelled range, with common random numbers, and keep the best pair.

**Checked to us (bet or check).**
- For each size in {0.33, 0.66, 1.0, 1.5}× pot, EV = `f·pot + (1−f)·(e_called·(pot+2b) − b)`, where `f` is the opponent's measured fold-to-bet rate and `e_called` is our equity against the strongest `(1−f)` of their range. Checking is valued at our equity of the current pot, slightly discounted out of position. Value bets and bluffs both come out of this one formula.

**Facing a bet.**
- Call when equity (discounted on flop and turn for the chance of being outplayed later: 0.80 out of position, 0.90 in position) beats pot odds plus a small margin. Raise when the EV of raising, using a conservative fold-rate estimate, beats calling.

## Safety

The worst outcome is a forfeit, so the bot is defensive by construction:

- Every message handler is wrapped. On any exception, the bot sends a legal fallback (check, else a cheap call, else fold; discard the lowest card).
- Every reply is validated against `legal_actions` and the raise bounds before it is written.
- **Time budget.** Each decision gets about 12% of the per-hand share of the remaining bank, capped at 60 ms. If the bank is being spent faster than the hands are being played, the budget shrinks and the bot gets cheaper. With under 3 s left it uses a few milliseconds. Measured worst case: about 15 s of 30 s (Stage 1) and about 34 s of 75 s (Stage 2) per leg.
- Only protocol JSON is written to stdout. The build removes the dev-only environment hooks and refuses to build if `exec`, `os.environ`, `open`, `subprocess` or `socket` appear.

## Tunable parameters

Defaults live in the `P` dict at the top of `strategy.py`. In development they can be overridden without editing code with `BOTP='{"r_oop": 0.85}'` (see [testing.md](testing.md)). The build removes this override.

## Known limits

- The opponent model is single-opponent and starts from priors, so it is weakest in the first 20 to 30 hands.
- The fold-rate estimate does not depend on bet size. A version that scaled it with size beat our earlier builds but lost badly to size-blind bots, so it was not adopted (see [results.md](results.md)).
- Beyond roughly the current settings, more Monte Carlo trials or sampled hands did not improve results. The bottleneck is how well the opponent is modelled, not sampling noise.
