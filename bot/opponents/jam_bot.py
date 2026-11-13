"""Maniac opponent: raises to max_raise whenever a raise is legal, otherwise calls (or checks). Discards random."""
import sys, json, random

def choose_action(req):
    legal = req["legal_actions"]
    if "raise" in legal:
        amount = int(max(req["min_raise"], min(req["max_raise"], req["max_raise"])))
        return {"type": "action", "action": "raise", "amount": amount}
    if "call" in legal:
        return {"type": "action", "action": "call"}
    if "check" in legal:
        return {"type": "action", "action": "check"}
    return {"type": "action", "action": "fold"}

def choose_discard(req):
    return {"type": "discard", "card": random.choice(req["hole_cards"])}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        t = msg.get("type")
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
