"""Collect training data for an opponent-type selector.

Usage (from bot/):
  python3.11 tools/collect.py [--manifest opponents/generated/manifest.json] [--split train|holdout|all]
        [--limit N] [--profiles JSON] [--seeds 1,2,3] [--hands 400] [-j 16]
        [--python python3.11] [--keep-logs] [--no-append] [--selftest]

For every manifest entry x profile x seed, plays OUR dev bot (a frozen snapshot of src/) against the
variant with --no-time-limits, passing env BOTP=json(profile + {"selector": false}) and BOTDBG=1.
All profiles of a variant use the same seeds (paired). FEAT lines (`FEAT h25 [..]`) are parsed from A's
stderr in the "base" profile runs only.

Output: logs/collect/<timestamp>/data.jsonl (also appended to logs/selector_data.jsonl unless --no-append),
one JSON object per variant:
  {"name","family","split","params","seeds","net": {profile: [net per seed | null]},
   "per_hand": {profile: mean chips/hand | null}, "features": {"25": [vector per leg, ...], ...},
   "flags": {profile: [flag per seed]}   # only present when something failed/forfeited
"""
import argparse, json, os, re, shutil, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

from paths import ENGINE, KIT, LOGS, ROOT, SRC, stamp

DEFAULT_PROFILES = '{"base":{},"elastic":{"f_alpha":1.0},"tightx":{"fr_mult":0.5}}'
DEFAULT_MANIFEST = os.path.join(ROOT, "opponents", "generated", "manifest.json")
NET_RE = re.compile(r"A\s+\S+\s+net\s+([+-]?\d+)\s+time used\s+([\d.]+) s.*notices (\d+)")
FEAT_RE = re.compile(r"^FEAT h(\d+) (\[.*\])\s*$", re.M)


def parse_feat(text):
    """{hand number (str): feature vector} from FEAT lines of one stderr text (last line per hand wins)."""
    out = {}
    for m in FEAT_RE.finditer(text):
        try:
            out[m.group(1)] = json.loads(m.group(2))
        except ValueError:
            pass
    return out


