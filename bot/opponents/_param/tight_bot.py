"""Tight-passive opponent: plays only strong hands, calls rather than raises, discards the weakest card.

Hole cards are tracked from new_hand / discard_result (action_request does not repeat them).
"""
import sys, json, random
from collections import Counter

PARAMS = {
    "high_card_rank": 10,       # rank value (T=10) a hole card must reach to count as high; two needed preflop
    "preflop_call_cap": 2,      # calls a preflop bet up to this many chips even without a playable hand
    "value_bet_pot_frac": 0.5,  # bet size as a pot fraction when checked to with a pair or better
    "bet_size_mult": 1.0,       # multiplies bet/raise target sizes
    "bluff_mult": 1.0,          # multiplies random bluff probabilities, clamped to [0,1] (this bot has none)
    "looseness": 0.0,           # extra chance to call a bet it would otherwise fold; 0.0 = off
    "size_sensitive": 0.0,      # chance scale to fold a big bet without a pair; 0.0 = off
}

RANKS = "23456789TJQKA"

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
    two_high = sum(1 for c in hole if rank_value(c) >= PARAMS["high_card_rank"]) >= 2
    return has_pair or two_high


def _decide(req):
    legal = req["legal_actions"]
    street = req["street"]
    to_call = req["to_call"]

    if street == "preflop":
        if "check" in legal:
            return {"type": "action", "action": "check"}
        if preflop_playable(HOLE) or req["opp_bet"] <= PARAMS["preflop_call_cap"]:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "fold"}

    _, pair_made, strong = strength(HOLE, req["board"])
    if to_call == 0:
        if pair_made and "raise" in legal:
            increment = max(1, int(req["pot"] * PARAMS["value_bet_pot_frac"] + 1e-9))
            target = req["my_bet"] + _size(increment)
            amount = int(max(req["min_raise"], min(req["max_raise"], target)))
            return {"type": "action", "action": "raise", "amount": amount}
        return {"type": "action", "action": "check"}
    if strong:
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "fold"}


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
