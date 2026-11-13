"""Discard Hold'em bot: strategy core (dev version; imports fasteval + preflop_table from src/).

`python3 tools/build.py` inlines both into dist/bot.py (the submission).
Never print to stdout except protocol replies.
"""
import sys, json, time, random

# ==== EVALUATOR (inlined at build) ====
from fasteval import card_to_int, eval7  # noqa: E402
# ==== END EVALUATOR ====

# ==== PREFLOP TABLE (inlined at build) ====
try:
    from preflop_table import PREFLOP_EQ  # dict: class_key -> equity vs random 3-card
except Exception:
    PREFLOP_EQ = {}
# ==== END PREFLOP TABLE ====

import os

# Tunable parameters (dev: override with env BOTP='{"r_oop":0.85}'); defaults are the submission.
P = {
    "r_oop": 0.80,      # equity realization when calling flop/turn out of position
    "r_ip": 0.90,       # ... in position
    "call_margin": 0.02,
    "river_margin": 0.02,
    "raise_thr": 0.78,
    "vthr": 0.64,
    "vthr_station": 0.60,
    "chk_real_ip": 1.0,
    "chk_real_oop": 0.92,
    "bet_sizes": [0.33, 0.66, 1.0, 1.5],
    "bet_edge": 0.0,
    "open_thr": 0.35,
    "time_frac": 0.12,
    "ev_raise": True,
    "fr_mult": 0.7,
    "raise_edge": 0.03,
    "sd_w": 3.0,
    "pf_ev_raise": True,
    "pf_fr_mult": 1.0,  # opp fold-to-our-preflop-raise multiplier
    "pf_real_bb": 1.0,  # preflop call realization out of position (0.92 tested worse)
    "pf_real_btn": 1.0,
    "cmul": 1.0,        # multiplier on MC trial caps
    "nmul": 1.0,        # multiplier on range-candidate counts
    "max_dec_ms": 60.0, # hard cap per decision
}
try:
    if os.environ.get("BOTP"):
        P.update(json.loads(os.environ["BOTP"]))
except Exception:
    pass

DBG = bool(os.environ.get("BOTDBG"))
RANKS = "23456789TJQKA"
SUITS = "shdc"
RV = {r: i for i, r in enumerate(RANKS)}  # 0..12
ALL_CARDS = [r + s for r in RANKS for s in SUITS]
CI = {c: card_to_int(c) for c in ALL_CARDS}
ALL_INTS = [CI[c] for c in ALL_CARDS]


def log(*a):
    try:
        print(*a, file=sys.stderr)
    except Exception:
        pass


# ------------------------------------------------------------------ preflop keys

def preflop_key(cards):
    """Suit-isomorphic class key for 3 hole cards (strings)."""
    cs = sorted(cards, key=lambda c: (-RV[c[0]], c[1]))
    best = None
    # canonical relabel of suits: try all orderings implied by first appearance in rank order
    # Rank-sorted with ties broken over all permutations of equal-rank cards.
    import itertools
    for perm in itertools.permutations(cs):
        if [RV[c[0]] for c in perm] != [RV[c[0]] for c in cs]:
            continue
        m = {}
        key = []
        for c in perm:
            if c[1] not in m:
                m[c[1]] = "abcd"[len(m)]
            key.append(c[0] + m[c[1]])
        k = "".join(key)
        if best is None or k < best:
            best = k
    return best


_PCT = {}


def _build_pct():
    """Map class key -> percentile (fraction of 3-card hands it beats), weighting by combos."""
    if not PREFLOP_EQ:
        return
    items = sorted(PREFLOP_EQ.items(), key=lambda kv: kv[1])
    # combos per class
    counts = {}
    import itertools
    for combo in itertools.combinations(ALL_CARDS, 3):
        k = preflop_key(combo)
        counts[k] = counts.get(k, 0) + 1
    total = float(sum(counts.values()))
    acc = 0.0
    for k, _ in items:
        n = counts.get(k, 0)
        _PCT[k] = (acc + n / 2.0) / total
        acc += n


def preflop_strength(cards):
    """Return (equity_vs_random, percentile) for 3 hole cards."""
    k = preflop_key(cards)
    eq = PREFLOP_EQ.get(k)
    if eq is None:
        eq = crude_preflop(cards)
        return eq, max(0.0, min(1.0, (eq - 0.35) / 0.3))
    return eq, _PCT.get(k, 0.5)


