# Pokerbots — Running Memory

_Last updated: 2026-11-13. **Deadline: 15 Nov 2026, 23:59 IST** (submit on Unstop; last valid upload counts)._

## Rules summary (Discard Hold'em)
- Heads-up no-limit Hold'em, but each player gets **3 hole cards**.
- After **flop betting**, if nobody folded, both players **discard 1 card simultaneously**. Both discards are revealed and removed from play. Each player then has 2 hole cards.
- The discard happens **even when players are all-in**. A fold before flop betting ends means no discard.
- Showdown: best 5 of (2 hole cards + 5 board cards). Playing the board is allowed. A–5 is the lowest straight, straights don't wrap, and suits never break ties.
- Blinds 1/2. **200-chip stacks reset every hand** (100 BB). The button posts the SB and acts first preflop and last postflop. Seats alternate every hand.
- The board (5 cards) and all hole cards are fixed before any action. Discards don't change the board.
- At most 4 bets/raises per street (the BB counts as the first preflop, so at most 3 voluntary raises preflop).
- Raise amounts are **"to" totals for the street**. `min_raise = opp_bet + max(s, 2)`, where s is the last raise size. The first preflop min-raise is to 4.
- max_raise is the all-in amount, which is always the same for both players. If all-in < min, then min_raise = max_raise. Facing an all-in you can only call or fold.
- Net chips: the winner takes the loser's commitment. A split pot is 0.

## Protocol cheatsheet
- JSON per line. stdin is engine→bot, stdout is bot→engine. **Flush every reply.**
- Engine messages: `init`, `new_hand`, `action_request`, `discard_request`, `discard_result`, `hand_result`, `notice`, `match_end`.
- Reply only to `action_request` → `{"type":"action","action":"fold|check|call|raise","amount":N}` and to `discard_request` → `{"type":"discard","card":"Kd"}`.
- action_request fields: street, board, pot, my_bet, opp_bet, to_call, my_stack, opp_stack, legal_actions, min_raise/max_raise (present only if raise is legal), raises_left, history (voluntary actions, actor "me"/"opp"), time_left_ms.
- `raise` can be missing even with raises_left > 0 (when the opponent is all-in). **Always choose from legal_actions.**
- hand_result gives result, net, board, my_hole_cards, opp_hole_cards (showdown only), opp_discard, and the full history. This is useful for opponent modelling.
- **A stray stdout line is taken as the reply** (malformed, so it becomes check/fold). Debug output goes to stderr only (64 KB cap).
- Ignore unknown types and fields. **Exit after `match_end`.**
- Corrections: an illegal action becomes check if legal, else fold. An illegal discard becomes the lowest rank (suit order s,h,d,c).

## Limits and forfeits
- Time bank: **30 s per leg (200 hands)** in Stage 1 and **75 s per leg (500 hands)** in Stage 2, so about 150 ms per hand total.
- 5 s max per reply. 5 s free startup per leg. Wall-clock kill at 90 s / 225 s per leg.
- Only time from request to reply is charged. Both discard clocks run in parallel.
- 1 vCPU, 1 GB RAM, no network, read-only FS, no subprocesses, no file writes.
- Submission is a single `bot.py` or a zip with bot.py at top level, ≤ 5 MB. Python 3.11, stdlib + numpy + scipy only.
- **A forfeit (crash, timeout, EOF on stdout) = ∆ −C for the whole match** (C = 400 in S1, 1000 in S2). That is worse than almost any chip loss, so **robustness comes first**.

## Scoring and tournament
- A match is 2 legs with a fresh process per leg. Leg 2 replays the same deals with hole cards and seats swapped (duplicate format, so card luck mostly cancels).
- Win = more net chips over both legs → 1 point; draw 0.5; loss 0. **Match points are primary**, so consistently winning by a little is what counts.
- Tie-break: Σ clip(∆, −C, C) / Σ hands.
- Stage 1 is Swiss over 6 rounds (7–8 if > 1000 teams; round robin if < 64 teams; skipped if ≤ 32 teams). Top 32 → Stage 2: full round robin with 1000-hand matches, and scores reset.
- Prizes: Rs 20k / 14k / 8k.

