"""Inline src/fasteval.py + src/preflop_table.py into src/strategy.py -> dist/bot.py.

Usage: python3 tools/build.py [--snapshot vN]
  --snapshot vN   also copy the build to opponents/vN_bot.py (frozen sparring version)
"""
import argparse, ast, os, re, shutil

from paths import SRC, DIST, OPPONENTS


def read(name):
    with open(os.path.join(SRC, name)) as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot")
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
    assert not re.search(r"^\s*(from|import) (fasteval|preflop_table)", st, re.M), "inline failed"
    # submission hardening: dev-only env hooks (BOTP params, BOTDBG logging) are disabled
    hook = 'try:\n    if os.environ.get("BOTP"):\n        P.update(json.loads(os.environ["BOTP"]))\nexcept Exception:\n    pass\n'
    assert hook in st, "BOTP hook not found"
    st = st.replace(hook, "# (dev-only BOTP parameter override removed by tools/build.py)\n")
    st = st.replace('DBG = bool(os.environ.get("BOTDBG"))', 'DBG = False  # dev-only debug disabled in build')
    for bad in ("exec(", "os.environ", "open(", "subprocess", "socket"):
        assert bad not in st, "forbidden in submission: " + bad
    ast.parse(st, feature_version=(3, 11))  # must be valid Python 3.11 syntax
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "bot.py")
    with open(out, "w") as f:
        f.write(st)
    print("wrote %s (%d bytes)" % (out, len(st)))
    if a.snapshot:
        snap = os.path.join(OPPONENTS, "%s_bot.py" % a.snapshot)
        shutil.copy(out, snap)
        print("snapshot ->", snap)


if __name__ == "__main__":
    main()