def crude_preflop(cards):
    """Fallback heuristic equity if the table is missing."""
    rs = sorted((RV[c[0]] for c in cards), reverse=True)
    ss = [c[1] for c in cards]
    score = 0.40 + 0.012 * rs[0] + 0.006 * rs[1] + 0.003 * rs[2]
    if rs[0] == rs[1] or rs[1] == rs[2]:
        score += 0.06 + 0.004 * (rs[1])
    if rs[0] == rs[2]:
        score += 0.05
    if len(set(ss)) < 3:
        score += 0.02
    return min(0.75, score)


# ------------------------------------------------------------------ keep heuristic

def keep_score(pair, board_i):
    """Cheap score of keeping int-card pair with the board (>=3 int cards). Higher = better."""
    v = eval7(list(pair) + board_i)
    # draws bonus: flush draw and straight potential (only when board < 5)
    bonus = 0
    if len(board_i) < 5:
        suits = [c & 3 for c in pair] + [c & 3 for c in board_i]
        for s in range(4):
            if suits.count(s) == 4 and (pair[0] & 3 == s or pair[1] & 3 == s):
                bonus += DRAW_BONUS_FLUSH
        ranks = set(c >> 2 for c in pair) | set(c >> 2 for c in board_i)
        if 12 in ranks:
            ranks.add(-1)
        for lo in range(-1, 9):
            have = sum(1 for r in range(lo, lo + 5) if r in ranks)
            if have >= 4 and any((c >> 2) in range(lo, lo + 5) or (lo == -1 and c >> 2 == 12) for c in pair):
                bonus += DRAW_BONUS_STRAIGHT
                break
    return v + bonus


# Filled in once evaluator value scale is known (set in init_eval_scale()).
DRAW_BONUS_FLUSH = 0
DRAW_BONUS_STRAIGHT = 0


def init_eval_scale():
    """Calibrate draw bonuses in eval7 units: a flush draw ~ worth a medium pair."""
    global DRAW_BONUS_FLUSH, DRAW_BONUS_STRAIGHT
    c = CI
    hi = eval7([c["As"], c["Kd"], c["9h"], c["7c"], c["2s"]])
    pair_low = eval7([c["2s"], c["2d"], c["9h"], c["7c"], c["4s"]])
    pair_mid = eval7([c["8s"], c["8d"], c["Ah"], c["7c"], c["4s"]])
    DRAW_BONUS_FLUSH = max(0, pair_mid - hi)
    DRAW_BONUS_STRAIGHT = max(0, (pair_low - hi))


def best_keep(cards3_i, board_i, discard_needed=True):
    """Heuristic best pair of 3 int cards; returns (kept_pair, discarded)."""
    a, b, c = cards3_i
    opts = (((a, b), c), ((a, c), b), ((b, c), a))
    best = None
    bs = None
    for pair, d in opts:
        s = keep_score(pair, board_i)
        if bs is None or s > bs:
            bs, best = s, (pair, d)
    return best


# ------------------------------------------------------------------ opponent model