def read_text(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def play(a, bot, var, profile, cfg, seed, run_dir):
    """One match. Returns dict(net, flag, feats=[per-leg {hand: vec}])."""
    mdir = os.path.join(run_dir, "matches", "%s_%s_s%d" % (var["name"], profile, seed))
    os.makedirs(mdir, exist_ok=True)
    cmd = [a.python, ENGINE, bot, var["path"], "--seed", str(seed), "--hands", str(a.hands),
           "--stage", "1", "--log-dir", mdir, "--python", a.python, "--no-time-limits"]
    env = dict(os.environ, BOTP=json.dumps(dict(cfg, selector=False)), BOTDBG="1")
    res = dict(net=None, flag="", feats=[])
    try:
        p = subprocess.run(cmd, cwd=KIT, env=env, capture_output=True, text=True, timeout=a.timeout)
        out = p.stdout
        m = NET_RE.search(out)
        if "forfeit" in out.lower():
            res["flag"] = "FORFEIT"
        elif not m:
            res["flag"] = "ERROR: " + (out[-200:] or p.stderr[-200:]).strip().replace("\n", " | ")
        else:
            res["net"] = int(m.group(1))
        if profile == "base":
            res["feats"] = [parse_feat(read_text(os.path.join(mdir, "A_leg%d_stderr.txt" % leg)))
                            for leg in (1, 2)]
    except subprocess.TimeoutExpired:
        res["flag"] = "TIMEOUT"
    except Exception as e:  # never crash the run
        res["flag"] = "EXC: %r" % (e,)
    if not a.keep_logs:
        shutil.rmtree(mdir, ignore_errors=True)
    return res


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def build_record(var, profiles, seeds, hands, results):
    """results: {(profile, seed): play-dict}."""
    rec = {"name": var["name"], "family": var["family"], "split": var["split"],
           "params": var["params"], "seeds": seeds, "net": {}, "per_hand": {}, "features": {}}
    flags = {}
    for prof in profiles:
        nets = [results[(prof, s)]["net"] for s in seeds]
        rec["net"][prof] = nets
        m = mean(nets)
        rec["per_hand"][prof] = None if m is None else m / hands
        fl = [results[(prof, s)]["flag"] for s in seeds]
        if any(fl):
            flags[prof] = fl
    if flags:
        rec["flags"] = flags
    if "base" in profiles:
        feats = {}
        for s in seeds:
            for leg in results[("base", s)]["feats"]:
                for h, vec in leg.items():
                    feats.setdefault(h, []).append(vec)
        rec["features"] = dict(sorted(feats.items(), key=lambda kv: int(kv[0])))
    return rec


def summary(recs, profiles):
    lines = []
    fams = sorted(set(r["family"] for r in recs))
    head = "%-12s %4s " % ("family", "n") + " ".join("%11s" % p for p in profiles)
    others = [p for p in profiles if p != "base"]
    if "base" in profiles:
        head += " | " + " ".join("%11s" % ("d_" + p) for p in others)
    lines.append(head)
    for fam in fams + ["ALL"]:
        rs = [r for r in recs if fam == "ALL" or r["family"] == fam]
        row = "%-12s %4d " % (fam, len(rs))
        for p in profiles:
            m = mean([r["per_hand"].get(p) for r in rs])
            row += " %+11.3f" % m if m is not None else " %11s" % "n/a"
        if "base" in profiles:
            row += " |"
            for p in others:
                d = mean([None if (r["per_hand"].get(p) is None or r["per_hand"].get("base") is None)
                          else r["per_hand"][p] - r["per_hand"]["base"] for r in rs])
                row += " %+11.3f" % d if d is not None else " %11s" % "n/a"
        lines.append(row)
    nfail = sum(1 for r in recs if "flags" in r)
    if nfail:
        lines.append("variants with failed/forfeited matches: %d (see 'flags' in data.jsonl)" % nfail)
    return "\n".join(lines)


def selftest():
    fake = ("noise line\nFEAT h25 [0.6, 0.3, 1]\nDBG river something\n"
            "FEAT h50 [0.5, 0.25, 2.5]\nFEAT h50 [0.55, 0.25, 2.5]\nFEAT hx [1]\n")
    got = parse_feat(fake)
    assert got == {"25": [0.6, 0.3, 1], "50": [0.55, 0.25, 2.5]}, got
    line = "  A  dev.py   net    +123   time used   4.50 s (bank 10 s per leg)   notices 0"
    assert NET_RE.search(line).group(1) == "+123"
    results = {("base", 1): dict(net=100, flag="", feats=[got, got]),
               ("elastic", 1): dict(net=None, flag="FORFEIT", feats=[])}
    rec = build_record({"name": "x", "family": "f", "split": "train", "params": {}},
                       ["base", "elastic"], [1], 400, results)
    assert rec["features"]["25"] == [[0.6, 0.3, 1], [0.6, 0.3, 1]] and rec["flags"]["elastic"] == ["FORFEIT"]
    assert rec["per_hand"] == {"base": 0.25, "elastic": None}
    print(summary([rec], ["base", "elastic"]))
    print("selftest ok: parsed", got)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--split", default="train", choices=["train", "holdout", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--profiles", default=DEFAULT_PROFILES)
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--hands", type=int, default=400)
    ap.add_argument("-j", type=int, default=16)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per match")
    ap.add_argument("--keep-logs", action="store_true")
    ap.add_argument("--no-append", action="store_true", help="do not append to logs/selector_data.jsonl")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    profiles = json.loads(a.profiles)
    seeds = [int(x) for x in a.seeds.split(",")]
    with open(a.manifest) as f:
        variants = json.load(f)
    if a.split != "all":
        variants = [v for v in variants if v["split"] == a.split]
    if a.limit:
        variants = variants[:a.limit]
    if not variants:
        raise SystemExit("no manifest entries selected")

    run_dir = os.path.join(LOGS, "collect", stamp())
    os.makedirs(run_dir, exist_ok=True)
    snap = os.path.join(run_dir, "src_snapshot")
    shutil.copytree(SRC, snap, ignore=shutil.ignore_patterns("__pycache__"))
    bot = os.path.join(snap, "strategy.py")

    data_path = os.path.join(run_dir, "data.jsonl")
    agg_path = os.path.join(LOGS, "selector_data.jsonl")
    lock = threading.Lock()
    pending = {v["name"]: len(profiles) * len(seeds) for v in variants}
    results = {v["name"]: {} for v in variants}
    recs = []
    done = [0]
    total = len(variants)
    print("%d variants x %d profiles x %d seeds = %d matches, %d hands, -j %d; run dir %s" % (
        total, len(profiles), len(seeds), total * len(profiles) * len(seeds), a.hands, a.j, run_dir), flush=True)

    def job(var, prof, seed):
        r = play(a, bot, var, prof, profiles[prof], seed, run_dir)
        with lock:
            results[var["name"]][(prof, seed)] = r
            pending[var["name"]] -= 1
            if pending[var["name"]]:
                return
            rec = build_record(var, list(profiles), seeds, a.hands, results.pop(var["name"]))
            recs.append(rec)
            line = json.dumps(rec) + "\n"
            with open(data_path, "a") as f:
                f.write(line)
            if not a.no_append:
                with open(agg_path, "a") as f:
                    f.write(line)
            done[0] += 1
            ph = rec["per_hand"]
            print("[%d/%d] %-16s %s%s" % (done[0], total, var["name"],
                  " ".join("%s %s" % (p, "n/a" if v is None else "%+.2f" % v) for p, v in ph.items()),
                  "  FLAGGED" if "flags" in rec else ""), flush=True)

    # variant-major order so variants complete (and get written) progressively
    jobs = [(v, p, s) for v in variants for s in seeds for p in profiles]
    with ThreadPoolExecutor(a.j) as ex:
        list(ex.map(lambda j: job(*j), jobs))

    if not a.keep_logs:
        shutil.rmtree(os.path.join(run_dir, "matches"), ignore_errors=True)
    text = summary(recs, list(profiles))
    print("\nchips/hand per family (mean over variants); d_X = X - base")
    print(text)
    with open(os.path.join(run_dir, "summary.txt"), "w") as f:
        f.write("cmd: %s\nprofiles: %s\nseeds: %s hands: %d\n\n%s\n" % (
            " ".join(sys.argv), a.profiles, a.seeds, a.hands, text))
    print("data:", data_path)


if __name__ == "__main__":
    main()
