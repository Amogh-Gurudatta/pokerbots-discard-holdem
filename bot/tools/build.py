"""Inline src/fasteval.py + src/preflop_table.py into src/strategy.py -> dist/bot.py.

Usage: python3 tools/build.py [--snapshot vN] [--out PATH] [--params JSON]
  --snapshot vN   also copy the build to opponents/vN_bot.py (frozen sparring version)
  --out PATH      write the build somewhere other than dist/bot.py (use for experiments)
  --params JSON   bake these P-dict defaults into the build (e.g. opponent variants)
"""
import argparse, ast, os, re, shutil

from paths import SRC, DIST, OPPONENTS


def read(name):
    with open(os.path.join(SRC, name)) as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot")
    ap.add_argument("--out")
    ap.add_argument("--params")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    fe = read("fasteval.py")
    # keep only the evaluator: drop fasteval's MC machinery (exec-generated runners, equity())
    # and the self-test; the strategy uses its own mc_equity
    fe = fe[:fe.index("_MAX_RESAMPLE = 200")]
    pt = read("preflop_table.py")
    st = read("strategy.py")
    st = re.sub(r"# ==== EVALUATOR.*?# ==== END EVALUATOR ====",
                lambda m: "# ==== EVALUATOR (inlined from src/fasteval.py) ====\n" + fe +
                "\n# ==== END EVALUATOR ====", st, flags=re.S)
    st = re.sub(r"# ==== PREFLOP TABLE.*?# ==== END PREFLOP TABLE ====",
                lambda m: "# ==== PREFLOP TABLE (inlined from src/preflop_table.py) ====\n" + pt +
                "\n# ==== END PREFLOP TABLE ====", st, flags=re.S)
    sw_path = os.path.join(SRC, "selector_weights.py")
    if "# ==== SELECTOR WEIGHTS" in st:
        sw = read("selector_weights.py") if os.path.exists(sw_path) else "SEL_W = {}\n"
        st = re.sub(r"# ==== SELECTOR WEIGHTS.*?# ==== END SELECTOR WEIGHTS ====",
                    lambda m: "# ==== SELECTOR WEIGHTS (inlined from src/selector_weights.py) ====\n" + sw +
                    "\n# ==== END SELECTOR WEIGHTS ====", st, flags=re.S)
    assert not re.search(r"^\s*(from|import) (fasteval|preflop_table|selector_weights)", st, re.M), "inline failed"
    # submission hardening: dev-only env hooks (BOTP params, BOTDBG logging) are disabled
    hook = 'try:\n    if os.environ.get("BOTP"):\n        P.update(json.loads(os.environ["BOTP"]))\nexcept Exception:\n    pass\n'
    assert hook in st, "BOTP hook not found"
    st = st.replace(hook, "# (dev-only BOTP parameter override removed by tools/build.py)\n")
    st = st.replace('DBG = bool(os.environ.get("BOTDBG"))', 'DBG = False  # dev-only debug disabled in build')
    for bad in ("exec(", "os.environ", "open(", "subprocess", "socket"):
        assert bad not in st, "forbidden in submission: " + bad
    if a.params:
        # bake parameter overrides in by appending an update right after the P dict
        import json
        anchor = "# (dev-only BOTP parameter override removed by tools/build.py)\n"
        assert anchor in st
        st = st.replace(anchor, anchor + "P.update(%r)\n" % json.loads(a.params))
    ast.parse(st, feature_version=(3, 11))  # must be valid Python 3.11 syntax
    out = os.path.abspath(a.out) if a.out else os.path.join(DIST, "bot.py")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(st)
    if not a.quiet:
        print("wrote %s (%d bytes)" % (out, len(st)))
    if a.snapshot:
        snap = os.path.join(OPPONENTS, "%s_bot.py" % a.snapshot)
        shutil.copy(out, snap)
        print("snapshot ->", snap)


if __name__ == "__main__":
    main()