class OppModel:
    def __init__(self):
        self.hands = 0
        self.vpip = 0
        self.pfr = 0
        self.pf_opps_raise = 0          # opp had a preflop decision
        self.post_aggr = 0              # opp bet/raise postflop
        self.post_acts = 0              # opp postflop actions total
        self.fold_to_bet = 0            # opp folded facing our bet
        self.faced_bet = 0              # opp faced our bet (postflop)
        self.pf_fold_to_raise = 0
        self.pf_faced_raise = 0
        self.showdowns = 0
        self.jams = 0
        self.big_bets = 0               # opp bets >= pot
        self.sd_pct = {"raise": [], "call": []}  # opp hand percentile at streets it raised/called
        # per-street opportunity counts: "bet" = opp acts with no bet outstanding, "raise" = facing our bet
        self.st_opps = {}
        self.st_acts = {}

    def freq(self, street, kind, prior, w=5.0):
        n = self.st_opps.get((street, kind), 0)
        k = self.st_acts.get((street, kind), 0)
        return (k + prior * w) / (n + w)

    def frac_for(self, kind, prior):
        xs = self.sd_pct[kind][-40:]
        n = len(xs)
        if n == 0:
            return prior
        m = sum(xs) / n
        f = max(0.12, min(0.95, 2.0 * (1.0 - m)))
        w = n / (n + P["sd_w"])
        return w * f + (1 - w) * prior

    def learn_showdown(self, rng, history, board, opp_hole, opp_discard):
        """Percentile of opp's kept pair on each postflop street where it raised/called."""
        try:
            b_i = [CI[c] for c in board]
            oh = [CI[c] for c in opp_hole]
            if len(oh) != 2 or len(b_i) != 5:
                return
            used = set(b_i) | set(oh)
            if opp_discard:
                used.add(CI[opp_discard])
            avail = [c for c in ALL_INTS if c not in used]
            nb = {"flop": 3, "turn": 4, "river": 5}
            seen = set()
            for h in history:
                if h.get("actor") != "opp":
                    continue
                st, a = h.get("street"), h.get("action")
                if st not in nb or a not in ("raise", "call") or (st, a) in seen:
                    continue
                seen.add((st, a))
                bb = b_i[:nb[st]]
                mine = keep_score(oh, bb)
                below = 0
                N = 60
                for _ in range(N):
                    x = rng.sample(avail, 2)
                    if keep_score(x, bb) < mine:
                        below += 1
                self.sd_pct[a].append(below / float(N))
        except Exception as ex:
            log("learn error", repr(ex))

    def _r(self, num, den, prior, w=8.0):
        return (num + prior * w) / (den + w)

    @property
    def vpip_rate(self):
        return self._r(self.vpip, self.hands, 0.6)

    @property
    def pfr_rate(self):
        return self._r(self.pfr, self.hands, 0.35)

    @property
    def aggr_rate(self):
        return self._r(self.post_aggr, self.post_acts, 0.35)

    @property
    def fold_to_bet_rate(self):
        return self._r(self.fold_to_bet, self.faced_bet, 0.4)

    @property
    def pf_fold_rate(self):
        return self._r(self.pf_fold_to_raise, self.pf_faced_raise, 0.35)

    def update_from_hand(self, history):
        self.hands += 1
        pf_v = False
        pf_r = False
        last_actor_bet = None  # who made the last bet/raise on the current street
        cur_street = None
        for h in history:
            st = h.get("street")
            if st != cur_street:
                cur_street = st
                last_actor_bet = "opp_blind" if st == "preflop" else None
            actor = h.get("actor")
            act = h.get("action")
            if actor == "opp":
                if st == "preflop":
                    if act in ("call", "raise"):
                        pf_v = True
                    if act == "raise":
                        pf_r = True
                        if h.get("amount", 0) >= 150:
                            self.jams += 1
                    if last_actor_bet == "me":
                        self.pf_faced_raise += 1
                        if act == "fold":
                            self.pf_fold_to_raise += 1
                else:
                    self.post_acts += 1
                    if act == "raise":
                        self.post_aggr += 1
                    kind = "raise" if last_actor_bet == "me" else ("bet" if last_actor_bet is None else None)
                    if kind:
                        self.st_opps[(st, kind)] = self.st_opps.get((st, kind), 0) + 1
                        if act == "raise":
                            self.st_acts[(st, kind)] = self.st_acts.get((st, kind), 0) + 1
                    if last_actor_bet == "me":
                        self.faced_bet += 1
                        if act == "fold":
                            self.fold_to_bet += 1
            if act == "raise":
                last_actor_bet = actor
        if pf_v:
            self.vpip += 1
        if pf_r:
            self.pfr += 1


# ------------------------------------------------------------------ range sampling

