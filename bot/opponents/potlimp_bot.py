"""Pot-limping opponent.

Preflop: as the button it always limps (calls). As the big blind it checks, calls raises up to
12 chips, and folds bigger raises unless it holds a pair.
Postflop: with a pair or better made using a hole card (or a made flush/straight) it bets/raises
the full pot; with a flush or straight draw it calls; otherwise it checks or folds.
Discard: keeps the pair if it has one, otherwise keeps the two highest cards.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json
from collections import Counter

RANKS = "23456789TJQKA"
BIG_BLIND = 2
CALL_CAP_PREFLOP = 12

HOLE = []  # our current hole cards (3 before the discard, 2 after)


def rank_value(card):
    return RANKS.index(card[0]) + 2


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
    if req["opp_bet"] <= CALL_CAP_PREFLOP or has_pair(HOLE, req["board"]):
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "fold"}


def postflop_action(req):
    legal = req["legal_actions"]
    to_call = req["to_call"]
    board = req["board"]

    if has_pair(HOLE, board) or made_flush_or_straight(HOLE, board):
        if "raise" in legal:
            amount = req["opp_bet"] + req["pot"] + to_call
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


def choose_action(req):
    if req["street"] == "preflop":
        return preflop_action(req)
    return postflop_action(req)


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
