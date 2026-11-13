"""Break down bot A's results in an engine match.log by how hands ended.

Usage: python3 tools/analyze.py PATH   (a match.log or a logs/matches/<run>/ directory)
"""
import os, re, sys
from collections import defaultdict


def parse(path):
    hands = []
    for l in open(path):
        m = re.match(r"(L\d) #(\d+)\s+button (\w)", l)
        if not m:
            continue
        res = re.search(r"\| ((?:fold|showdown).*)$", l.strip())
        nets = dict(re.findall(r"\b([AB]) ([+-]\d+)", res.group(1))) if res else {}
        acts = re.search(r"\| preflop: (.*?) \| (?:fold|showdown)", l)
        hands.append(dict(leg=m.group(1), id=int(m.group(2)), button=m.group(3),
                          netA=int(nets.get("A", 0)), line=l.strip(),
                          acts=acts.group(1) if acts else "",
                          showdown="showdown" in (res.group(1) if res else "")))
    return hands


def report(path, worst=8):
    if os.path.isdir(path):
        path = os.path.join(path, "match.log")
    hs = parse(path)
    cat = defaultdict(lambda: [0, 0])
    for h in hs:
        a = h["acts"]
        pos = "BTN" if h["button"] == "A" else "BB"
        streets = [s for s in ("flop:", "turn:", "river:") if s in a]
        last = streets[-1][:-1] if streets else "preflop"
        if h["showdown"]:
            k = "showdown"
        else:
            folder = re.findall(r"([AB])f", a)
            k = "%s_folds_%s" % (folder[-1] if folder else "?", last)
        for key in (k, pos + ":" + k):
            cat[key][0] += 1
            cat[key][1] += h["netA"]
    lines = ["hands %d  netA %+d  (%+.2f/hand)" % (len(hs), sum(h["netA"] for h in hs),
                                                 sum(h["netA"] for h in hs) / max(1, len(hs)))]
    for k in sorted(cat, key=lambda k: (":" in k, k)):
        n, v = cat[k]
        lines.append("  %-24s n=%5d  net %+7d  avg %+6.1f" % (k, n, v, v / n))
    lines.append("worst hands for A:")
    for h in sorted(hs, key=lambda h: h["netA"])[:worst]:
        lines.append("  " + h["line"][:260])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(report(sys.argv[1]))
