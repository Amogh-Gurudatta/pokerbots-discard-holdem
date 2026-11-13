"""Run BOT vs several opponents over several seeds in parallel and print a results table.

Usage: python3 tools/gauntlet.py BOT [--opps a,b] [--seeds 1,2,3,4] [--hands 400] [--stage 1]
                                      [--nolimits] [-j 12] [--label NAME] [--save-logs]
BOT/opps: a path, 'dev', 'dist', or a name in opponents/ / example_bots/.

Every run writes logs/gauntlet/<timestamp>_<label>/summary.txt (the table + the command + BOTP).
A dev bot (src/) is first copied to <run>/src_snapshot/ and that frozen copy is played.
and appends one row per match to logs/results.csv. --save-logs also keeps each match's
engine logs (match.log, stderr) under the run directory. Env BOTP is passed to the bots.
"""
import argparse, csv, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

from paths import ENGINE, KIT, LOGS, RESULTS_CSV, SRC, resolve_bot, short, stamp

DEFAULT_OPPS = "random_bot,call_bot,jam_bot,tight_bot,aggro_bot,shark_bot,checkraise_bot,riverbluff_bot"


def run(bot, opp, seed, a, run_dir):
    opp_path = resolve_bot(opp)
    log_dir = ""
    if a.save_logs:
        log_dir = os.path.join(run_dir, "%s_s%d" % (short(opp_path), seed))
        os.makedirs(log_dir, exist_ok=True)
    cmd = [a.python, ENGINE, bot, opp_path, "--seed", str(seed), "--hands", str(a.hands),
           "--stage", str(a.stage), "--log-dir", log_dir, "--python", a.python]
    if a.nolimits:
        cmd.append("--no-time-limits")
    out = subprocess.run(cmd, cwd=KIT, capture_output=True, text=True).stdout
    m = re.search(r"A\s+\S+\s+net\s+([+-]?\d+)\s+time used\s+([\d.]+) s.*notices (\d+)", out)
    flag = "FORFEIT" if "forfeit" in out.lower() else ""
    if not m:
        return dict(opp=opp, seed=seed, net=None, time=None, notices=None, flag="ERROR: " + out[-200:])
    return dict(opp=opp, seed=seed, net=int(m.group(1)), time=float(m.group(2)),
                notices=int(m.group(3)), flag=flag)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot")
    ap.add_argument("--seeds", default="1,2,3,4")
    ap.add_argument("--opps", default=DEFAULT_OPPS)
    ap.add_argument("--hands", type=int, default=400)
    ap.add_argument("--stage", type=int, default=1)
    ap.add_argument("-j", type=int, default=12)
    ap.add_argument("--nolimits", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--save-logs", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    bot = resolve_bot(a.bot)
    seeds = [int(x) for x in a.seeds.split(",")]
    opps = a.opps.split(",")
    label = a.label or short(bot)
    ts = stamp()
    run_dir = os.path.join(LOGS, "gauntlet", "%s_%s" % (ts, label))
    os.makedirs(run_dir, exist_ok=True)
    if os.path.dirname(bot) == SRC:
        # freeze the dev source for this run so edits made while it runs can't leak in
        snap = os.path.join(run_dir, "src_snapshot")
        shutil.copytree(SRC, snap, ignore=shutil.ignore_patterns("__pycache__"))
        bot = os.path.join(snap, os.path.basename(bot))
    jobs = [(bot, o, s, a, run_dir) for o in opps for s in seeds]
    with ThreadPoolExecutor(a.j) as ex:
        res = list(ex.map(lambda j: run(*j), jobs))

    botp = os.environ.get("BOTP", "")
    lines = []
    for opp in opps:
        rows = [r for r in res if r["opp"] == opp]
        nets = [r["net"] for r in rows if r["net"] is not None]
        wins = sum(1 for n in nets if n > 0)
        maxt = max((r["time"] for r in rows if r["time"] is not None), default=0)
        notes = sum(r["notices"] or 0 for r in rows)
        flags = " ".join(r["flag"] for r in rows if r["flag"])
        lines.append("%-15s wins %d/%d  total %+8d  %+6.2f/hand  max_time %5.1fs  notices %d  per-seed %s %s" % (
            opp, wins, len(rows), sum(nets), sum(nets) / max(1, a.hands * len(nets)), maxt, notes, nets, flags))
    table = "\n".join(lines)
    print(table)
    with open(os.path.join(run_dir, "summary.txt"), "w") as f:
        f.write("cmd: %s\nBOTP: %s\nbot: %s\nhands: %d stage: %d nolimits: %s seeds: %s\n\n%s\n" % (
            " ".join(sys.argv), botp, bot, a.hands, a.stage, a.nolimits, a.seeds, table))
    new = not os.path.exists(RESULTS_CSV)
    with open(RESULTS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "label", "bot", "botp", "opp", "seed", "hands", "stage",
                        "nolimits", "net", "per_hand", "time_s", "notices", "flag"])
        for r in res:
            ph = "" if r["net"] is None else "%.3f" % (r["net"] / float(a.hands))
            w.writerow([ts, label, short(bot), botp, r["opp"], r["seed"], a.hands, a.stage,
                        int(a.nolimits), r["net"], ph, r["time"], r["notices"], r["flag"]])
    print("run log:", run_dir)


if __name__ == "__main__":
    main()