class RangeSampler:
    """Builds a weighted list of opponent 2-card holdings for a spot, then samples it.

    Opp holdings are generated as 3-card hands; post-flop the opponent keeps the
    heuristic-best pair (or, after the discard, the pair consistent with its revealed discard).
    """

    def __init__(self, rng, hero_i, board_i, dead_i, opp_discard_i, keep_frac,
                 pre_discard, n_cand=260, opp_pf_frac=1.0):
        self.rng = rng
        used = set(hero_i) | set(board_i) | set(dead_i)
        if opp_discard_i is not None:
            used.add(opp_discard_i)
        avail = [c for c in ALL_INTS if c not in used]
        self.avail = avail
        cands = []
        tries = 0
        while len(cands) < n_cand and tries < n_cand * 6:
            tries += 1
            if opp_discard_i is not None:
                two = rng.sample(avail, 2)
                # consistency: would a sensible opp discard opp_discard_i from these 3 on the flop?
                if len(board_i) >= 3:
                    kept, d = best_keep((two[0], two[1], opp_discard_i), board_i[:3])
                    if d != opp_discard_i and rng.random() > 0.25:
                        continue
                pair = (two[0], two[1])
            else:
                three = rng.sample(avail, 3)
                if opp_pf_frac < 0.999:
                    ks = [ALL_CARDS[ALL_INTS.index(x)] if False else INT2STR[x] for x in three]
                    _, pct = preflop_strength(ks)
                    if pct < 1.0 - opp_pf_frac and rng.random() > 0.1:
                        continue
                if len(board_i) >= 3:
                    pair, _ = best_keep(three, board_i[:3] if not pre_discard else board_i)
                else:
                    pair = tuple(three)  # preflop: keep all 3, resolved at showdown
            cands.append(pair)
        if not cands:
            cands = [tuple(rng.sample(avail, 2))]
        # Action-weighted range: keep the top keep_frac by current made-hand strength.
        if keep_frac < 0.999 and len(board_i) >= 3:
            scored = sorted(cands, key=lambda p: keep_score(p if len(p) == 2 else p[:2], board_i))
            k = max(8, int(len(scored) * keep_frac))
            cands = scored[-k:]
        self.cands = cands


INT2STR = {}


def _init_int2str():
    for c in ALL_CARDS:
        INT2STR[CI[c]] = c


_init_int2str()


def best_of(cards, board5):
    """Best eval over keeping any 2 of the given (2 or 3) cards with 5-card board."""
    if len(cards) == 2:
        return eval7([cards[0], cards[1]] + board5)
    a, b, c = cards
    return max(eval7([a, b] + board5), eval7([a, c] + board5), eval7([b, c] + board5))


def mc_equity(rng, hero_sets, board_i, sampler, deadline, max_trials=4000, min_trials=60):
    """CRN Monte Carlo: equity for each hero card-set in hero_sets vs sampler.cands.

    hero_sets: list of tuples (2 or 3 int cards). Dead cards are already excluded in sampler.avail
    (sampler.avail excludes all hero cards in the union).
    Returns list of equities.
    """
    need = 5 - len(board_i)
    avail = sampler.avail
    cands = sampler.cands
    nset = len(hero_sets)
    wins = [0.0] * nset
    trials = 0
    t_check = time.perf_counter
    hero_lists = [list(h) for h in hero_sets]
    while trials < max_trials:
        if trials >= min_trials and (trials & 31) == 0 and t_check() > deadline:
            break
        opp = cands[rng.randrange(len(cands))]
        if need:
            runout = []
            while len(runout) < need:
                x = avail[rng.randrange(len(avail))]
                if x in opp or x in runout:
                    continue
                runout.append(x)
            b5 = board_i + runout
        else:
            b5 = board_i
        ov = best_of(opp, b5)
        for i in range(nset):
            hv = best_of(hero_lists[i], b5)
            if hv > ov:
                wins[i] += 1.0
            elif hv == ov:
                wins[i] += 0.5
        trials += 1
    if trials == 0:
        return [0.5] * nset
    return [w / trials for w in wins]


# ------------------------------------------------------------------ the bot

