"""Check-raise opponent: tight preflop but defends wider (calls any raise up to 10 chips).

Postflop: when it acts first it checks. When facing a bet after its own check on the
same street it raises to 3x the bet ~45% of the time (always with pair or better),
otherwise calls with a pair or draw and folds the rest. When checked to in position
it bets 1/2 pot 50% of the time.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

RANKS = "23456789TJQKA"
T_RANK = RANKS.index("T") + 2  # value 10
BIG_BLIND = 2
WIDE_CALL_CAP = 10       # calls any preflop raise up to this many chips
CHECK_RAISE_RATE = 0.45  # raise rate after checking and facing a bet (no made hand)
BET_RATE = 0.50          # bet rate when checked to in position

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


def has_draw(hole, board):
    """Crude draw check: four-to-a-flush or open-ended-style four-of-a-window straight draw
    that uses at least one hole card."""
    cards = list(hole) + list(board)
    suits = Counter(c[1] for c in cards)
    for s, n in suits.items():
        if n == 4 and any(c[1] == s for c in hole):
            return True
    vals = {rank_value(c) for c in cards}
    hole_vals = {rank_value(c) for c in hole}
    if 14 in vals:
        vals.add(1)  # ace low
    if 14 in hole_vals:
        hole_vals.add(1)
    for low in range(1, 11):
        window = set(range(low, low + 5))
        present = vals & window
        if len(present) == 4 and any(v in window for v in hole_vals):
            return True
    return False


def preflop_playable(hole):
    ranks = Counter(c[0] for c in hole)
    has_pair = any(n >= 2 for n in ranks.values())
    two_high = sum(1 for c in hole if rank_value(c) >= T_RANK) >= 2
    return has_pair or two_high


def clamp_raise(req, target):
    return int(max(req["min_raise"], min(req["max_raise"], target)))


def opp_acted_this_street(req):
    return any(h["street"] == req["street"] and h["actor"] == "opp" for h in req["history"])


def i_checked_this_street(req):
    return any(h["street"] == req["street"] and h["actor"] == "me" and h["action"] == "check"
               for h in req["history"])


def choose_action(req):
    legal = req["legal_actions"]
    street = req["street"]
    to_call = req["to_call"]
    can_raise = "raise" in legal

    if street == "preflop":
        if "check" in legal:
            return {"type": "action", "action": "check"}
        if to_call > 0 and req["opp_bet"] <= WIDE_CALL_CAP:
            return {"type": "action", "action": "call"}
        if preflop_playable(HOLE) or req["opp_bet"] <= BIG_BLIND:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "fold"}

    _, pair_made, _ = strength(HOLE, req["board"])
    if to_call == 0:
        if not opp_acted_this_street(req):
            # we act first on this street: always check
            return {"type": "action", "action": "check"}
        # checked to in position
        if can_raise and random.random() < BET_RATE:
            target = req["my_bet"] + max(1, req["pot"] // 2)
            return {"type": "action", "action": "raise", "amount": clamp_raise(req, target)}
        return {"type": "action", "action": "check"}

    # facing a bet
    if i_checked_this_street(req) and can_raise and \
            (pair_made or random.random() < CHECK_RAISE_RATE):
        target = 3 * req["opp_bet"]
        return {"type": "action", "action": "raise", "amount": clamp_raise(req, target)}
    if "call" in legal and (pair_made or has_draw(HOLE, req["board"])):
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
