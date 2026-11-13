"""A/B several BOTP parameter variants of the dev bot on identical seeds vs a fixed panel.

Usage: python3 tools/sweep.py --variants '{"name":{...BOTP...}, "base":{}}' [--opps ...] [--seeds ...]
                              [--hands 2000] [-j 16] [--python PY] [--limits]
Prints chips/hand per variant per opponent, and the panel mean. Each variant is one gauntlet run
(logged to logs/gauntlet/ and logs/results.csv with label sweep-<name>).
"""
import argparse, json, os, re, subprocess, sys

from paths import TOOLS

PANEL = "shark_bot,tight_bot,riverbluff_bot,checkraise_bot,v3_bot"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", required=True)
    ap.add_argument("--opps", default=PANEL)
    ap.add_argument("--seeds", default="51,52,53,54,55,56")
    ap.add_argument("--hands", type=int, default=2000)
    ap.add_argument("-j", type=int, default=16)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--limits", action="store_true", help="use real stage-1 time limits")
    a = ap.parse_args()
    variants = json.loads(a.variants)
    table = {}
    for name, cfg in variants.items():
        env = dict(os.environ, BOTP=json.dumps(cfg))
        cmd = [a.python, os.path.join(TOOLS, "gauntlet.py"), "dev", "--opps", a.opps, "--seeds", a.seeds,
               "--hands", str(a.hands), "-j", str(a.j), "--label", "sweep-" + name, "--python", a.python]
        if not a.limits:
            cmd.append("--nolimits")
        out = subprocess.run(cmd, env=env, capture_output=True, text=True).stdout
        row = {}
        for l in out.splitlines():
            m = re.match(r"(\S+)\s+wins (\d+)/(\d+).*?([+-]\d+\.\d+)/hand", l)
            if m:
                row[m.group(1)] = (float(m.group(4)), m.group(2) + "/" + m.group(3))
        table[name] = row
        print("%-14s %s  | mean %+.3f" % (name, "  ".join("%s %+.2f (%s)" % (o[:8], v, w) for o, (v, w) in row.items()),
                                         sum(v for v, _ in row.values()) / max(1, len(row))), flush=True)


if __name__ == "__main__":
    main()