class Bot:
    def __init__(self):
        self.rng = random.Random()
        self.opp = OppModel()
        self.total_hands = 200
        self.time_bank_ms = 30000
        self.hand_id = 0
        self.seat = None
        self.hole = []
        self.my_discard = None
        self.opp_discard = None
        self.cum_net = 0
        self.street_eq_cache = {}

    # ---------------- time
    def budget(self, req):
        tl = req.get("time_left_ms", 10000)
        left_hands = max(1, self.total_hands - self.hand_id + 1)
        per_hand = tl / left_hands
        b = per_hand * P["time_frac"]  # ms for this decision (up to ~7 decisions/hand + reserve)
        frac_left = tl / float(max(1, self.time_bank_ms))
        hands_frac = left_hands / float(max(1, self.total_hands))
        if frac_left < 0.5 * hands_frac:  # spending too fast: go cheap
            b *= 0.4
        if tl < 3000:
            b = min(b, 3)
        return max(1.5, min(b, P["max_dec_ms"])) / 1000.0

    @staticmethod
    def ncand(deadline, full):
        """Candidate count: ~30us each; use at most ~35% of the remaining budget."""
        rem = deadline - time.perf_counter()
        return int(max(20, min(full, rem * 0.35 / 30e-6)))

    # ---------------- messages
    def on_init(self, m):
        self.total_hands = int(m.get("total_hands", 200))
        self.time_bank_ms = int(m.get("time_bank_ms", 30000))

    def on_new_hand(self, m):
        self.hand_id = int(m.get("hand_id", self.hand_id + 1))
        self.seat = m.get("seat")
        self.hole = list(m.get("hole_cards", []))
        self.my_discard = None
        self.opp_discard = None
        self.street_eq_cache = {}

    def on_discard_result(self, m):
        self.my_discard = m.get("my_discard")
        self.opp_discard = m.get("opp_discard")
        hc = m.get("hole_cards")
        if hc:
            self.hole = list(hc)

    def on_hand_result(self, m):
        try:
            self.cum_net += int(m.get("net", 0))
            self.opp.update_from_hand(m.get("history") or [])
            if m.get("opp_hole_cards"):
                self.opp.showdowns += 1
                self.opp.learn_showdown(self.rng, m.get("history") or [], m.get("board") or [],
                                        m.get("opp_hole_cards"), m.get("opp_discard"))
        except Exception:
            pass

    # ---------------- helpers
    @staticmethod
    def raise_to(req, amt):
        lo, hi = req["min_raise"], req["max_raise"]
        a = int(max(lo, min(hi, round(amt))))
        # avoid leaving a tiny stack behind: if raise commits most of the stack, jam
        my_total = req["my_bet"] + req["my_stack"]
        if a > 0.6 * my_total + req["my_bet"] * 0:
            if a >= 0.6 * hi:
                a = hi
        return {"type": "action", "action": "raise", "amount": a}

    @staticmethod
    def act(a):
        return {"type": "action", "action": a}

    def opp_keep_frac(self, req):
        """How much of opp's range survives given its betting this hand (postflop)."""
        frac = 1.0
        om = self.opp
        call_f = om.frac_for("call", 0.8 if om.vpip_rate < 0.8 else 0.92)
        last = None
        cur = None
        for h in req.get("history", []):
            st = h.get("street")
            if st != cur:
                cur, last = st, None
            a = h.get("action")
            if h.get("actor") == "opp" and st != "preflop":
                if a == "raise":
                    if last == "me":   # raise facing our bet
                        prior = max(0.08, min(0.9, om.freq(st, "raise", 0.15)))
                    else:              # bet when checked to / acting first
                        prior = max(0.12, min(0.95, om.freq(st, "bet", 0.4)))
                    frac *= om.frac_for("raise", prior)
                elif a == "call":
                    frac *= call_f
            if a == "raise":
                last = h.get("actor")
        return max(0.06, frac)

    def opp_pf_frac(self, req):
        """Fraction of 3-card hands opp plays given its preflop actions."""
        frac = 1.0
        for h in req.get("history", []):
            if h.get("actor") == "opp" and h.get("street") == "preflop":
                if h.get("action") == "raise":
                    frac *= max(0.15, min(1.0, self.opp.pfr_rate * 1.1))
                    if h.get("amount", 0) >= 100:
                        frac *= max(0.3, min(1.0, 0.4 + self.opp.jams / max(1.0, self.opp.hands) * 3))
                elif h.get("action") == "call":
                    frac *= max(0.4, min(1.0, self.opp.vpip_rate * 1.1))
        return max(0.05, frac)

    # ---------------- preflop
    def preflop(self, req, deadline):
        legal = req["legal_actions"]
        eq, pct = preflop_strength(self.hole)
        to_call = req["to_call"]
        pot = req["pot"]
        opp_bet = req["opp_bet"]
        can_raise = "raise" in legal
        r = self.rng.random()

        if to_call == 0:
            # BB after a limp (or reopened) -> raise strong hands, else check
            if can_raise and pct > 0.62:
                return self.raise_to(req, opp_bet + max(6, pot))
            return self.act("check")

        # facing a bet
        if opp_bet <= 2:
            # we are the button, first action
            pfr = self.opp.pf_fold_rate
            thr = max(0.08, min(0.55, P["open_thr"] - 0.6 * (pfr - 0.35)))
            if can_raise and pct > thr:
                size = 5 if pfr > 0.5 else 6
                return self.raise_to(req, size)
            if pct > 0.12 or self.opp.pfr_rate < 0.2:
                return self.act("call")
            return self.act("fold")

        # facing a real raise: EV of fold / call / 3-bet / jam vs opp's preflop range
        frac = self.opp_pf_frac(req)
        hero_i = tuple(CI[c] for c in self.hole)
        t_now = time.perf_counter()
        d1 = t_now + (deadline - t_now) * 0.5
        sampler = RangeSampler(self.rng, hero_i, [], [], None, 1.0, True,
                               n_cand=self.ncand(d1, int(150 * P["nmul"])), opp_pf_frac=frac)
        e = mc_equity(self.rng, [hero_i], [], sampler, d1, max_trials=int(600 * P["cmul"]), min_trials=60)[0]
        all_in_call = req["my_stack"] <= to_call
        real = 1.0 if all_in_call else (P["pf_real_bb"] if self.seat == "big_blind" else P["pf_real_btn"])
        ev_call = e * real * (pot + to_call) - to_call
        best_ev = max(ev_call, 0.0)
        best = self.act("call") if ev_call > 0 else self.act("fold")
        if can_raise and req.get("raises_left", 0) > 0 and P["pf_ev_raise"]:
            f = max(0.02, min(0.85, self.opp.pf_fold_rate * P["pf_fr_mult"]))
            sub = RangeSampler(self.rng, hero_i, [], [], None, 1.0, True,
                               n_cand=self.ncand(deadline, int(120 * P["nmul"])),
                               opp_pf_frac=max(0.03, frac * (1.0 - f)))
            e_c = mc_equity(self.rng, [hero_i], [], sub, deadline,
                            max_trials=int(500 * P["cmul"]), min_trials=40)[0]
            lo, hi = req["min_raise"], req["max_raise"]
            my_bet = req["my_bet"]
            sizes = [hi]
            if opp_bet * 3 < 0.45 * hi:
                sizes.append(opp_bet * 3)
            for R in sizes:
                R = int(max(lo, min(hi, R)))
                add = R - my_bet
                fin = pot + add + (R - opp_bet)
                rr = 1.0 if R >= hi else real
                ev = f * pot + (1.0 - f) * (e_c * rr * fin - add)
                if ev > best_ev + P["raise_edge"] * pot:
                    best_ev, best = ev, self.raise_to(req, R)
        elif can_raise and e > 0.62 and req.get("raises_left", 0) > 0:
            if e > 0.70 or opp_bet >= 60:
                return self.raise_to(req, req["max_raise"])
            return self.raise_to(req, opp_bet * 3)
        return best

    # ---------------- postflop
    def postflop_equity(self, req, deadline):
        board = req["board"]
        board_i = [CI[c] for c in board]
        hero_i = tuple(CI[c] for c in self.hole)
        dead = []
        if self.my_discard:
            dead.append(CI[self.my_discard])
        od = CI[self.opp_discard] if self.opp_discard else None
        pre_discard = len(self.hole) == 3
        kf = self.opp_keep_frac(req)
        pf = self.opp_pf_frac(req)
        sampler = RangeSampler(self.rng, hero_i, board_i, dead, od, kf, False,
                               n_cand=self.ncand(deadline, int(200 * P["nmul"])), opp_pf_frac=max(pf, 0.25))
        if pre_discard:
            a, b, c = hero_i
            sets = [(a, b), (a, c), (b, c)]
            eqs = mc_equity(self.rng, sets, board_i, sampler, deadline, max_trials=int(3000 * P["cmul"]))
            self._spot = (sets, board_i, sampler)
            return max(eqs), sets, eqs
        eqs = mc_equity(self.rng, [hero_i], board_i, sampler, deadline, max_trials=int(3000 * P["cmul"]))
        self._spot = ([hero_i], board_i, sampler)
        return eqs[0], None, eqs

    def postflop(self, req, deadline):
        legal = req["legal_actions"]
        street = req["street"]
        to_call = req["to_call"]
        t_now = time.perf_counter()
        d1 = t_now + (deadline - t_now) * 0.6 if "raise" in legal else deadline
        e, _, _ = self.postflop_equity(req, d1)
        pot = req["pot"]
        can_raise = "raise" in legal
        r = self.rng.random()
        station = self.opp.fold_to_bet_rate < 0.25 and self.opp.faced_bet >= 8
        folder = self.opp.fold_to_bet_rate > 0.55 and self.opp.faced_bet >= 8
        maniac = self.opp.aggr_rate > 0.6 and self.opp.post_acts >= 15

        if to_call == 0:
            if not can_raise:
                return self.act("check")
            return self.bet_or_check(req, deadline, e, street, station, maniac)

        # facing a bet
        po = to_call / float(pot + to_call)
        margin = P["call_margin"] if not maniac else -0.02
        e_call = e
        if street == "river":
            margin += P["river_margin"]
        elif req["my_stack"] > to_call:  # more betting can follow: discount equity realization
            e_call = e * (P["r_oop"] if self.seat == "big_blind" else P["r_ip"])
        ev_call = e_call * (pot + to_call) - to_call - margin * (pot + to_call)
        if DBG and street == "river":
            log("RIVER h%d e=%.3f kf=%.3f po=%.3f ev_call=%.1f sd_r=%s ftb=%.2f" % (
                self.hand_id, e, self.opp_keep_frac(req), po, ev_call,
                ["%.2f" % x for x in self.opp.sd_pct["raise"][-5:]], self.opp.fold_to_bet_rate))
        if can_raise and P["ev_raise"]:
            amt = self.raise_ev(req, deadline, max(ev_call, 0.0))
            if amt is not None:
                return self.raise_to(req, amt)
        elif can_raise and e > P["raise_thr"]:
            if e > 0.9 or req["max_raise"] <= pot * 1.5:
                return self.raise_to(req, req["max_raise"])
            return self.raise_to(req, req["opp_bet"] * 2.5 + pot * 0.25)
        if ev_call > 0:
            return self.act("call")
        return self.act("fold")

    def raise_ev(self, req, deadline, ev_best):
        """Return a raise-to amount if raising beats ev_best (call/fold EV), else None."""
        pot = float(req["pot"])
        my_bet, opp_bet = req["my_bet"], req["opp_bet"]
        sets, board_i, sampler = self._spot
        f = max(0.02, min(0.8, self.opp.fold_to_bet_rate * P["fr_mult"]))
        cands = sorted(sampler.cands, key=lambda p: keep_score(p, board_i))
        k = max(6, int(len(cands) * (1.0 - f) + 0.5))
        sub = RangeSampler.__new__(RangeSampler)
        sub.rng, sub.avail, sub.cands = sampler.rng, sampler.avail, cands[-k:]
        e_c = max(mc_equity(self.rng, sets, board_i, sub, deadline, max_trials=int(1500 * P["cmul"]), min_trials=40))
        best_amt = None
        lo, hi = req["min_raise"], req["max_raise"]
        for R in (opp_bet + (pot + (opp_bet - my_bet)) * 0.8, hi):
            R = max(lo, min(hi, int(R)))
            add = R - my_bet               # our chips added from now
            final_pot = pot + add + (R - opp_bet)
            ev = f * pot + (1.0 - f) * (e_c * final_pot - add)
            if ev > ev_best + P["raise_edge"] * pot:
                ev_best, best_amt = ev, R
        return best_amt

    def bet_or_check(self, req, deadline, e, street, station, maniac):
        """EV comparison of betting sizes vs checking, using opp fold rate and calling range."""
        pot = float(req["pot"])
        sets, board_i, sampler = self._spot
        f = max(0.03, min(0.85, self.opp.fold_to_bet_rate))
        if station:
            f = min(f, 0.15)
        # opponent's calling range: the top (1-f) of its current range by made strength
        cands = sorted(sampler.cands, key=lambda p: keep_score(p, board_i))
        k = max(6, int(len(cands) * (1.0 - f) + 0.5))
        sub = RangeSampler.__new__(RangeSampler)
        sub.rng, sub.avail, sub.cands = sampler.rng, sampler.avail, cands[-k:]
        e_c = max(mc_equity(self.rng, sets, board_i, sub, deadline, max_trials=int(1500 * P["cmul"]), min_trials=40))
        # checking: we realize roughly our equity of the current pot (less OOP, opp may bet/bluff)
        ev_check = e * pot * (P["chk_real_ip"] if self.seat == "button" else P["chk_real_oop"])
        if maniac and self.seat == "big_blind":
            ev_check = e * pot * 1.05  # maniac bets into us: checking keeps its bluffs in
        best_ev, best_amt = ev_check, None
        stack = req["max_raise"] - req["my_bet"]
        for frac in P["bet_sizes"]:
            b = min(pot * frac, stack)
            if b < 2:
                continue
            ev = f * pot + (1.0 - f) * (e_c * (pot + 2.0 * b) - b)
            if ev > best_ev + P["bet_edge"] * pot:
                best_ev, best_amt = ev, b
        if best_amt is None:
            return self.act("check")
        return self.raise_to(req, req["my_bet"] + best_amt)

    # ---------------- discard
    def discard(self, req, deadline):
        hole = req.get("hole_cards") or self.hole
        board = req.get("board") or []
        hero_i = tuple(CI[c] for c in hole)
        board_i = [CI[c] for c in board]
        a, b, c = hero_i
        sets = [(a, b), (a, c), (b, c)]
        drops = [hole[2], hole[1], hole[0]]
        kf = self.opp_keep_frac(req)
        pf = self.opp_pf_frac({"history": self.hist_cache})
        sampler = RangeSampler(self.rng, hero_i, board_i, [], None, kf, False,
                               n_cand=self.ncand(deadline, int(220 * P["nmul"])), opp_pf_frac=max(pf, 0.25))
        eqs = mc_equity(self.rng, sets, board_i, sampler, deadline, max_trials=int(4000 * P["cmul"]), min_trials=150)
        i = max(range(3), key=lambda k: eqs[k])
        return {"type": "discard", "card": drops[i]}

    # ---------------- safe dispatch
    def handle_action(self, req):
        t0 = time.perf_counter()
        deadline = t0 + self.budget(req)
        self.hist_cache = req.get("history", [])
        try:
            if req.get("hand_id") is not None and req["hand_id"] != self.hand_id:
                pass
            if req.get("street") == "preflop":
                out = self.preflop(req, deadline)
            else:
                out = self.postflop(req, deadline)
            return self.validate(req, out)
        except Exception as ex:  # never crash
            log("action error", repr(ex))
            return self.fallback(req)

    def handle_discard(self, req):
        t0 = time.perf_counter()
        deadline = t0 + self.budget(req) * 1.5
        try:
            out = self.discard(req, deadline)
            if out.get("card") in (req.get("hole_cards") or []):
                return out
        except Exception as ex:
            log("discard error", repr(ex))
        hole = req.get("hole_cards") or self.hole
        worst = min(hole, key=lambda c: RV.get(c[0], 0))
        return {"type": "discard", "card": worst}

    def validate(self, req, out):
        legal = req.get("legal_actions", [])
        a = out.get("action")
        if a not in legal:
            return self.fallback(req)
        if a == "raise":
            amt = out.get("amount")
            if not isinstance(amt, int) or not (req["min_raise"] <= amt <= req["max_raise"]):
                return self.fallback(req)
        return out

    @staticmethod
    def fallback(req):
        legal = req.get("legal_actions", [])
        if "check" in legal:
            return {"type": "action", "action": "check"}
        if "call" in legal and req.get("to_call", 99) <= 2:
            return {"type": "action", "action": "call"}
        return {"type": "action", "action": "fold"}


def main():
    bot = Bot()
    bot.hist_cache = []
    try:
        init_eval_scale()
    except Exception as ex:
        log("init scale error", repr(ex))
    try:
        _build_pct()
    except Exception as ex:
        log("pct error", repr(ex))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        t = msg.get("type")
        reply = None
        try:
            if t == "action_request":
                reply = bot.handle_action(msg)
            elif t == "discard_request":
                reply = bot.handle_discard(msg)
            elif t == "match_end":
                break
            elif t == "init":
                bot.on_init(msg)
            elif t == "new_hand":
                bot.on_new_hand(msg)
            elif t == "discard_result":
                bot.on_discard_result(msg)
            elif t == "hand_result":
                bot.on_hand_result(msg)
        except Exception as ex:
            log("handler error", repr(ex))
            if t == "action_request":
                reply = Bot.fallback(msg)
            elif t == "discard_request":
                hole = msg.get("hole_cards") or ["2s"]
                reply = {"type": "discard", "card": min(hole, key=lambda c: RV.get(c[0], 0))}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
