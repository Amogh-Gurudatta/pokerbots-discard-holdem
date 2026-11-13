# Testing guide

All commands run from `bot/` and use Python 3.11 (the version the competition server runs). The tools call the organisers' `Starter Kit/engine.py`, so the rules enforced are the official ones.

Bots are named by a path, an alias (`dev` = `src/strategy.py`, `dist` = `dist/bot.py`), or a file name in `opponents/` or the kit's `example_bots/`.

## Tools

| Tool | Use |
|---|---|
| `tools/build.py [--snapshot vN]` | Inline the evaluator and table into one file `dist/bot.py`. Checks 3.11 syntax, strips dev hooks, refuses to build if forbidden calls remain. `--snapshot` also freezes the build as `opponents/vN_bot.py`. |
| `tools/match.py A B [--seed S] [--hands N] [--stage 1\|2] [--nolimits]` | Play one match with full logs, and print a breakdown of how A's chips were won and lost. |
| `tools/gauntlet.py BOT [--opps a,b] [--seeds 1,2,3] [--hands N] [-j N]` | Many opponents × many seeds in parallel, one table. Appends to `logs/results.csv`. |
| `tools/sweep.py --variants '{"name": {...}}'` | A/B parameter variants of the dev bot on identical seeds against a fixed opponent panel. |
| `tools/analyze.py PATH` | Breakdown of a `match.log`: net chips by how hands ended, position, and the worst hands. |
| `tools/build_preflop.py [trials]` | Regenerate `src/preflop_table.py` (about 40 s with 4000 trials on 16 cores). |

## Workflow

```sh
# 1. edit src/strategy.py, then A/B the dev version without building
BOTP='{"r_oop":0.85}' python3.11 tools/gauntlet.py dev --python python3.11 \
    --opps shark_bot,tight_bot,v4_bot --seeds 21,22,23,24,25,26,27,28 \
    --hands 2000 --nolimits -j 16 --label roop085

# 2. compare several variants on identical seeds
python3.11 tools/sweep.py --python python3.11 --variants '{"base":{}, "bigger":{"bet_sizes":[0.33,0.66,1.0,1.5]}}'

# 3. build, then validate the real thing under the real time limits
python3.11 tools/build.py --snapshot v5
python3.11 tools/gauntlet.py dist --python python3.11 --seeds 1,2,3,4 -j 8
python3.11 tools/match.py dist call_bot --stage 2 --seed 3 --python python3.11   # slowest case

# 4. investigate one match
python3.11 tools/match.py dev shark_bot --seed 14 --hands 2000 --nolimits --python python3.11
```

`BOTDBG=1` prints debug lines (river decisions, opponent statistics) to the bot's stderr file. Both `BOTP` and `BOTDBG` exist only in the dev version, not in `dist/bot.py`.

## Noise: read this before tuning

- A **400-hand match swings by roughly ±400 chips** from 200-chip all-ins alone. The same configuration went from 3/8 to 6/8 wins between two runs.
- Compare variants with `--hands 2000 --nolimits` over **8 seeds**, on the **same seeds**, by chips per hand.
- Head-to-head against our own earlier version stayed noisy (about ±0.3 to 0.4 per hand) even at 16 000 hands, so use it as a regression guard and judge improvements mainly against the independent bots.
- `--nolimits` removes the clocks, so it is for strength comparison only. Always validate timing with the real limits.
- `gauntlet.py` copies `src/` into the run folder and plays that copy, so edits made while a run is in progress cannot leak into it.

## Logs

- `logs/results.csv` (**committed**): one row per match: timestamp, label, bot, parameter overrides, opponent, seed, hands, stage, net, chips per hand, time used, notices, flags.
- `logs/gauntlet/<time>_<label>/`: `summary.txt` (the table, command and parameters) and `src_snapshot/` (the exact source played). Git-ignored.
- `logs/matches/<time>_<A>_vs_<B>_s<seed>/`: engine log, each bot's stderr per leg, `summary.txt` and `analysis.txt`. Git-ignored.

## Sparring opponents

| Bot | Style |
|---|---|
| `random_bot` (kit) | Random legal actions |
| `call_bot` | Checks or calls everything |
| `jam_bot` | All-in whenever possible |
| `tight_bot` | Tight-passive: plays pairs and high cards |
| `aggro_bot` | Loose-aggressive heuristic |
| `shark_bot` | Equity-based, balanced bluffs and check-raises; written independently of our code, so it is the main realistic benchmark |
| `checkraise_bot` | Checks first, then check-raises about 45% of the time |
| `riverbluff_bot` | Calls down, bets pot on 80% of rivers |
| `minraise_bot` | Min-raises 70% of the time |
| `potlimp_bot` | Limps, then bets pot with a pair or better |
| `v1_bot` ... `v4_bot` | Frozen builds of our own bot |

## Pre-submission checklist

- [ ] `python3.11 tools/build.py` succeeds (syntax and forbidden-call checks pass).
- [ ] `python3.11 tools/gauntlet.py dist --python python3.11 ...` shows **zero notices** and no forfeits against every opponent.
- [ ] A Stage 2 run against `call_bot` and `checkraise_bot` finishes inside 75 s per leg.
- [ ] Fuzz test: garbage lines, unknown message types, missing fields, all-in situations, tiny `time_left_ms`; the bot never crashes and never prints to stdout except replies.
- [ ] The bot exits with code 0 after `match_end`.
