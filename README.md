# Pokerbots: Discard Hold'em

A bot for heads-up no-limit Hold'em with a twist: each player gets **three hole cards**, and after the flop betting both players **discard one card at the same time, face up**. The bot is written in Python 3.11, uses only the standard library, and ships as a single file: **[`bot/dist/bot.py`](bot/dist/bot.py)** (built from `bot/src/`).

## What the bot does

Every decision is made by sampling: the bot deals out the opponent's possible hands, keeps the ones that fit what it has seen, and picks the action with the best expected value.

- **Opponent range.** It starts as any 3-card hand. The opponent's discard (which is public) and each bet, raise or call then narrow it.
- **Learned, not assumed.** How much an action narrows the range is learned during the match from the opponent's betting frequencies and from hands shown at showdown. The bot starts from priors, so it is weakest in the first 20 to 30 hands.
- **EV-based betting.** Bet sizes (0.33, 0.66, 1.0 and 1.5× pot) are compared using the opponent's measured fold rate, so value bets and bluffs come from one formula instead of hand-strength thresholds.
- **Discard.** The three possible keeps are compared by equity against the modelled range, using the same random samples for each so the comparison is fair.
- **Safety first.** A forfeit (crash or timeout) costs more than almost any chip loss, so every reply is validated against the legal actions, every handler has a fallback, and the time budget is managed per decision.

Details and reasoning: [docs/strategy.md](docs/strategy.md).

## Results

Chips per hand over 2000-hand matches, several seeds each, using the organisers' engine on Python 3.11. These are practice opponents we wrote ourselves, so read them as "does it beat different styles", not as a ranking.

| Opponent | Result |
|---|---|
| Simple bots (random, call-station, all-in maniac, loose-aggressive) | +14 to +38 |
| Min-raiser, limp-then-pot-bettor, check-raiser | +14 to +19 |
| Tight-passive, river bluffer | about +1.3 to +2 |
| `shark_bot` (independently designed, equity-based) | about +1.4 to +2.4; won 21 of 24 real-format matches |
| Our own earlier versions | about even (within noise) |

Real matches are short and noisy. A 400-hand match swings by roughly ±400 chips, so we tuned on 2000-hand runs and compared versions on the same seeds.

### How it got here

| Version | Main change | Effect |
|---|---|---|
| v1 | Monte Carlo equity vs a range, with showdown calibration | Beats the simple bots; roughly even with the stronger ones |
| v2 | EV-based bet/check, adaptive button opening | About +0.9 chips/hand vs shark and tight (was +0.1) |
| v3 | EV-based raising, frequency-based range priors | Tight-passive +0.9 to +1.6; fixed over-folding to river bluffs |
| **v4 (submitted)** | EV-based preflop vs raises, larger bet sizes | Another +1 to +2 chips/hand vs shark and tight |

### What did not work

Documented because it shaped the final design:

- **More compute.** Tripling the Monte Carlo trials gave no gain (+4.47 vs +4.54). The limit is the quality of the opponent model, not sampling noise, so we didn't switch to numpy.
- **Bet-size-dependent fold rates.** Letting the fold rate scale with bet size beat our earlier builds head-to-head but lost heavily to bots that ignore sizing. Parked in `bot/experiments/`.
- **Small parameter tweaks after v4.** Every one landed inside the measurement noise or worse, so v4 was frozen.

Full history: [docs/results.md](docs/results.md).

## Running it

Requires Python 3.11. No packages needed.

```sh
cd bot
python3.11 tools/build.py                                               # src/ -> dist/bot.py, with checks
python3.11 tools/match.py dist shark_bot --python python3.11            # one match plus a breakdown
python3.11 tools/gauntlet.py dist --python python3.11 --seeds 1,2,3,4   # table vs every sparring bot
```

The tools run the unmodified organiser engine in `Starter Kit/`. The build refuses to produce a file containing `exec`, `open`, `subprocess`, `socket` or environment access, and the evaluator self-test (`python3 bot/src/fasteval.py`) checks it against the organisers' hand ranking. See [docs/testing.md](docs/testing.md).

## Repository layout

| Path | Contents |
|---|---|
| `bot/dist/bot.py` | **The submission.** Built file; never edit by hand. |
| `bot/src/` | `strategy.py` (decisions and protocol loop), `fasteval.py` (fast 5 to 7 card evaluator), `preflop_table.py` (generated strength table for all 1755 distinct 3-card hands). |
| `bot/tools/` | Build, match runner, multi-seed gauntlet, parameter sweeps, log analysis. |
| `bot/opponents/` | Sparring bots and frozen builds of earlier versions. |
| `bot/experiments/` | Variants that tested worse. Not built. |
| `bot/logs/results.csv` | Ledger of every test match played. |
| `Starter Kit/` | The organisers' material, unmodified. |
| `docs/` | Rules, strategy, testing, results, original plan. |

## Documentation

- [docs/rules.md](docs/rules.md): game rules, protocol, limits and scoring, distilled from the PDFs
- [docs/strategy.md](docs/strategy.md): how the bot decides, and its known limits
- [docs/testing.md](docs/testing.md): tools, workflow, noise
- [docs/results.md](docs/results.md): experiment history, including dead ends
- [docs/original-plan.md](docs/original-plan.md): the plan we started from
- [bot/README.md](bot/README.md): code layout and commands
