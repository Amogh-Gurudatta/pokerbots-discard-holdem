> **Historical document.** This is the initial plan written before implementation. The implemented design is in [strategy.md](strategy.md) and the outcome in [results.md](results.md); paths below predate the move of the bot into `bot/`.

# Plan: Discard Hold'em Pokerbot

## Context
We're entering a Pokerbots competition (submission deadline **15 Nov 2026, 23:59 IST**, ~2.5 days away). The starter kit in `Starter Kit/` has a trivial check/call `bot.py`, a local match runner `engine.py` (official rules + a reusable but slow hand evaluator), and `example_bots/random_bot.py`. The aim is a strong, **forfeit-proof** bot in a single `bot.py`, plus a running `memory.md` in the project root (`memory.md`) that records everything we learn.

Online research turned up nothing specific to this variant. The closest known games are **Crazy Pineapple** (3 hole cards, discard after the flop) and Irish Hold'em. The two lessons that carry over: a discard tells you about the opponent's range, and loose play beyond the flop gets punished. General bot practice: Monte Carlo equity combined with pot-odds/threshold rules, opponent modelling, and CFR/abstraction (too big for our timeline).

## Step 0: Create `memory.md` (first thing after approval)
Sections: Rules summary · Protocol cheatsheet · Limits and forfeit rules · Scoring implications · Environment notes · Strategy insights · Decisions log · TODO. Starting content, from the docs:
- **Game:** heads-up NLHE with 3 hole cards. After flop betting, both players discard one card at the same time and both discards are shown. At showdown it's best 5 of 2 hole cards + 5 board cards. Blinds 1/2, 200-chip stacks reset every hand (100 BB deep). At most 4 bets/raises per street (the BB counts preflop). Raise amounts are "to" totals. min_raise = opp_bet + max(last raise size, 2).
- The discard still happens when players are all-in. No action requests are sent on streets with no betting. A fold before flop betting ends means no discard.
- **Protocol:** JSON lines on stdin/stdout, flushed after each reply. Answer only `action_request` and `discard_request`. Ignore unknown messages and fields. Exit on `match_end`. **Never print to stdout except replies** (a stray print counts as the reply and gets corrected to check/fold). Use stderr for debugging.
- **Limits:** time bank 30 s per 200-hand leg (Stage 1), 75 s per 500-hand leg (Stage 2), so about **150 ms per hand**. 5 s per reply. 5 s free startup. Wall-clock kill at 90/225 s. 1 vCPU, 1 GB RAM, read-only FS. Single bot.py or a zip ≤ 5 MB. Stdlib + numpy + scipy only.
- **Forfeit** (crash, timeout, closing stdout) = ∆ −C (−400 or −1000) for the whole match. That is the worst outcome possible, so robustness comes first.
- **Scoring:** a match is won by net chips > 0 over both duplicate legs, and match points count first, so card luck mostly cancels and **edge over the specific opponent** decides the match. The tie-break is capped chips per hand, clip ±C. Swiss over 6–8 rounds, then the top 32 play a round robin with 1000 hands per match.
- **Conduct:** no trying to link the two duplicate legs, no carrying state across legs, no public bot repos (engine.py code is allowed).
- **Env:** the local machine has only Python 3.14, without numpy/scipy, and the target is 3.11. So: pure stdlib, no 3.12+ syntax. Run the engine with `python3 engine.py ...`.

## Step 1: Robust skeleton (`Starter Kit/bot.py`)
- Main loop modelled on the starter bot. Wrap every decision in try/except that falls back to a safe legal action (check > call-if-cheap > fold; discard = lowest card). Clamp every raise to [min_raise, max_raise] and make it an int. Read only `legal_actions`.
- Per-hand state from `new_hand`, `discard_result` and `hand_result`. A time budget from `time_left_ms`: cap compute per decision at min(remaining_bank / hands_left × 0.5, ~400 ms), and drop to cheap heuristics when the bank runs low.

## Step 2: Fast evaluator and equity engine
- Replace engine.py's 21-combination `rank5` with a fast 7-card evaluator: rank-count/bitmask based, returning one int. Precompute straight masks. Check it against `engine.best_hand` on random hands.
- Monte Carlo `equity(my2or3, board, opp_range_sampler, dead, n)`. Use n adaptive to the time left. Target ≥ 2–3k sims per decision within budget, and measure this.

## Step 3: Preflop table (offline, embedded)
- Group all 3-card hands into suit-isomorphic classes (~1.7k). Offline, compute each class's equity vs a random 3-card hand, with both players keeping their best 2 after the flop. Embed it as a compact dict or string in bot.py.
- Preflop policy: equity thresholds by position. Open-raise to ~2.5–3 BB, 3-bet with the top ~10%, call/fold by pot odds and equity vs the raiser's range. Jam/call ranges for big bets and all-ins.

## Step 4: Discard logic (the variant's core edge)
- For each of the 3 possible discards, estimate the equity of the 2 kept cards vs the opponent's range over turn and river. Keep the best pair. The discarded card becomes dead to us.
- After `discard_result`: **opponent range inference**. Remove their discard from the deck. Weight sampled opponent hands toward 2-card holdings they would rationally keep over the card they threw (sample their 3-card hand as {kept pair + revealed discard} and accept it only if the discard is plausibly their worst card on this flop). Also use our own exposed discard: a high card discarded shows we kept something strong.

## Step 5: Postflop betting
- Equity vs the action-weighted range. Raise for value with high equity (size 50–75% pot, larger vs calling stations). Call when equity > pot odds plus a margin. Make thin, limited bluffs only vs opponents who fold often. Respect the 4-raise cap and all-in rules.

## Step 6: Opponent modelling (within a leg)
- Track VPIP, PFR, aggression frequency, fold-to-bet, showdown hands vs actions, and average bet size. Classify opponents (calling station / maniac / nit / random) after about 20–30 hands and shift thresholds: no bluffing and big value bets vs stations, wider calls vs maniacs, more stealing vs nits. The likely field includes many weak or random bots, so exploiting them matters more than being balanced.

## Step 7: Test opponents and tuning
- Add sparring bots in `Starter Kit/example_bots/`: always-call, always-raise/jam, tight-passive, and an older snapshot of our own bot. Use several `--seed` values. Duplicate format means a few hundred hands already gives low-variance signal.

## Critical files (updated: the code now lives in `bot/`; see bot/README.md)
- `Starter Kit/bot.py` (the submission), new `memory.md` (project root), new sparring bots in `Starter Kit/example_bots/`, and an offline script for the preflop table (scratch, not submitted).
- Reuse: `rank5`/`best_hand` from `engine.py` as the correctness oracle, and the constants `RANKS`/`SUITS`.

## Verification
- `python3 engine.py bot.py example_bots/random_bot.py` and `--stage 2` should show **zero notices**, no forfeits, and time bank use well under the limit (check `logs/`). Run vs each sparring bot over several seeds and confirm consistently positive net chips. Check the evaluator against engine.py on 100k random hands. Do a final syntax check that avoids 3.12+ features (no `type` statements, no PEP 695 generics).
- Update `memory.md` after each step with results and decisions.
