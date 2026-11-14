"""Calling station opponent: never raises, checks when free, otherwise calls. Discards lowest rank."""
import sys, json, random
from collections import Counter

PARAMS = {
    "bet_size_mult": 1.0,   # multiplies bet/raise target sizes (this bot never bets or raises)
    "bluff_mult": 1.0,      # multiplies random bluff probabilities, clamped to [0,1] (this bot has none)
    "looseness": 0.0,       # extra chance to call a bet it would otherwise fold; 0.0 = off
    "size_sensitive": 0.0,  # chance scale to fold a big bet without a pair; 0.0 = off
}

RANKS = "23456789TJQKA"


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


HOLE = []  # our current hole cards (3 before the discard, 2 after)


def _decide(req):
    legal = req["legal_actions"]
    if "check" in legal:
        return {"type": "action", "action": "check"}
    if "call" in legal:
        return {"type": "action", "action": "call"}
    return {"type": "action", "action": "fold"}


def choose_action(req):
    """Apply the bot's decision, then the size_sensitive (fold) and looseness (call) knobs."""
    reply = _decide(req)
    if req["to_call"] > 0:
        legal = req["legal_actions"]
        if reply["action"] == "call" and "fold" in legal and not _made_hand(req) and _size_folds(req):
            return {"type": "action", "action": "fold"}
        if reply["action"] == "fold" and "call" in legal and _loose_call():
            return {"type": "action", "action": "call"}
    return reply

def choose_discard(req):
    worst = min(req["hole_cards"], key=lambda c: RANKS.index(c[0]))
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
