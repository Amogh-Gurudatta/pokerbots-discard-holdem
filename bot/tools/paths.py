"""Shared paths and bot-name resolution for the dev tools."""
import os
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)                      # bot/
PROJECT = os.path.dirname(ROOT)                    # Pokerbots/
KIT = os.path.join(PROJECT, "Starter Kit")         # organisers' kit (kept pristine)
ENGINE = os.path.join(KIT, "engine.py")
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")
OPPONENTS = os.path.join(ROOT, "opponents")
LOGS = os.path.join(ROOT, "logs")
RESULTS_CSV = os.path.join(LOGS, "results.csv")

ALIASES = {
    "dev": os.path.join(SRC, "strategy.py"),     # live dev version (imports fasteval/preflop_table)
    "dist": os.path.join(DIST, "bot.py"),        # built single-file submission
}


def resolve_bot(name):
    """Accept a path, an alias (dev/dist), or a bot name in opponents/ or the kit's example_bots/."""
    if name in ALIASES:
        return ALIASES[name]
    if os.path.isfile(name):
        return os.path.abspath(name)
    for d in (OPPONENTS, os.path.join(KIT, "example_bots")):
        for cand in (name, name + ".py", name + "_bot.py"):
            p = os.path.join(d, cand)
            if os.path.isfile(p):
                return p
    raise SystemExit("unknown bot: %s" % name)


def short(name):
    """Short label for a bot name/path (for directory names and tables)."""
    b = os.path.basename(name)
    return b[:-3] if b.endswith(".py") else b


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")
