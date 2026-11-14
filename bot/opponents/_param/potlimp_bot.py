"""Pot-limping opponent.

Preflop: as the button it always limps (calls). As the big blind it checks, calls raises up to
12 chips, and folds bigger raises unless it holds a pair.
Postflop: with a pair or better made using a hole card (or a made flush/straight) it bets/raises
the full pot; with a flush or straight draw it calls; otherwise it checks or folds.
Discard: keeps the pair if it has one, otherwise keeps the two highest cards.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

PARAMS = {
    "call_cap_preflop": 12,     # big blind calls a raise up to this many chips (or any size with a pair)
    "raise_pot_frac": 1.0,      # postflop raise size: bet faced + call + this x pot
    "bet_size_mult": 1.0,       # multiplies bet/raise target sizes
    "bluff_mult": 1.0,          # multiplies random bluff probabilities (this bot has none)
    "looseness": 0.0,           # extra chance to call a bet it would otherwise fold; 0.0 = off
    "size_sensitive": 0.0,      # chance scale to fold a big bet without a pair; 0.0 = off
}

RANKS = "23456789TJQKA"
BIG_BLIND = 2  # position check: our bet below this means we are on the button (game structure)

HOLE = []  # our current hole cards (3 before the discard, 2 after)


def rank_value(card):
    return RANKS.index(card[0]) + 2


def _size(amount):
    """bet_size_mult: scale a bet/raise size (identity at 1.0)."""
    return amount * PARAMS["bet_size_mult"]


def _loose_call():
    """looseness: when the bot would fold facing a bet, call instead with probability looseness.
    Uses no randomness when looseness is 0."""
    return PARAMS["looseness"] > 0 and random.random() < min(1.0, PARAMS["looseness"])


def _size_folds(req, made):
    """size_sensitive: facing a bet without pair-or-better, fold with probability
    size_sensitive * max(0, bet_to_pot - 0.66), where bet_to_pot = to_call / (pot - to_call).
    Uses no randomness when size_sensitive is 0."""
    if made or PARAMS["size_sensitive"] <= 0:
        return False
    denom = req["pot"] - req["to_call"]
    bet_to_pot = req["to_call"] / denom if denom > 0 else 0.0
    p = min(1.0, PARAMS["size_sensitive"] * max(0.0, bet_to_pot - 0.66))
    return random.random() < p


def has_pair(hole, board):
    """True if a pair (or better) is made using at least one hole card."""
    counts = Counter(c[0] for c in list(hole) + list(board))
    return any(counts[c[0]] >= 2 for c in hole)


def _straight_windows(hole, board):
    """Yield (present_count, hole_in_window) for every 5-rank straight window (ace low and high)."""
    vals = {rank_value(c) for c in list(hole) + list(board)}
    hole_vals = {rank_value(c) for c in hole}
    if 14 in vals:
        vals.add(1)
        hole_vals = hole_vals | ({1} if 14 in hole_vals else set())
    for lo in range(1, 11):
        window = set(range(lo, lo + 5))
        yield len(window & vals), bool(window & hole_vals)


def made_flush_or_straight(hole, board):
    cards = list(hole) + list(board)
    for suit in "shdc":
        if sum(1 for c in cards if c[1] == suit) >= 5 and any(c[1] == suit for c in hole):
            return True
    return any(n == 5 and h for n, h in _straight_windows(hole, board))


def has_draw(hole, board):
    cards = list(hole) + list(board)
    for suit in "shdc":
        if sum(1 for c in cards if c[1] == suit) == 4 and any(c[1] == suit for c in hole):
            return True
    return any(n == 4 and h for n, h in _straight_windows(hole, board))


def preflop_action(req):
    legal = req["legal_actions"]
    to_call = req["to_call"]

    if to_call == 0 and "check" in legal:
        return {"type": "action", "action": "check"}
    if req["my_bet"] < BIG_BLIND:  # button: limp any hand
        return {"type": "action", "action": "call"}
    # big blind facing a raise
    if req["opp_bet"] <= PARAMS["call_cap_preflop"] or has_pair(HOLE, req["board"]):
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "fold"}


def postflop_action(req):
    legal = req["legal_actions"]
    to_call = req["to_call"]
    board = req["board"]

    if has_pair(HOLE, board) or made_flush_or_straight(HOLE, board):
        if "raise" in legal:
            amount = req["opp_bet"] + to_call + _size(req["pot"] * PARAMS["raise_pot_frac"])
            amount = int(max(req["min_raise"], min(req["max_raise"], amount)))
            return {"type": "action", "action": "raise", "amount": amount}
        if to_call > 0:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "check"}

    if has_draw(HOLE, board):
        if to_call > 0:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "check"}

    if to_call > 0:
        return {"type": "action", "action": "fold"}
    return {"type": "action", "action": "check"}


def _decide(req):
    if req["street"] == "preflop":
        return preflop_action(req)
    return postflop_action(req)


def choose_action(req):
    """Apply the bot's decision, then the size_sensitive (fold) and looseness (call) knobs."""
    reply = _decide(req)
    if req["to_call"] > 0:
        legal = req["legal_actions"]
        made = has_pair(HOLE, req.get("board", []))
        if reply["action"] in ("call", "raise") and "fold" in legal and _size_folds(req, made):
            return {"type": "action", "action": "fold"}
        if reply["action"] == "fold" and "call" in legal and _loose_call():
            return {"type": "action", "action": "call"}
    return reply


def choose_discard(req):
    board = req["board"]
    cards = req["hole_cards"]
    best = None
    for i, card in enumerate(cards):
        keep = cards[:i] + cards[i + 1:]
        # prefer keeping a pair; among equal options keep the higher cards
        key = (has_pair(keep, board), sum(rank_value(c) for c in keep))
        if best is None or key > best[0]:
            best = (key, card)
    return {"type": "discard", "card": best[1]}


def main():
    global HOLE
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        t = msg.get("type")
        if t == "new_hand" or t == "discard_result":
            HOLE = list(msg["hole_cards"])
            continue
        if t == "action_request":
            reply = choose_action(msg)
        elif t == "discard_request":
            reply = choose_discard(msg)
        elif t == "match_end":
            break
        else:
            continue
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
