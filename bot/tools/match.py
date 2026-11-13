"""Play one logged match and print the engine summary plus a breakdown of A's results.

Usage: python3 tools/match.py A B [--seed S] [--hands N] [--stage 1|2] [--nolimits] [--python PY]
Bots: a path, 'dev' (src/strategy.py), 'dist' (dist/bot.py), or a name in opponents/ / example_bots/.
Logs go to logs/matches/<timestamp>_<A>_vs_<B>_s<seed>/ (engine logs + summary.txt + analysis.txt).
Env vars such as BOTP / BOTDBG are passed through to the bots.
"""
import argparse, os, subprocess, sys

from paths import ENGINE, KIT, LOGS, resolve_bot, short, stamp
import analyze


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hands", type=int)
    ap.add_argument("--stage", type=int, default=1)
    ap.add_argument("--nolimits", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    A, B = resolve_bot(a.a), resolve_bot(a.b)
    d = os.path.join(LOGS, "matches", "%s_%s_vs_%s_s%d" % (stamp(), short(A), short(B), a.seed))
    os.makedirs(d, exist_ok=True)
    cmd = [a.python, ENGINE, A, B, "--seed", str(a.seed), "--stage", str(a.stage),
           "--log-dir", d, "--python", a.python]
    if a.hands:
        cmd += ["--hands", str(a.hands)]
    if a.nolimits:
        cmd.append("--no-time-limits")
    out = subprocess.run(cmd, cwd=KIT, capture_output=True, text=True)
    summary = out.stdout + out.stderr
    with open(os.path.join(d, "summary.txt"), "w") as f:
        f.write(" ".join(cmd) + "\n\n" + summary)
    print(summary.strip())
    ml = os.path.join(d, "match.log")
    if os.path.exists(ml):
        text = analyze.report(ml)
        with open(os.path.join(d, "analysis.txt"), "w") as f:
            f.write(text)
        print(text)
    print("logs:", d)


if __name__ == "__main__":
    main()
