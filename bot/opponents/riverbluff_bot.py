"""River-bluff opponent: calls preflop raises up to 8 chips with any hand, check/calls
flop and turn bets up to 1/2 pot with anything, and on the river bets the full pot 80%
of the time when checked to or acting first (regardless of hand). Facing a river bet it
calls only with a pair or better.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

RANKS = "23456789TJQKA"
T_RANK = RANKS.index("T") + 2  # value 10
BIG_BLIND = 2
PREFLOP_CALL_CAP = 8   # calls any preflop raise up to this many chips
RIVER_BLUFF_RATE = 0.80

HOLE = []  # our current hole cards (3 before the discard, 2 after)


def rank_value(card):
    return RANKS.index(card[0]) + 2


def strength(hole, board):
    """Crude score from hole cards + board.

    Returns (score, pair_made, strong):
      score     - higher is better (pairs/trips using a hole card, high cards, suitedness)
      pair_made - a pair or better made using a hole card
      strong    - top pair or better, two pair, or trips (call-worthy postflop)
    """
    counts = Counter(c[0] for c in list(hole) + list(board))
    board_vals = [rank_value(c) for c in board]
    hole_vals = [rank_value(c) for c in hole]
    made = {rank_value(c) for c in hole if counts[c[0]] >= 2}
    trips = any(counts[c[0]] >= 3 for c in hole)

    score = sum(hole_vals)  # high cards
    if trips:
        score += 30
    if len(made) >= 2:
        score += 20
    elif made:
        score += 12
    if made:
        score += max(made)
    suited = any(hole[i][1] == hole[j][1] for i in range(len(hole)) for j in range(i + 1, len(hole)))
    if suited:
        score += 3

    pair_made = bool(made) or trips
    top = bool(made) and max(made) >= max(board_vals, default=0)
    strong = trips or len(made) >= 2 or top
    return score, pair_made, strong


def preflop_playable(hole):
    ranks = Counter(c[0] for c in hole)
    has_pair = any(n >= 2 for n in ranks.values())
    two_high = sum(1 for c in hole if rank_value(c) >= T_RANK) >= 2
    return has_pair or two_high


def choose_action(req):
    legal = req["legal_actions"]
    street = req["street"]
    to_call = req["to_call"]

    if street == "preflop":
        if "check" in legal:
            return {"type": "action", "action": "check"}
        if to_call > 0 and req["opp_bet"] <= PREFLOP_CALL_CAP:
            return {"type": "action", "action": "call"}
        if preflop_playable(HOLE) or req["opp_bet"] <= BIG_BLIND:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "fold"}

    _, pair_made, strong = strength(HOLE, req["board"])

    if street in ("flop", "turn"):
        if to_call == 0:
            return {"type": "action", "action": "check"}
        # bet size as a fraction of the pot before the bet
        pot_before = req["pot"] - to_call
        if "call" in legal and to_call * 2 <= pot_before:
            return {"type": "action", "action": "call"}
        if "call" in legal and strong:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "fold"}

    # river
    if to_call == 0:
        if "raise" in legal and random.random() < RIVER_BLUFF_RATE:
            target = req["opp_bet"] + req["pot"]
            amount = int(max(req["min_raise"], min(req["max_raise"], target)))
            return {"type": "action", "action": "raise", "amount": amount}
        return {"type": "action", "action": "check"}
    if "call" in legal and pair_made:
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "fold"}


def choose_discard(req):
    board = req["board"]
    best = None
    for i, card in enumerate(req["hole_cards"]):
        keep = req["hole_cards"][:i] + req["hole_cards"][i + 1:]
        score = strength(keep, board)[0]
        # max remaining score; ties discard the lower-ranked card
        key = (score, -rank_value(card))
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
