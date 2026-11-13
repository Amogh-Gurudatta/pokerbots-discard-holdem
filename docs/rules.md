# Rules, protocol and limits

A distilled summary of the three organiser PDFs in `Starter Kit/` (Problem Statement, Protocol Specification, Rulebook). The PDFs are authoritative. Where this file disagrees with them, the PDFs win.

## The game

Heads-up no-limit Hold'em with two changes:

1. **Three hole cards** per player.
2. **Simultaneous exposed discard.** After the flop betting round, if nobody has folded, each player discards one hole card at the same time. Both discards are then revealed. Each player continues with two hole cards. This also happens when a player is all-in.

| Item | Value |
|---|---|
| Blinds | 1 (button) / 2 (big blind) |
| Stacks | 200 chips, reset every hand |
| Raises per street | at most 4 (the big blind counts as the first preflop) |
| Showdown | best 5 cards from 2 hole cards and 5 board cards |
| Seats | alternate every hand; the button acts first preflop and last on later streets |

Raise amounts are totals for the street ("raise to"). The minimum raise is `opp_bet + max(last raise size, 2)`. If the all-in amount is smaller than the minimum, only the all-in is legal. The board and all hole cards are fixed before any action, so discards never change the board.

## Matches and scoring

- A match is **two legs** with fresh bot processes: 200 deals per leg in Stage 1, 500 in Stage 2.
- Each deal is played in both legs with the **hole cards and seats swapped** between the two bots, so card luck largely cancels out.
- More net chips over the match wins: win = 1 point, draw = 0.5. Ties on points are broken by capped chips per hand, with each match's net clipped to ±400 (Stage 1) or ±1000 (Stage 2).
- Stage 1 is a Swiss qualifier. The top 32 teams play a round robin in Stage 2, with scores reset.
- **A forfeit** (crash, timeout, closed stdout) loses the whole match with Δ = −C. This is the worst possible result, so robustness comes before strength.

## Protocol

One JSON object per line. The engine writes to the bot's stdin and the bot replies on stdout. The bot answers only `action_request` and `discard_request`.

| Message | Direction | Purpose |
|---|---|---|
| `init` | engine → bot | once per leg: hand count, time bank |
| `new_hand` | engine → bot | seat and your 3 hole cards |
| `action_request` | engine → bot | street, board, pot, bets, `legal_actions`, `min_raise`/`max_raise`, history, `time_left_ms` |
| `discard_request` | engine → bot | your 3 cards and the flop |
| `discard_result` | engine → bot | both discards, now public |
| `hand_result` | engine → bot | result, net chips, history, opponent cards at showdown |
| `notice` | engine → bot | an illegal reply was corrected |
| `match_end` | engine → bot | the bot must exit |
| `action` / `discard` | bot → engine | the replies |

Rules of the interface:

- Flush stdout after every reply. **Nothing except protocol JSON may go to stdout.** A stray line counts as the reply and is corrected to check or fold. Use stderr for debugging.
- Choose only from `legal_actions`. `raise` can be missing even when `raises_left > 0` (for example when the opponent is all-in).
- Ignore unknown message types and fields.
- Illegal actions become check (else fold). Illegal discards become the lowest-ranked card.

## Limits

| Limit | Stage 1 | Stage 2 |
|---|---|---|
| Time bank per leg | 30 s | 75 s |
| Wall-clock kill per leg | 90 s | 225 s |
| Per reply | 5 s | 5 s |
| Startup (not charged) | 5 s | 5 s |

- Python 3.11, standard library, numpy and scipy only.
- Single `bot.py` (or a zip with `bot.py` at the top), at most 5 MB.
- 1 vCPU, 1 GB RAM, no network, read-only filesystem, no subprocesses.
- State is kept only within a leg.

## Conduct

The bot must be the team's own work. Organiser material (including `engine.py`) may be reused. Copying public bot repositories is prohibited. Any attempt to identify the two hands of a duplicate deal, or to carry information between legs, is prohibited.
