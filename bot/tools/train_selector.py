"""Train the opponent-type profile selector from tools/collect.py data (pure Python, no numpy).

For each checkpoint (hand 25, 50, ...) and each non-base profile k, fit ridge regression
    features(opponent at checkpoint)  ->  advantage of profile k over base (chips/hand)
Samples are per leg (each variant contributes one feature vector per leg per seed, all sharing the
variant's paired advantage). Lambda and the switching margin are picked by cross-validation grouped
by variant; the report also shows leave-one-family-out results and the holdout split.

Usage: python3.11 tools/train_selector.py DATA.jsonl [--out src/selector_weights.py] [--clip 3]
"""
import argparse, json, math, os, random
from collections import defaultdict

from paths import SRC

LAMBDAS = [0.3, 1, 3, 10, 30, 100]
MARGINS = [0.0, 0.15, 0.3, 0.5, 0.8]


def solve(A, b):
    """Gaussian elimination with partial pivoting (A is n x n, small)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                if f:
                    for k in range(c, n + 1):
                        M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


def standardize(X):
    d = len(X[0])
    mean = [sum(x[j] for x in X) / len(X) for j in range(d)]
    scale = [math.sqrt(sum((x[j] - mean[j]) ** 2 for x in X) / len(X)) or 1.0 for j in range(d)]
    return mean, scale


def ridge(X, y, lam, mean, scale):
    """Returns [b0, w1..wd] on standardized features; intercept not penalized."""
    Z = [[1.0] + [(xi - m) / s for xi, m, s in zip(x, mean, scale)] for x in X]
    d = len(Z[0])
    A = [[0.0] * d for _ in range(d)]
    b = [0.0] * d
    for z, t in zip(Z, y):
        for i in range(d):
            zi = z[i]
            b[i] += zi * t
            Ai = A[i]
            for j in range(d):
                Ai[j] += zi * z[j]
    for i in range(1, d):
        A[i][i] += lam
    return solve(A, b)


def predict(coef, x, mean, scale):
    return coef[0] + sum(c * (xi - m) / s for c, xi, m, s in zip(coef[1:], x, mean, scale))


def load(path, clip):
    recs = []
    for line in open(path):
        r = json.loads(line)
        ph = r.get("per_hand") or {}
        if ph.get("base") is None or not r.get("features"):
            continue
        adv = {k: max(-clip, min(clip, v - ph["base"])) for k, v in ph.items() if k != "base" and v is not None}
        raw = {k: v - ph["base"] for k, v in ph.items() if k != "base" and v is not None}
        recs.append(dict(name=r["name"], family=r["family"], split=r["split"], adv=adv, raw=raw,
                         feats={int(k): v for k, v in r["features"].items()}))
    return recs


def fit_cp(recs, cp, profiles, lam):
    X, Y = [], {k: [] for k in profiles}
    for r in recs:
        for x in r["feats"].get(cp, []):
            X.append(x)
            for k in profiles:
                Y[k].append(r["adv"].get(k, 0.0))
    if not X:
        return None
    mean, scale = standardize(X)
    return dict(mean=mean, scale=scale, coef={k: ridge(X, Y[k], lam, mean, scale) for k in profiles})


def policy_gain(model, recs, cp, margin):
    """Realized (raw, unclipped) advantage of the chosen profile, averaged over legs of all variants."""
    g = []
    for r in recs:
        xs = r["feats"].get(cp, [])
        if not xs:
            continue
        for x in xs:  # one decision per leg, exactly as the bot sees it at runtime
            best, ba = None, margin
            for k, coef in model["coef"].items():
                a = predict(coef, x, model["mean"], model["scale"])
                if a > ba:
                    best, ba = k, a
            g.append(r["raw"].get(best, 0.0) if best else 0.0)
    return sum(g) / max(1, len(g)), len(g)


def cv(recs, cp, profiles, lam, margin, folds=6, seed=1):
    names = sorted({r["name"] for r in recs})
    rnd = random.Random(seed)
    rnd.shuffle(names)
    fold_of = {n: i % folds for i, n in enumerate(names)}
    tot, n = 0.0, 0
    for f in range(folds):
        tr = [r for r in recs if fold_of[r["name"]] != f]
        te = [r for r in recs if fold_of[r["name"]] == f]
        m = fit_cp(tr, cp, profiles, lam)
        if m is None or not te:
            continue
        g, k = policy_gain(m, te, cp, margin)
        tot += g * k
        n += k
    return tot / max(1, n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--out", default=os.path.join(SRC, "selector_weights.py"))
    ap.add_argument("--clip", type=float, default=3.0)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    recs = load(a.data, a.clip)
    train = [r for r in recs if r["split"] == "train"]
    hold = [r for r in recs if r["split"] == "holdout"]
    profiles = sorted({k for r in recs for k in r["adv"]})
    cps = sorted({cp for r in train for cp in r["feats"]})
    print("variants: train %d holdout %d  profiles %s  checkpoints %s" % (len(train), len(hold), profiles, cps))

    # baselines
    def mean_raw(rs, k):
        v = [r["raw"].get(k, 0.0) for r in rs]
        return sum(v) / max(1, len(v))
    oracle = lambda rs: sum(max([0.0] + list(r["raw"].values())) for r in rs) / max(1, len(rs))
    for name, rs in (("train", train), ("holdout", hold)):
        print("%-8s always-base 0.000  %s  oracle %+.3f" % (
            name, "  ".join("always-%s %+.3f" % (k, mean_raw(rs, k)) for k in profiles), oracle(rs)))
    fams = defaultdict(list)
    for r in recs:
        fams[r["family"]].append(r)
    print("per family mean raw advantage over base:")
    for f, rs in sorted(fams.items()):
        print("  %-11s n=%3d  %s  oracle %+.2f" % (f, len(rs), "  ".join("%s %+.2f" % (k, mean_raw(rs, k)) for k in profiles), oracle(rs)))

    # choose lambda / margin per checkpoint by grouped CV on train
    chosen = {}
    for cp in cps:
        best = None
        for lam in LAMBDAS:
            for mg in MARGINS:
                g = cv(train, cp, profiles, lam, mg)
                if best is None or g > best[0]:
                    best = (g, lam, mg)
        chosen[cp] = best
        print("cp %3d  CV policy gain %+.3f  (lambda %g, margin %.2f)" % (cp, best[0], best[1], best[2]))

    # leave-one-family-out (generalization to an unseen style), at the most-used checkpoints
    print("leave-one-family-out (train families), policy gain vs held-out family:")
    for cp in [c for c in (50, 100) if c in chosen]:
        _, lam, mg = chosen[cp]
        parts = []
        for f in sorted({r["family"] for r in train}):
            m = fit_cp([r for r in train if r["family"] != f], cp, profiles, lam)
            g, _ = policy_gain(m, [r for r in train if r["family"] == f], cp, mg)
            parts.append("%s %+.2f" % (f, g))
        print("  cp %d: %s" % (cp, "  ".join(parts)))

    # final models on all train data; evaluate on holdout
    models = {}
    for cp in cps:
        _, lam, mg = chosen[cp]
        models[str(cp)] = fit_cp(train, cp, profiles, lam)
        models[str(cp)]["margin"] = mg
        if hold:
            g, n = policy_gain(models[str(cp)], hold, cp, mg)
            print("holdout cp %3d policy gain %+.3f over %d variants (oracle %+.3f)" % (cp, g, n, oracle(hold)))
    if hold:
        print("holdout per variant at cp 50 / 100 (chosen profile, raw advantages):")
        for r in hold:
            row = []
            for cp in (50, 100):
                m = models.get(str(cp))
                xs = r["feats"].get(cp, [])
                if not m or not xs:
                    continue
                x = [sum(v[j] for v in xs) / len(xs) for j in range(len(xs[0]))]
                preds = {k: predict(c, x, m["mean"], m["scale"]) for k, c in m["coef"].items()}
                pick = max(preds, key=preds.get)
                pick = pick if preds[pick] > m["margin"] else "base"
                row.append("cp%d->%s" % (cp, pick))
            print("  %-16s %s  raw %s" % (r["name"], " ".join(row), {k: round(v, 2) for k, v in r["raw"].items()}))

    if a.no_write:
        return
    out = {"checkpoints": cps, "models": models, "profiles": profiles,
           "note": "ridge on standardized OppModel.features(); coef = [intercept, w...] predicting chips/hand advantage over base"}
    with open(a.out, "w") as f:
        f.write("# Generated by tools/train_selector.py from %s\n" % os.path.basename(a.data))
        f.write("SEL_W = %r\n" % (json.loads(json.dumps(out)),))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
