"""Min-raise opponent: whenever a raise is legal, raises to exactly min_raise with probability 0.7
(any hand). Otherwise calls when it can (folds only to a bet over 60 without a pair), else checks.
Discards the lowest-ranked card.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

RANKS = "23456789TJQKA"
RAISE_PROB = 0.7
FOLD_TO_CALL = 60

HOLE = []  # our current hole cards (3 before the discard, 2 after)


def rank_value(card):
    return RANKS.index(card[0]) + 2


def has_pair(hole, board):
    """True if a pair (or better) is made using at least one hole card."""
    counts = Counter(c[0] for c in list(hole) + list(board))
    return any(counts[c[0]] >= 2 for c in hole)


def choose_action(req):
    legal = req["legal_actions"]
    to_call = req["to_call"]

    if "raise" in legal and random.random() < RAISE_PROB:
        amount = int(max(req["min_raise"], min(req["max_raise"], req["min_raise"])))
        return {"type": "action", "action": "raise", "amount": amount}

    if to_call > 0 and "call" in legal:
        if to_call > FOLD_TO_CALL and not has_pair(HOLE, req["board"]):
            return {"type": "action", "action": "fold"}
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "check"}


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
