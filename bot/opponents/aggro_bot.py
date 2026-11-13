"""Loose-aggressive opponent.

Preflop: raises to ~3x (of the current bet or the big blind) with any high card (T or better) or pair.
Postflop: when checked to, bets 2/3 pot about 60% of the time; calls bets with any pair or a
4-card flush draw; folds otherwise. Discards lowest rank.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

RANKS = "23456789TJQKA"
T_RANK = RANKS.index("T") + 2  # value 10
BIG_BLIND = 2
BET_FREQ = 0.6

HOLE = []  # our current hole cards (3 before the discard, 2 after)


def rank_value(card):
    return RANKS.index(card[0]) + 2


def has_pair(cards):
    counts = Counter(c[0] for c in cards)
    return any(n >= 2 for n in counts.values())


def has_flush_draw(hole, board):
    cards = list(hole) + list(board)
    for suit in "shdc":
        n = sum(1 for c in cards if c[1] == suit)
        if n >= 4 and any(c[1] == suit for c in hole):
            return True
    return False


def choose_action(req):
    legal = req["legal_actions"]
    street = req["street"]
    to_call = req["to_call"]

    if street == "preflop":
        playable = has_pair(HOLE) or any(rank_value(c) >= T_RANK for c in HOLE)
        if playable and "raise" in legal:
            target = 3 * max(req["opp_bet"], BIG_BLIND)
            amount = int(max(req["min_raise"], min(req["max_raise"], target)))
            return {"type": "action", "action": "raise", "amount": amount}
        if "check" in legal:
            return {"type": "action", "action": "check"}
        if playable or req["opp_bet"] <= BIG_BLIND:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "fold"}

    if to_call == 0:
        if "raise" in legal and random.random() < BET_FREQ:
            target = req["my_bet"] + max(1, (2 * req["pot"]) // 3)
            amount = int(max(req["min_raise"], min(req["max_raise"], target)))
            return {"type": "action", "action": "raise", "amount": amount}
        return {"type": "action", "action": "check"}

    if has_pair(HOLE + list(req["board"])) or has_flush_draw(HOLE, req["board"]):
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "fold"}


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