## Conduct
- Our own work only. Code from the organisers' kit (engine.py) is allowed. Published algorithms are fine, but no copying from public bot repos.
- No trying to identify duplicate deals or to carry info between legs. No collusion.

## Environment notes
- Local machine: **Python 3.14 only, without numpy/scipy, and no python3.11**. Plan: write pure-stdlib code with no 3.12+ syntax, and run with `python3 engine.py bot.py example_bots/random_bot.py`.
- engine.py has `rank5`/`best_hand`, which are correct but slow (21 five-card combos per evaluation). Use them as the correctness oracle.

## Strategy insights
- Closest known variant: **Crazy Pineapple** (3 cards, discard after the flop). Lessons: don't over-loosen preflop because 3 cards feel strong, and be careful past the flop.
- The **exposed discard is information**. The opponent's discard is dead, and they presumably kept something better than it on that flop. Weight their range accordingly. Our discard leaks info too.
- In a duplicate format, the edge against each specific opponent decides the match, so **exploitative opponent modelling** has high value (the field likely includes many naive bots: random, call-stations, jammers).

## Decisions log
- 2026-11-13: Pure-stdlib bot, using Monte Carlo equity + heuristic betting + opponent modelling. No CFR (not enough time). Plan in `docs/original-plan.md`.

## Code layout (moved 2026-11-13 at the user's request: the bot lives in its own folder)
- `Starter Kit/` is **kept pristine** (organisers' engine.py, original bot.py, random_bot, PDFs). The tools call `Starter Kit/engine.py` by path.
- `bot/` holds everything of ours. **See `bot/README.md`** for the layout, workflow, logs and opponents; keep that README up to date.
  - `bot/src/strategy.py` (strategy core; edit this), `bot/src/fasteval.py`, `bot/src/preflop_table.py`
  - `bot/dist/bot.py` = **the submission** (built by `bot/tools/build.py`; never hand-edit)
  - `bot/tools/` build, build_preflop, gauntlet, match, analyze, paths
  - `bot/opponents/` sparring bots + frozen snapshots v1/v2/v3
  - `bot/logs/` results.csv ledger, gauntlet/<run>/summary.txt, matches/<run>/
- Strategy design: MC equity vs an **action-weighted range**. RangeSampler keeps the top keep_frac of opp candidates by made-hand strength. It uses the opp discard (sampled 3-card hands must be consistent with it 75% of the time) and **common random numbers** to compare the 3 keep options.

## Results log
- 2026-11-13 v1 (`bot/opponents/v1_bot.py` snapshot), 4 seeds × 400 hands: beats random/call/jam/aggro in 4/4 seeds by thousands of chips. Initially lost to tight_bot (−123). A **showdown-calibrated range model** (`OppModel.learn_showdown` → `frac_for`) made it 3/4 wins, +815 total.
- Timing: about 7 s per 200-hand leg vs call_bot (the longest hands), with 30 s allowed. The per-decision budget is 0.22 × time_left/hands_left, and candidate counts scale with the budget. Startup 0.16 s.
- Fuzz test (garbage, unknown types, missing fields) gives no crash and safe fallbacks.

- vs shark_bot (Sonnet-written TAG/LAG with balanced bluffs, a different design from ours) and tight_bot, 8 seeds × 400 hands: roughly break-even (3/8–6/8 wins; the **same config swung 3/8→6/8 between runs**). **400-hand matches are too noisy (±~400 chips) to tune on.** Use `--hands 2000 --nolimits` × 8 seeds and compare chips/hand.
- Leak found with `tools/analyze.py`: **call flop/turn, then fold river** (−20 chips per hand over 36 hands vs shark). Testing a realization discount `r_oop`/`r_ip` on non-river calls.
- Real-match variance is dominated by 200-chip all-in coolers (flush vs flush, boat vs boat).

- **v2 (2026-11-13), the big win: EV-based bet/check** (`Bot.bet_or_check`). For sizes {0.4, 0.66, 1.0}·pot, EV = f·pot + (1−f)(e_called·(pot+2b) − b), compared with check = e·pot·(1.0 IP / 0.92 OOP). f is the opp's fold-to-bet rate and e_called is our equity vs the top (1−f) of its range. Also: **adaptive button open range** from the opp's preflop fold rate, and **realization discount** r_oop 0.80 / r_ip 0.90 on flop/turn calls.
  - 2000 hands × 8 seeds, no limits: vs shark +0.85/hand (8/8), vs tight +0.92/hand (8/8); before this it was +0.12 / +0.14.
  - Under stage-2 limits vs shark: +0.78/hand (8/8). v2 beats v1 by +2.47/hand.
- Time: `time_frac` 0.12 of per-hand bank per decision, 60 ms cap, cheap mode if spending too fast. Solo vs call_bot about 9 s per 200-hand leg (30 s bank).

- **v3 (2026-11-13): EV-based raising** facing bets (`raise_ev`; fold rate 0.7 × fold-to-bet; equity vs the continuing range). Plus **frequency-based range priors**: the opp's measured per-street bet frequency (when checked to) and raise frequency (facing our bet) give keep_frac without selection bias, refined by showdown calibration (weight n/(n+3), last 40 samples).
  - EV raise: tight +0.92 → +1.59/hand, v2 head-to-head +0.22/hand.
  - Freq priors fixed river overfolding vs riverbluff_bot (+0.57 → +1.25/hand). v3 vs v2: +0.86/hand (6/8). Seeds 41–48: shark +0.60, tight +1.44.
- **Python 3.11.18 at /usr/bin/python3.11** (no numpy; we don't need it). The fasteval self-test passes under 3.11 (0 mismatches, ~2.1M eval7/s). Timing under 3.11 vs call_bot (worst case): **~10.4 s of 30 s per leg (stage 1) and ~32.6 s of 75 s (stage 2)**. Run all tests with `python3.11` and `--python python3.11`.
- numpy/scipy: allowed, and could make MC maybe 5–10× faster via vectorized eval. But MC noise at 3000 trials is already about ±1% equity, and range-model error dominates. **First measure whether more compute helps** (sweep `cmul`/`nmul`) before rewriting. A venv is OK if we need pip packages locally.
- **Lesson:** gauntlet/sweep runs of `dev` must not read the live src (edits mid-run contaminate the results). gauntlet now copies src/ to `<run>/src_snapshot/`.
- v4 work: **EV-based preflop facing-raise** (fold/call/3x/jam, where the jam EV is exact). P keys pf_ev_raise, pf_fr_mult, pf_real_bb/btn. `tools/sweep.py` A/Bs BOTP variants on the same seeds vs the panel (shark, tight, riverbluff, checkraise, v3).

- **Sweeps (2026-11-13, 2000 hands × 8 seeds, py3.11, chips/hand):**
  - 3× compute (cmul 3, nmul 2) is **not better** (mean +4.47 vs +4.54). Compute is not the bottleneck, so numpy is not worth it and using more of the time bank won't help.
  - EV preflop with pf_real 0.92 lost to v3 (−0.44). **pf_real 1.0** gives shark +1.37 (vs +1.01), v3 −0.07, so it was adopted.
  - Broad sweep, mean over shark/tight/riverbluff/v3: base +1.17. **overbet (bet_sizes 0.33/0.66/1.0/1.5) +1.54** (best). chk_real_oop 1.0 +1.09, r 0.72/0.85 +1.05, fr_mult 0.5 +1.20 (but v3 −0.70), raise_edge 0 +1.08, open_thr 0.25 +1.21. Only overbet is a candidate to adopt.
  - **Head-to-head vs v3 noise is about ±0.4/hand even at 16k hands**, so judge mainly on the non-self panel and treat v3 as a guard against regressions.
- New sparring bots: minraise_bot (min-raises 70% of the time), potlimp_bot (limps, then bets pot with pair+).

- **v4 (2026-11-13) = pf_ev + pf_real 1.0 + overbet sizes [0.33, 0.66, 1.0, 1.5]**, snapshot `opponents/v4_bot.py`, built to dist.
  - Fresh seeds 81–88, overbet vs base: shark +1.92/+1.68, tight +1.96/+1.56, riverbluff +1.71/+1.11, weak bots +0.8 to +1.6 better, v3 −0.01/+0.10.
  - Real format (400 hands, stage-1 limits, py3.11, 24 seeds): **vs shark 21/24 wins, +2.42/hand; vs v3 11/24, −0.28/hand**. Worst-case time about 15 s per leg (call_bot). 0 notices.
  - Hypothesis: the overbet hurts vs EV-based bots because the bet EV assumed a **size-independent fold rate**. Testing `f_alpha`: f(b) = f0·(req(b)/req(0.66))^α, with a per-candidate MC (`mc_by_cand`/`top_equity`) giving equity vs the calling range for each size.

- **Size-elastic fold model (2026-11-13), not adopted:**
  - α=1 (fold rate ∝ required equity of the size) beats v4 by +1.01/hand, but is a disaster vs size-blind bots (tight −4.11, riverbluff −2.63).
  - Per-size-bucket learning was **confounded**: we overbet in spots where the opp is already weak, so the inflated fold rates lead to over-bluffing.
  - α=0.5 prior only: real-format validation vs v4 −0.31, v3 −0.55. **Reverted to v4.** The code is parked in `bot/experiments/strategy_v5_elastic.py`.
- **SUBMISSION = v4** (`bot/dist/bot.py` == `bot/opponents/v4_bot.py`, rebuilt from src byte-identical).
  - Py3.11 checks: stage 1 at most ~15 s per leg; stage 2 at most ~33.5 s/75 s per leg (call_bot, checkraise_bot); fuzz-clean; 0 notices.
- Takeaway on tuning: after v4, every tweak tested was within the ±0.3–0.4/hand noise or worse. Remaining gains are small next to the jumps from EV betting and freq priors. More compute and numpy don't help (tested).

- **Pre-submission compliance check (2026-11-13), all rules re-checked against the PDFs:** see the table in my report. The build now **strips fasteval's unused MC code (the only `exec`) and the dev env hooks (BOTP/BOTDBG)**, and asserts that no exec/os.environ/open/subprocess/socket remains. dist/bot.py is 72 KB and strategy-identical to v4. Full gauntlet: 0 notices, 0 stderr bytes. Startup→exit 0.22 s, about 23 MB RSS.

## Build / test commands
See `bot/README.md` (run from `bot/`): `python3 tools/build.py [--snapshot vN]`, `python3 tools/gauntlet.py dev|dist ...`, `python3 tools/match.py A B ...`, `BOTP='{...}'` to try parameter variants and `BOTDBG=1` for debug lines on stderr.

## TODO
- [x] Step 1: robust skeleton  - [x] Step 2: fast evaluator + MC  - [x] Step 3: preflop table
- [x] Step 4: discard logic + range inference (v1)  - [x] Step 5: postflop betting (v1; tune)
- [x] Step 6: opponent modelling (fold rates, per-street freqs, showdown calibration)  - [x] Step 7: sparring bots (8 + snapshots)
- [x] Verify under real Python 3.11 (timing + syntax): done for v4
- [ ] More tuning ideas: preflop 3-bet/defend EV, river-specific bluff frequencies, check-raise as BB, bet-size tells
