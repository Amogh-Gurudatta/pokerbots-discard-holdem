"""Min-raise opponent: whenever a raise is legal, raises to exactly min_raise with probability 0.7
(any hand). Otherwise calls when it can (folds only to a bet over 60 without a pair), else checks.
Discards the lowest-ranked card.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

PARAMS = {
    "raise_prob": 0.7,          # chance to raise whenever a raise is legal (random)
    "fold_to_call": 60,         # folds a bet larger than this many chips unless it holds a pair
    "bet_size_mult": 1.0,       # multiplies the raise target (base is min_raise)
    "bluff_mult": 1.0,          # multiplies random raise probability, clamped to [0,1]
    "looseness": 0.0,           # extra chance to call a bet it would otherwise fold; 0.0 = off
    "size_sensitive": 0.0,      # chance scale to fold a big bet without a pair; 0.0 = off
}

RANKS = "23456789TJQKA"

HOLE = []  # our current hole cards (3 before the discard, 2 after)


def rank_value(card):
    return RANKS.index(card[0]) + 2


def _p(base):
    """bluff_mult: scale a random probability, clamped to [0,1] (identity at 1.0)."""
    return min(1.0, max(0.0, base * PARAMS["bluff_mult"]))


def _size(amount):
    """bet_size_mult: scale a bet/raise size (identity at 1.0)."""
    return amount * PARAMS["bet_size_mult"]


def _loose_call():
    """looseness: when the bot would fold facing a bet, call instead with probability looseness.
    Uses no randomness when looseness is 0."""
    return PARAMS["looseness"] > 0 and random.random() < min(1.0, PARAMS["looseness"])


def _made_hand(req):
    """True if a pair (or better) is made using at least one hole card."""
    counts = Counter(c[0] for c in list(HOLE) + list(req.get("board", [])))
    return any(counts[c[0]] >= 2 for c in HOLE)


def _size_folds(req):
    """size_sensitive: facing a bet without pair-or-better, fold with probability
    size_sensitive * max(0, bet_to_pot - 0.66), where bet_to_pot = to_call / (pot - to_call).
    Uses no randomness when size_sensitive is 0."""
    if PARAMS["size_sensitive"] <= 0:
        return False
    denom = req["pot"] - req["to_call"]
    bet_to_pot = req["to_call"] / denom if denom > 0 else 0.0
    p = min(1.0, PARAMS["size_sensitive"] * max(0.0, bet_to_pot - 0.66))
    return random.random() < p


def has_pair(hole, board):
    """True if a pair (or better) is made using at least one hole card."""
    counts = Counter(c[0] for c in list(hole) + list(board))
    return any(counts[c[0]] >= 2 for c in hole)


def _decide(req):
    legal = req["legal_actions"]
    to_call = req["to_call"]

    if "raise" in legal and random.random() < _p(PARAMS["raise_prob"]):
        target = _size(req["min_raise"])
        amount = int(max(req["min_raise"], min(req["max_raise"], target)))
        return {"type": "action", "action": "raise", "amount": amount}

    if to_call > 0 and "call" in legal:
        if to_call > PARAMS["fold_to_call"] and not has_pair(HOLE, req["board"]):
            return {"type": "action", "action": "fold"}
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "check"}


def choose_action(req):
    """Apply the bot's decision, then the size_sensitive (fold) and looseness (call) knobs."""
    reply = _decide(req)
    if req["to_call"] > 0:
        legal = req["legal_actions"]
        if reply["action"] in ("call", "raise") and "fold" in legal and not _made_hand(req) and _size_folds(req):
            return {"type": "action", "action": "fold"}
        if reply["action"] == "fold" and "call" in legal and _loose_call():
            return {"type": "action", "action": "call"}
    return reply


def choose_discard(req):
    worst = min(req["hole_cards"], key=rank_value)
    return {"type": "discard", "card": worst}


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
