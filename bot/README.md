# Discard Hold'em bot

Our competition bot. The organisers' material is in `../Starter Kit/`, kept untouched. Rules, strategy and results are in `../docs/`.

**Submission file: `dist/bot.py`** (currently v4, identical to `opponents/v4_bot.py`). It is a single self-contained file built from `src/`. Upload that file. Never edit it by hand.

## Layout

| Path | What it is |
| --- | --- |
| `src/strategy.py` | Strategy core and protocol loop. **Edit this.** It imports the two modules below during development. |
| `src/fasteval.py` | Fast 5–7 card evaluator (`eval7`, card ints = rank·4 + suit). `python3 src/fasteval.py` runs the self-test against `engine.best_hand`. |
| `src/preflop_table.py` | Generated: equity of each of the 1755 suit-isomorphic 3-card hands vs a random hand. |
| `dist/bot.py` | Built submission. |
| `tools/build.py` | Inlines the evaluator and table into `strategy.py` → `dist/bot.py`, then checks Python 3.11 syntax. It also strips the evaluator's unused MC code (its `exec`) and the dev-only `BOTP`/`BOTDBG` env hooks, and refuses to build if `exec`/`os.environ`/`open`/`subprocess`/`socket` remain. `--snapshot vN` also freezes the build as `opponents/vN_bot.py`. |
| `tools/build_preflop.py` | Regenerates `src/preflop_table.py` (`python3 tools/build_preflop.py 4000`, about 40 s on 16 cores). |
| `tools/match.py` | One logged match + an analysis of how hands ended. |
| `tools/gauntlet.py` | Parallel multi-seed test vs many opponents, with a results ledger. |
| `tools/analyze.py` | Breakdown of a `match.log`: net by "who folded on which street", position, showdowns, and the worst hands. |
| `tools/paths.py` | Shared paths and bot-name resolution. |
| `experiments/` | Parked strategy variants that tested worse (e.g. `strategy_v5_elastic.py`, size-elastic fold model). Not used by the build. |
| `tools/sweep.py` | A/B several `BOTP` parameter variants on identical seeds vs a panel (`--variants '{"name":{...}}'`). |
| `tools/make_variants.py` | Writes randomized copies of the parameterized sparring bots (`opponents/_param/`) and EV-bot variants to `opponents/generated/` (git-ignored) with a `manifest.json`. |
| `tools/collect.py` | Plays each selector profile vs each generated variant and records opponent features plus chips/hand to `logs/collect/<ts>/data.jsonl`. |
| `tools/train_selector.py` | Fits the ridge-regression profile selector (pure Python) and reports CV, leave-one-family-out and holdout gains. It writes `src/selector_weights.py`, which the build inlines. The experiment found no signal, so the selector is off (`"selector": False`) and no weights file is committed. |
| `opponents/` | Sparring bots (see below) and frozen snapshots of our own versions (`v1_bot.py`, `v2_bot.py`, …). |
| `logs/` | All test output (see below). Safe to delete. |

Bot arguments in the tools accept `dev` (`src/strategy.py`), `dist` (`dist/bot.py`), a name from `opponents/` or the kit's `example_bots/` (`shark_bot`, `random_bot`, …), or a path.

## Workflow

```sh
cd bot
# 1. edit src/strategy.py, then quick A/B test the dev version (no build needed)
python3 tools/gauntlet.py dev --opps shark_bot,tight_bot,v3_bot --seeds 21,22,23,24,25,26,27,28 --hands 2000 --nolimits -j 16 --label my-change
# try parameter variants without editing code (keys of the P dict in strategy.py)
BOTP='{"r_oop":0.85}' python3 tools/gauntlet.py dev --opps shark_bot --hands 2000 --nolimits --label roop085
# 2. build the submission and check it under the real time limits
python3 tools/build.py                      # add --snapshot v4 to freeze a version as a sparring opponent
python3 tools/gauntlet.py dist --seeds 1,2,3,4 -j 12             # stage-1 limits, every opponent
python3 tools/match.py dist call_bot --stage 2 --seed 3           # stage-2 timing (longest hands)
# 3. dig into one match
python3 tools/match.py dev shark_bot --seed 14 --hands 2000 --nolimits
BOTDBG=1 python3 tools/match.py dev riverbluff_bot --hands 400 --nolimits   # river debug lines in the A_leg*_stderr.txt files
```

