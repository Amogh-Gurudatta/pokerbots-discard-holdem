# Pokerbots: Discard Hold'em

A Python 3.11 bot for the Pokerbots competition. The game is heads-up no-limit Hold'em with **three hole cards and a simultaneous, exposed discard after the flop**.

The submission is **[`bot/dist/bot.py`](bot/dist/bot.py)**: one self-contained file, standard library only, built from `bot/src/`.

## The approach in one paragraph

Every decision is **Monte Carlo equity against a modelled opponent range**. The range starts as a random 3-card hand, and the keep-the-best-2 discard logic narrows it down. After that it is shrunk by what the opponent did (bets, raises, calls) and by what the revealed discard implies. How much each action narrows it is learned during the match, from how often the opponent bets and from the strength of the hands they showed down. Bets and raises are then chosen by **expected value**, using the opponent's measured fold rate, rather than by fixed hand-strength thresholds. See [docs/strategy.md](docs/strategy.md).

## Results

All numbers are chips per hand, from 2000-hand matches over several seeds, played with the organisers' engine on Python 3.11.

| Opponent | Result |
|---|---|
| Simple bots (random, call-station, all-in maniac, loose-aggressive) | +14 to +38 |
| Min-raiser, limp-then-pot-bettor, check-raiser | +14 to +19 |
| Tight-passive, river bluffer | about +1.3 to +2 |
| `shark_bot`, an independently designed equity-based bot | about +1.4 to +2.4, wins 21 of 24 real-format matches |
| Our own earlier versions | about even to slightly ahead (differences are within noise) |

The full history, including what did **not** work (extra compute, numpy, size-dependent fold models), is in [docs/results.md](docs/results.md).

## Repository layout

| Path | Contents |
|---|---|
| `bot/dist/bot.py` | **The submission.** Built file; never edit by hand. |
| `bot/src/` | Source: `strategy.py` (strategy and protocol loop), `fasteval.py` (fast hand evaluator), `preflop_table.py` (generated table of 3-card hand strength). |
| `bot/tools/` | Build, tournament-style testing, parameter sweeps, log analysis. |
| `bot/opponents/` | Sparring bots, plus frozen builds of our own earlier versions. |
| `bot/experiments/` | Variants that tested worse. Not built. |
| `bot/logs/results.csv` | Ledger of every test match played (the rest of `logs/` is git-ignored). |
| `Starter Kit/` | The organisers' material, unmodified: `engine.py`, rules PDFs, starter bot. |
| `docs/` | Rules summary, strategy design, testing guide, results history. |
| `memory.md` | The running notes kept during development (decisions, dead ends, lessons). |

## Quick start

Requires Python 3.11. No packages are needed.

```sh
cd bot
python3.11 tools/build.py                              # src/ -> dist/bot.py (checks 3.11 syntax and forbidden calls)
python3.11 tools/match.py dist shark_bot --python python3.11             # one logged match + breakdown
python3.11 tools/gauntlet.py dist --python python3.11 --seeds 1,2,3,4    # table vs every sparring bot
```

The tools run the organisers' `Starter Kit/engine.py`. See [docs/testing.md](docs/testing.md) for the full workflow and for why 400-hand matches are too noisy to tune on.

## Documentation

- [docs/rules.md](docs/rules.md): the game, protocol, limits and scoring, distilled from the three PDFs
- [docs/strategy.md](docs/strategy.md): how the bot decides
- [docs/testing.md](docs/testing.md): test tools, workflow, noise
- [docs/results.md](docs/results.md): experiment history and conclusions
- [bot/README.md](bot/README.md): the bot's code layout and commands
