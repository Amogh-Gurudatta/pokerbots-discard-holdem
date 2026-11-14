"""Generate randomized opponent variants into opponents/generated/ plus manifest.json.

Usage (from bot/):
  python3.11 tools/make_variants.py [--per-family 12] [--ev 40] [--seed 1]
                                    [--holdout-families riverbluff] [--python python3.11]

Heuristic families: every bot in opponents/_param/ is copied --per-family times as
<family>__NN.py with a `PARAMS.update({...})` line inserted right after the PARAMS dict
(variant 00 keeps the default PARAMS). "ev" family: --ev builds of our own dev bot with random
parameters (tools/build.py --params). The fixed holdout bots (shark_bot, v3_bot, v4_bot,
jam_bot) are added to the manifest by name. opponents/generated/ is wiped on every run.

manifest.json: list of {"name", "path" (absolute), "family", "split" ("train"|"holdout"), "params"}.
For heuristic variants "params" is the full effective PARAMS dict; for ev it is the build params.
"""
import argparse, ast, json, os, random, shutil, subprocess, sys

from paths import ROOT, OPPONENTS, resolve_bot

PARAM_DIR = os.path.join(OPPONENTS, "_param")
GEN_DIR = os.path.join(OPPONENTS, "generated")
FIXED = ["shark_bot", "v3_bot", "v4_bot", "jam_bot"]
GENERIC = {"size_sensitive", "looseness", "bluff_mult", "bet_size_mult"}
RANK_KEYS = {"high_card_rank", "playable_rank"}          # rank thresholds (2..14)
PROB_SUFFIXES = ("_rate", "_freq", "_prob")               # probabilities -> clamp to [0, 1]


def read_params(src):
    """Return (default PARAMS dict, 1-based line number where the PARAMS dict ends)."""
    tree = ast.parse(src)
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "PARAMS"
                and isinstance(node.value, ast.Dict)):
            params = eval(compile(ast.Expression(node.value), "<params>", "eval"), {"__builtins__": {}})
            return params, node.end_lineno
    raise ValueError("no top-level `PARAMS = {...}` found")


def randomize(defaults, rng):
    """Random PARAMS overrides for one heuristic variant (only the keys that change)."""
    upd = {
        "size_sensitive": rng.uniform(0, 1),
        "looseness": rng.uniform(0, 0.35),
        "bluff_mult": rng.uniform(0.4, 2.2),
        "bet_size_mult": rng.uniform(0.5, 2.0),
    }
    for k, v in defaults.items():
        if k in GENERIC or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if k in RANK_KEYS:
            upd[k] = max(6, min(13, int(v) + rng.randint(-3, 2)))
        elif isinstance(v, int):
            upd[k] = max(1, int(round(v * rng.uniform(0.6, 1.5))))
        else:
            nv = v * rng.uniform(0.6, 1.5)
            if k.endswith(PROB_SUFFIXES):
                nv = max(0.0, min(1.0, nv))
            upd[k] = nv
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in upd.items()}


def ev_params(rng):
    return {
        "r_oop": round(rng.uniform(0.7, 0.95), 4),
        "r_ip": round(rng.uniform(0.82, 0.98), 4),
        "fr_mult": round(rng.uniform(0.4, 1.0), 4),
        "f_alpha": rng.choice([0, 0, 0.5, 1.0]),
        "bet_sizes": rng.choice([[0.5], [0.66], [0.33, 0.66, 1.0], [0.33, 0.66, 1.0, 1.5], [0.5, 1.0]]),
        "open_thr": round(rng.uniform(0.2, 0.5), 4),
        "raise_edge": round(rng.uniform(0, 0.06), 4),
        "chk_real_oop": round(rng.uniform(0.85, 1.0), 4),
        "selector": False,
    }


def validate(path):
    with open(path) as f:
        ast.parse(f.read(), feature_version=(3, 11))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-family", type=int, default=12)
    ap.add_argument("--ev", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--holdout-families", default="riverbluff", help="comma-separated families -> split holdout")
    ap.add_argument("--python", default=sys.executable, help="interpreter for tools/build.py")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    holdout = set(x for x in a.holdout_families.split(",") if x)

    shutil.rmtree(GEN_DIR, ignore_errors=True)
    os.makedirs(GEN_DIR)
    manifest = []

    def add(name, path, family, params):
        validate(path)
        manifest.append({"name": name, "path": os.path.abspath(path), "family": family,
                         "split": "holdout" if family in holdout else "train", "params": params})

    for fn in sorted(os.listdir(PARAM_DIR)):
        if not fn.endswith("_bot.py"):
            continue
        fam = fn[:-len("_bot.py")]
        with open(os.path.join(PARAM_DIR, fn)) as f:
            src = f.read()
        defaults, end = read_params(src)
        lines = src.splitlines(keepends=True)
        for i in range(a.per_family):
            upd = {} if i == 0 else randomize(defaults, rng)
            out = list(lines)
            if upd:
                out.insert(end, "PARAMS.update(%r)\n" % upd)
            name = "%s__%02d" % (fam, i)
            path = os.path.join(GEN_DIR, name + ".py")
            with open(path, "w") as f:
                f.write("".join(out))
            add(name, path, fam, dict(defaults, **upd))

    for i in range(a.ev):
        params = ev_params(rng)
        name = "ev__%02d" % i
        path = os.path.join(GEN_DIR, name + ".py")
        r = subprocess.run([a.python, os.path.join("tools", "build.py"), "--quiet", "--out", path,
                            "--params", json.dumps(params)], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("build failed for %s:\n%s" % (name, r.stderr[-800:]))
        add(name, path, "ev", params)

    for name in FIXED:
        add(name, resolve_bot(name), "fixed", {})
        manifest[-1]["split"] = "holdout"

    with open(os.path.join(GEN_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    fams = {}
    for m in manifest:
        fams.setdefault((m["family"], m["split"]), 0)
        fams[(m["family"], m["split"])] += 1
    print("wrote %d entries to %s" % (len(manifest), os.path.join(GEN_DIR, "manifest.json")))
    for (fam, sp), n in sorted(fams.items()):
        print("  %-12s %-8s %d" % (fam, sp, n))


if __name__ == "__main__":
    main()