Run everything with `python3.11` and pass `--python python3.11` (the server runs 3.11).

**Noise:** a 400-hand match swings by about ±400 chips, so don't tune on it. To compare variants, use `--hands 2000 --nolimits` over 8 seeds, compare chips/hand, and run both variants on the **same seeds**.

## Logs

- `logs/results.csv`: an append-only ledger with one row per match from every gauntlet run (timestamp, label, bot, BOTP, opponent, seed, hands, stage, net, chips/hand, time, notices, flag). Use it to compare versions over time.
- `logs/gauntlet/<timestamp>_<label>/summary.txt`: the results table, command and BOTP for each gauntlet run. `--save-logs` also keeps each match's engine logs there.
- `logs/matches/<timestamp>_<A>_vs_<B>_s<seed>/`: from `tools/match.py`. Contains the engine `match.log`, each bot's stderr per leg, `summary.txt` and `analysis.txt`.

## Sparring opponents (`opponents/`)

| Bot | Style |
| --- | --- |
| `call_bot` | Calling station: checks or calls everything. |
| `jam_bot` | Maniac: all-in whenever it can. |
| `tight_bot` | Tight-passive: plays pairs and high cards, bets pair+, folds otherwise. |
| `aggro_bot` | Loose-aggressive heuristic. |
| `shark_bot` | Competent equity-based TAG/LAG with balanced bluffs and check-raises. Written independently of our code; the main "real opponent" benchmark. |
| `checkraise_bot` | Checks first, then check-raises about 45% of the time. |
| `riverbluff_bot` | Calls down, then bets pot on 80% of rivers. |
| `minraise_bot` | Min-raises 70% of the time. |
| `potlimp_bot` | Limps, then bets pot with pair+. |
| `vN_bot` | Frozen builds of our own bot. |

`../Starter Kit/example_bots/random_bot.py` (organisers) is also available as `random_bot`.

## How the strategy works (short)

- **Preflop:** a table lookup for the hand's percentile. The button open threshold adapts to the opponent's fold-to-raise rate. Facing a raise, we compare the EV of fold, call, 3-bet to 3x and jam. Equity comes from Monte Carlo against the opponent's estimated preflop range, and against its continuing range for the raises. The jam EV is exact.
- **Equity:** Monte Carlo against an **action-weighted range** (`RangeSampler`). We sample the opponent's 3-card hands, keep the pair their flop heuristic would keep, and after the discard require consistency with their revealed discard. Then we keep the top `keep_frac` by made strength. `keep_frac` shrinks with each opponent bet, raise or call, using:
  - their measured per-street bet/raise frequency (a prior with no selection bias), and
  - showdown calibration: the percentile their shown hands had when they bet or called.
- **Discard:** common-random-number Monte Carlo over the 3 keep options.
- **Checked to us:** an EV comparison of check vs bet sizes {0.33, 0.66, 1.0, 1.5}·pot. It uses the opponent's fold-to-bet rate and our equity against their calling range (the top 1−f), so value bets and bluffs come out of one formula.
- **Facing a bet:** call if the realization-discounted equity beats pot odds. Raise when EV(raise), using a fold rate of 0.7 × fold-to-bet and equity vs the continuing range, beats calling.
- **Safety:** every handler is wrapped and falls back to a legal action. Raises are clamped. The time budget per decision is about 0.12 × (time left / hands left), capped at 60 ms, with a cheap mode when the bank runs low.
