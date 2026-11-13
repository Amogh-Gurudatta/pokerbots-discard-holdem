"""shark_bot.py -- balanced TAG/LAG sparring opponent (self-contained, stdlib only).

Evaluator/equity code below is copied from fasteval.py; strategy starts at "SHARK STRATEGY".
"""
import random
import time
from itertools import combinations_with_replacement as _cwr

# ---------------------------------------------------------------- cards

RANKS = "23456789TJQKA"
SUITS = "shdc"
_C2I = {r + s: ri * 4 + si for ri, r in enumerate(RANKS) for si, s in enumerate(SUITS)}
_I2C = [r + s for r in RANKS for s in SUITS]
DECK = list(range(52))


def card_to_int(s):
    """'As' -> 48.  rank_index*4 + suit_index."""
    return _C2I[s]


def int_to_card(c):
    return _I2C[c]


def cards_to_ints(strs):
    return [_C2I[s] for s in strs]


def ints_to_cards(ints):
    return [_I2C[c] for c in ints]


def card_rank(c):
    return c >> 2


def card_suit(c):
    return c & 3


# ---------------------------------------------------------------- tables

_M = (1 << 39) - 1                                   # rank-counter mask
_SUITSHIFT = 39
_OFFS = 3 * sum(1 << (_SUITSHIFT + 4 * s) for s in range(4))  # bias suit fields by 3
_FM = sum(8 << (_SUITSHIFT + 4 * s) for s in range(4))        # "suit count >= 5" bits
# packed value of every card (index = card int)
_PK = [(1 << (3 * (c >> 2))) + (1 << (_SUITSHIFT + 4 * (c & 3))) for c in range(52)]
_P2C = {p: c for c, p in enumerate(_PK)}

_WHEEL = 0b1000000001111  # A,2,3,4,5 as a 13-bit rank mask (bit0 = '2', bit12 = 'A')


def _build_straight_and_flush():
    # STR[mask] = high-rank index of the best straight in the mask, or -1
    straight = [-1] * 8192
    windows = [(hi, 0b11111 << (hi - 4)) for hi in range(4, 13)]
    for mask in range(8192):
        if mask & (mask >> 1) & (mask >> 2) & (mask >> 3) & (mask >> 4):
            for hi in range(12, 3, -1):
                w = 0b11111 << (hi - 4)
                if mask & w == w:
                    straight[mask] = hi
                    break
        elif mask & _WHEEL == _WHEEL:
            straight[mask] = 3
    # FL[mask]: score of a flush whose suited cards have rank mask `mask` (>=5 bits)
    fl = [0] * 8192
    for mask in range(8192):
        if bin(mask).count("1") < 5:
            continue
        sh = straight[mask]
        if sh >= 0:
            fl[mask] = (8 << 20) | sh
            continue
        top = []
        m = mask
        for r in range(12, -1, -1):
            if m >> r & 1:
                top.append(r)
                if len(top) == 5:
                    break
        fl[mask] = (5 << 20) | (top[0] << 16) | (top[1] << 12) | (top[2] << 8) | (top[3] << 4) | top[4]
    return straight, fl


_STR, _FL = _build_straight_and_flush()


def _score_tuple(t):
    """Non-flush score of the sorted-ascending rank tuple t (5..7 ranks, rank idx)."""
    STR = _STR
    quads = []
    trips = []
    pairs = []
    singles = []
    mask = 0
    i = len(t) - 1
    while i >= 0:
        r = t[i]
        j = i
        while j > 0 and t[j - 1] == r:
            j -= 1
        c = i - j + 1
        mask |= 1 << r
        if c == 1:
            singles.append(r)
        elif c == 2:
            pairs.append(r)
        elif c == 3:
            trips.append(r)
        else:
            quads.append(r)
        i = j - 1
    if quads:
        rest = trips[:1] + pairs[:1] + singles[:1]
        return (7 << 20) | (quads[0] << 16) | (max(rest) << 12)
    if trips:
        if len(trips) > 1 or pairs:
            p = max(trips[1:2] + pairs[:1])
            return (6 << 20) | (trips[0] << 16) | (p << 12)
    sh = STR[mask]
    if sh >= 0:
        return (4 << 20) | sh
    if trips:
        s = singles
        return (3 << 20) | (trips[0] << 16) | (s[0] << 12) | (s[1] << 8)
    if len(pairs) >= 2:
        kick = max(pairs[2:3] + singles[:1])
        return (2 << 20) | (pairs[0] << 16) | (pairs[1] << 12) | (kick << 8)
    if pairs:
        s = singles
        return (1 << 20) | (pairs[0] << 16) | (s[0] << 12) | (s[1] << 8) | (s[2] << 4)
    s = singles
    return (s[0] << 16) | (s[1] << 12) | (s[2] << 8) | (s[3] << 4) | s[4]


def _build_rank_table():
    """Dict: packed rank-counter key -> score, prefilled for all legal 7-card multisets."""
    pw = [1 << (3 * r) for r in range(13)]
    score = _score_tuple
    d = {}
    for t in _cwr(range(13), 7):
        # reject 5+ of a kind (impossible in a real deck)
        if t[0] == t[4] or t[1] == t[5] or t[2] == t[6]:
            continue
        d[pw[t[0]] + pw[t[1]] + pw[t[2]] + pw[t[3]] + pw[t[4]] + pw[t[5]] + pw[t[6]]] = score(t)
    return d


_RS = _build_rank_table()


def _slow_key(key):
    """Score for a rank key not in the prefilled (7-card) table, i.e. 5 or 6 cards."""
    t = []
    r = 0
    kk = key
    while kk:
        c = kk & 7
        if c:
            t.extend([r] * c)
        kk >>= 3
        r += 1
    v = _score_tuple(t)
    _RS[key] = v
    return v


def _flush_score(cards):
    """Score for a hand known to contain >=5 cards of one suit (cards = int list)."""
    cnt = [0, 0, 0, 0]
    for c in cards:
        cnt[c & 3] += 1
    s = cnt.index(max(cnt))
    m = 0
    for c in cards:
        if c & 3 == s:
            m |= 1 << (c >> 2)
    return _FL[m]


# ---------------------------------------------------------------- eval7

def eval7(cards, _sum=sum, _map=map, _g=_PK.__getitem__, _OFFS=_OFFS, _FM=_FM,
          _M=_M, _RS=_RS):
    """Score of the best 5-card hand among 5, 6 or 7 int cards (higher = better)."""
    k = _sum(_map(_g, cards), _OFFS)
    if k & _FM:
        return _flush_score(cards)
    k &= _M
    try:
        return _RS[k]
    except KeyError:
        return _slow_key(k)


def hand_category(score):
    """0 high card, 1 pair, 2 two pair, 3 trips, 4 straight, 5 flush,
    6 full house, 7 quads, 8 straight flush."""
    return score >> 20


# ---------------------------------------------------------------- equity

_MAX_RESAMPLE = 200


def _slow_list(plist):
    """Slow-path evaluator for a tuple of packed cards (only reached on flushes)."""
    return eval7([_P2C[p] for p in plist])


_RUNNERS = {}


def _gen_runner(k, nh, mode):
    """Build (and cache) an unrolled trial loop.

    k     : number of board cards still to come (0..5)
    nh    : number of hero 2-card choices (1, or 3 when hero holds 3 cards)
    mode  : 'A' opponent has exactly 2 random cards, hero choices scored with
                common random numbers (one tally per choice);
            'B' opponent has 3 random cards and keeps its best 2 on the final
                board; hero takes the best of its choices; a single tally.
    """
    key = (k, nh, mode)
    f = _RUNNERS.get(key)
    if f is not None:
        return f
    m = k + (2 if mode == "A" else 3)
    runout = "(" + "".join("a%d, " % i for i in range(k)) + ")"
    L = []
    w = L.append
    w("def run(deck, nd, trials, rnd, C, HS, HP, BP, RS, slow, M, FM):")
    for i in range(m):
        w("    n%d = nd - %d" % (i, i))
    for i in range(nh):
        w("    h%d = HS[%d]" % (i, i))
        w("    hp%d = HP[%d]" % (i, i))
    nt = nh if mode == "A" else 1
    for i in range(nt):
        w("    t%d = 0" % i)
    w("    for _ in range(trials):")
    for i in range(m):
        if i == 0:
            w("        j = int(rnd() * n0)")
            w("        a0 = deck[j]; deck[j] = deck[0]; deck[0] = a0")
        else:
            w("        j = %d + int(rnd() * n%d)" % (i, i))
            w("        a%d = deck[j]; deck[j] = deck[%d]; deck[%d] = a%d" % (i, i, i, i))
    if k:
        w("        b = C + " + " + ".join("a%d" % i for i in range(k)))
    else:
        w("        b = C")

    def ev(var, expr_sum, cards_expr):
        w("        x = %s" % expr_sum)
        w("        %s = RS[x & M] if not x & FM else slow(%s)" % (var, cards_expr))

    if mode == "A":
        ev("ov", "b + a%d + a%d" % (k, k + 1), "(a%d, a%d) + BP + %s" % (k, k + 1, runout))
    else:
        pairs = [(k, k + 1), (k, k + 2), (k + 1, k + 2)]
        for idx, (p, q) in enumerate(pairs):
            ev("o%d" % idx, "b + a%d + a%d" % (p, q), "(a%d, a%d) + BP + %s" % (p, q, runout))
        w("        ov = o0 if o0 > o1 else o1")
        w("        if o2 > ov: ov = o2")
    for i in range(nh):
        ev("hv%d" % i, "b + h%d" % i, "hp%d + BP + %s" % (i, runout))
    if mode == "A":
        for i in range(nh):
            w("        if hv%d > ov: t%d += 2" % (i, i))
            w("        elif hv%d == ov: t%d += 1" % (i, i))
    else:
        w("        hv = hv0")
        for i in range(1, nh):
            w("        if hv%d > hv: hv = hv%d" % (i, i))
        w("        if hv > ov: t0 += 2")
        w("        elif hv == ov: t0 += 1")
    w("    return [" + ", ".join("t%d" % i for i in range(nt)) + "]")
    ns = {}
    exec("\n".join(L), ns)
    f = ns["run"]
    _RUNNERS[key] = f
    return f


def _prepare(hero, board, dead, rng, opp_sampler):
    """Returns (run, nslots).  run(trials) -> list of per-slot tallies in
    half-points (win = 2, tie = 1); equity = max(tallies) / (2 * trials)."""
    hero = list(hero)
    board = list(board)
    nh = len(hero)
    b = len(board)
    if nh not in (2, 3):
        raise ValueError("hero must have 2 or 3 cards")
    if b > 5:
        raise ValueError("board has more than 5 cards")
    used = set(hero)
    used.update(board)
    if len(used) != nh + b:
        raise ValueError("duplicate card between hero and board")
    used.update(dead)
    avail = [c for c in range(52) if c not in used]
    k = 5 - b
    PK = _PK
    if nh == 2:
        hchoices = [(hero[0], hero[1])]
    else:
        hchoices = [(hero[0], hero[1]), (hero[0], hero[2]), (hero[1], hero[2])]
    HP = tuple((PK[x], PK[y]) for x, y in hchoices)
    HS = tuple(PK[x] + PK[y] for x, y in hchoices)
    BP = tuple(PK[c] for c in board)
    C = _OFFS + sum(BP)
    crn = nh == 3 and b >= 3          # hero keeps best 2 of 3 AFTER seeing flop
    nslots = nh if crn else 1
    rnd = rng.random

    if opp_sampler is None:
        mode = "A" if b >= 3 else "B"
        need = k + (2 if mode == "A" else 3)
        if len(avail) < need:
            raise ValueError("not enough cards left in deck")
        if mode == "B" and crn:
            raise AssertionError  # unreachable
        run_fn = _gen_runner(k, len(hchoices), mode)
        deck = [PK[c] for c in avail]
        nd = len(deck)

        def run(trials):
            return run_fn(deck, nd, trials, rnd, C, HS, HP, BP, _RS, _slow_list, _M, _FM)
        return run, nslots

    # ---- custom opponent sampler (generic, slower path)
    availp = [PK[c] for c in avail]
    sample = rng.sample
    if len(avail) < k + 2:
        raise ValueError("not enough cards left in deck")

    def run(trials):
        tot = [0] * nslots
        RS = _RS
        for _ in range(trials):
            o = None
            for _try in range(_MAX_RESAMPLE):
                o = opp_sampler(rng, avail)
                if o is not None:
                    break
            if o is None:
                raise RuntimeError("opp_sampler kept returning None")
            o0 = PK[o[0]]
            o1 = PK[o[1]]
            if k:
                s = sample(availp, k + 2)
                ro = tuple([x for x in s if x != o0 and x != o1][:k])
            else:
                ro = ()
            bsum = C + sum(ro)
            x = bsum + o0 + o1
            if x & _FM:
                ov = _slow_list((o0, o1) + BP + ro)
            else:
                ov = RS[x & _M]
            best = -1
            for i in range(nh if nh == 2 else 3):
                idx = i if nh == 3 else 0
                x = bsum + HS[idx]
                if x & _FM:
                    hv = _slow_list(HP[idx] + BP + ro)
                else:
                    hv = RS[x & _M]
                if crn:
                    if hv > ov:
                        tot[idx] += 2
                    elif hv == ov:
                        tot[idx] += 1
                elif hv > best:
                    best = hv
                if nh == 2:
                    break
            if not crn:
                if best > ov:
                    tot[0] += 2
                elif best == ov:
                    tot[0] += 1
        return tot
    return run, nslots


def equity(hero, board, dead, n, opp_sampler=None, rng=random):
    """Monte Carlo equity of `hero` vs one opponent over `n` trials.

    hero        2 int cards, or 3 int cards (hero will keep its best 2).
    board       0..5 known board int cards; each trial completes it to 5.
    dead        int cards removed from the deck (discards / known-out cards).
    opp_sampler None -> opponent drawn uniformly at random (see below);
                else callable(rng, available_cards_list) -> (c1, c2) ints or
                None (reject; it is called again, up to 200 times per trial).
                `available_cards_list` = all cards not in hero/board/dead; do
                not mutate it.  The sampler's 2 cards are the opponent's hand
                on every street.  The runout is then drawn from the remaining
                cards.  (The sampler path is slower: ~2-3x.)
    Returns P(win) + 0.5 * P(tie).

    Modelling rules
      * len(hero)==2, len(board)>=3: opp gets 2 random cards (post-discard
        world), standard 7-card showdown.
      * len(board)<3 (pre-flop discard still to come), no sampler: the opponent
        is dealt 3 random cards and is assumed to keep the BEST 2 of 3 against
        the final 5-card board.  This is an OPTIMISTIC (for the opponent)
        approximation: a real player picks the keep-pair after only 3 board
        cards, not knowing the turn/river.  Equity is therefore biased low.
        If hero has 3 cards here it likewise keeps its best 2 against the final
        board (optimistic for hero, same bias), so the two biases roughly
        cancel; a hero with 2 cards in this situation is evaluated as given.
      * len(hero)==3 and len(board)>=3: the best 2-of-3 is NOT chosen per
        runout (that would peek at the future).  Instead we compute the
        2-card equity of each of the 3 keep-choices against the SAME simulated
        runouts / opponent hands (common random numbers) and return the max.
        (Max of 3 noisy estimates is very slightly optimistic; CRN makes
        that negligible.)  The 3rd card stays out of the deck in all cases.
    """
    if n <= 0:
        return 0.5
    run, _ = _prepare(hero, board, dead, rng, opp_sampler)
    return max(run(n)) / (2.0 * n)


def equity_timed(hero, board, dead, n, time_budget_s, opp_sampler=None, rng=random):
    """Like equity() but stops after time_budget_s seconds or n trials,
    whichever comes first.  Returns (equity, trials_done).  Always runs at
    least one small chunk so it returns a number even for a ~0 budget."""
    if n <= 0:
        return 0.5, 0
    clock = time.perf_counter
    t0 = clock()
    run, nslots = _prepare(hero, board, dead, rng, opp_sampler)
    deadline = t0 + time_budget_s
    tot = [0] * nslots
    done = 0
    chunk = 32
    while done < n:
        c = chunk if chunk < n - done else n - done
        ts = clock()
        r = run(c)
        te = clock()
        for i in range(nslots):
            tot[i] += r[i]
        done += c
        if te >= deadline:
            break
        # aim for ~0.5 ms chunks (but never overshoot the deadline by much)
        per = (te - ts) / c
        if per > 0:
            room = (deadline - te) / per
            want = int(0.0005 / per)
            chunk = max(8, min(want if want > 8 else 8, int(room) or 1, 4096))
        else:
            chunk = min(chunk * 2, 4096)
        if chunk > deadline - te and per > 0 and (deadline - te) / per < 1:
            break
    return max(tot) / (2.0 * done), done




# =====================================================================
# SHARK STRATEGY
# =====================================================================
import sys
import json

# preflop equity (3-card model, 1200 trials) at percentiles 0,5,...,100 of random hands
_QS = [0.319, 0.393, 0.412, 0.426, 0.438, 0.449, 0.459, 0.468, 0.477, 0.487, 0.495,
       0.504, 0.514, 0.524, 0.533, 0.543, 0.555, 0.57, 0.595, 0.661, 0.769]


def _pct(e):
    q = _QS
    if e <= q[0]:
        return 0.0
    if e >= q[-1]:
        return 1.0
    for i in range(1, len(q)):
        if e <= q[i]:
            lo, hi = q[i - 1], q[i]
            f = (e - lo) / (hi - lo) if hi > lo else 0.0
            return ((i - 1) + f) / (len(q) - 1)
    return 1.0


def _canon_key(hole):
    cs = sorted(hole, key=lambda c: (-(c >> 2), c & 3))
    m = {}
    out = []
    for c in cs:
        s = c & 3
        if s not in m:
            m[s] = len(m)
        out.append((c >> 2, m[s]))
    return tuple(out)


class Shark(object):
    def __init__(self):
        self.rng = random.Random()
        self.hole = []
        self.seat = "button"
        self.dead = []
        self.total = 200
        self.hands = 0
        self.time_left = 30000.0
        self.pre_pct = None
        self.trap = False
        self.cache = {}

    # ---------------------------------------------------------- messages
    def new_hand(self, msg):
        self.hole = cards_to_ints(msg["hole_cards"])
        self.seat = msg.get("seat", "button")
        self.dead = []
        self.pre_pct = None
        self.trap = False
        self.hands += 1

    def discard_result(self, msg):
        self.hole = cards_to_ints(msg["hole_cards"])
        self.dead = []
        for k in ("my_discard", "opp_discard"):
            if msg.get(k):
                self.dead.append(card_to_int(msg[k]))

    # ---------------------------------------------------------- helpers
    def per_hand_ms(self):
        rem = max(1, self.total - self.hands + 1)
        return self.time_left / rem

    def budget_s(self):
        b = self.per_hand_ms() * 0.45 / 3.0
        return max(0.001, min(0.015, b / 1000.0))

    def preflop_pct(self):
        if self.pre_pct is not None:
            return self.pre_pct
        key = _canon_key(self.hole)
        e = self.cache.get(key)
        if e is None:
            n = 1200 if self.per_hand_ms() > 60 else 200
            e = equity(self.hole, [], [], n, rng=self.rng)
            if n >= 1200:
                self.cache[key] = e
        self.pre_pct = _pct(e)
        return self.pre_pct

    def post_equity(self, req, k=1):
        """Equity vs a random hand (k=1) or vs the best of k random hands by current
        made strength (a crude 'opponent bet, so range is top-heavy' model)."""
        board = cards_to_ints(req["board"])
        sampler = None
        if k > 1:
            rng_ = self.rng
            bd = list(board)

            def sampler(rng, avail, _k=k, _bd=bd, _ev=eval7):
                best = None
                bv = -1
                for _ in range(_k):
                    c = rng.sample(avail, 2)
                    v = _ev(c + _bd)
                    if v > bv:
                        bv = v
                        best = c
                return (best[0], best[1])
        e, _ = equity_timed(self.hole, board, self.dead, 2500, self.budget_s(),
                            opp_sampler=sampler, rng=self.rng)
        return e

    @staticmethod
    def _raise(req, target, jam_frac=0.55):
        legal = req["legal_actions"]
        if "raise" not in legal:
            return None
        lo, hi = req["min_raise"], req["max_raise"]
        amt = int(round(target))
        if amt >= jam_frac * hi:
            amt = hi
        amt = max(lo, min(hi, amt))
        return {"type": "action", "action": "raise", "amount": int(amt)}

    @staticmethod
    def _simple(name):
        return {"type": "action", "action": name}

    def _check_or_fold(self, req):
        return self._simple("check" if "check" in req["legal_actions"] else "fold")

    # ---------------------------------------------------------- preflop
    def decide_pre(self, req):
        legal = req["legal_actions"]
        to_call = req["to_call"]
        opp_bet = req["opp_bet"]
        pot = req["pot"]
        p = self.preflop_pct()
        r = self.rng.random()
        lvl = 3 - req.get("raises_left", 3)  # voluntary raises so far
        odds = to_call / float(pot + to_call) if to_call > 0 else 0.0
        rr = self._raise

        if lvl <= 0:
            if "check" in legal:  # BB, SB limped
                if p >= 0.62 and r < 0.85:
                    return rr(req, 3 * 2 + 0) or self._simple("check")
                return self._simple("check")
            # button first to act
            if p >= 0.30:
                return rr(req, 5) or self._simple("call")
            if p >= 0.22 and r < 0.3:
                return self._simple("call")
            return self._simple("fold")

        bb = opp_bet / 2.0
        floor = 1.1 * odds + 0.1
        if lvl == 1:
            thr = max(0.38 + 0.05 * max(0.0, bb - 3.0), floor)
            if p >= 0.88 and r < 0.88:
                return rr(req, 3.4 * opp_bet) or self._simple("call")
            if 0.50 <= p < 0.72 and r < 0.09 and bb <= 4:
                return rr(req, 3.4 * opp_bet) or self._simple("call")
            if p >= thr:
                return self._simple("call")
            return self._simple("fold")
        if lvl == 2:
            thr = max(0.74, floor)
            if p >= 0.975 and r < 0.6:
                return rr(req, req.get("max_raise", 200), 0.0) or self._simple("call")
            if p >= 0.95 and r < 0.7:
                return rr(req, 2.3 * opp_bet) or self._simple("call")
            if 0.45 <= p < 0.65 and r < 0.05:
                return rr(req, 2.3 * opp_bet) or self._simple("fold")
            if p >= thr:
                return self._simple("call")
            return self._simple("fold")
        # level 3+: facing 4-bet or jam
        thr = max(0.93, floor)
        if p >= 0.98 and "raise" in legal:
            return rr(req, req["max_raise"], 0.0)
        if p >= thr:
            return self._simple("call")
        return self._simple("fold")

    # ---------------------------------------------------------- postflop
    def decide_post(self, req):
        legal = req["legal_actions"]
        to_call = req["to_call"]
        pot = req["pot"]
        street = req["street"]
        opp_bet = req["opp_bet"]
        my_bet = req["my_bet"]
        ip = (self.seat == "button")
        can_raise = "raise" in legal
        rr = self._raise
        r = self.rng.random()
        # opponent range model: facing aggression, its range is top-heavy
        k = 1
        prev_streets = set()
        if to_call > 0:
            for h in req.get("history", []):
                if h.get("actor") == "opp" and h.get("action") == "raise" and h.get("street") != street:
                    prev_streets.add(h.get("street"))
            prev = max(1.0, pot - to_call)
            b0 = to_call / prev
            k = 2 if street == "flop" else 3
            if b0 > 0.8:
                k += 1
            if my_bet > 0:
                k += 1
            k += len(prev_streets)
            k = min(k, 6)
        E = self.post_equity(req, k)

        if to_call == 0:
            if not can_raise:
                return self._simple("check")
            if E >= 0.72:
                if (not ip) and street != "river" and r < 0.25:
                    self.trap = True
                    return self._simple("check")
                if r < 0.88:
                    f = self.rng.choice((0.66, 0.8, 1.0))
                    return rr(req, f * pot) or self._simple("check")
                return self._simple("check")
            if E >= 0.64:
                if r < 0.40:
                    return rr(req, 0.5 * pot) or self._simple("check")
                return self._simple("check")
            if E < 0.45:
                if street == "river":
                    bp = 0.38 if E < 0.30 else 0.0
                else:
                    bp = 0.35 if E >= 0.25 else 0.2
                bp *= 1.2 if ip else 0.8
                if r < bp:
                    f = self.rng.choice((0.66, 0.75, 0.9))
                    return rr(req, f * pot) or self._simple("check")
            return self._simple("check")

        # facing a bet / raise
        odds = to_call / float(pot + to_call)
        prev = max(1.0, pot - to_call)
        b = to_call / prev
        shade = 0.02
        if street == "flop" and len(self.hole) == 3:
            shade += 0.03
        Eeff = E - shade

        if self.trap and Eeff >= 0.6 and can_raise and r < 0.85:
            self.trap = False
            return rr(req, opp_bet + 1.0 * (pot + to_call)) or self._simple("call")
        if can_raise and Eeff >= 0.78 and r < 0.55:
            return rr(req, opp_bet + 0.9 * (pot + to_call)) or self._simple("call")
        if Eeff > odds + 0.03:
            return self._simple("call")
        if (can_raise and E < 0.30 and r < 0.07 and street != "river"
                and req.get("raises_left", 0) >= 2 and b <= 0.8):
            return rr(req, opp_bet + 0.8 * (pot + to_call)) or self._simple("fold")
        if Eeff > odds - 0.06 and r < 0.15 and street != "river":
            return self._simple("call")
        return self._simple("fold")

    def decide(self, req):
        self.time_left = float(req.get("time_left_ms", self.time_left))
        try:
            if req["street"] == "preflop":
                a = self.decide_pre(req)
            else:
                a = self.decide_post(req)
            if a is None or a.get("action") not in req["legal_actions"]:
                return self._check_or_fold(req)
            return a
        except Exception as ex:  # robust fallback
            try:
                sys.stderr.write("decide error: %r\n" % (ex,))
            except Exception:
                pass
            return self._check_or_fold(req)

    # ---------------------------------------------------------- discard
    def discard(self, req):
        hole = cards_to_ints(req["hole_cards"])
        try:
            board = cards_to_ints(req["board"])
            self.hole = hole
            run, _ = _prepare(hole, board, [], self.rng, None)
            clock = time.perf_counter
            end = clock() + self.budget_s()
            tot = [0, 0, 0]
            done = 0
            while True:
                r = run(64)
                for i in range(3):
                    tot[i] += r[i]
                done += 64
                if clock() >= end or done >= 2000:
                    break
            best = tot.index(max(tot))
            drop = (2, 1, 0)[best]  # keep (0,1)->drop 2, (0,2)->drop 1, (1,2)->drop 0
            return {"type": "discard", "card": req["hole_cards"][drop]}
        except Exception as ex:
            try:
                sys.stderr.write("discard error: %r\n" % (ex,))
            except Exception:
                pass
            low = min(range(3), key=lambda i: hole[i] >> 2 if hole else 0)
            return {"type": "discard", "card": req["hole_cards"][low]}


def main():
    bot = Shark()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        t = msg.get("type")
        reply = None
        try:
            if t == "init":
                bot.total = int(msg.get("total_hands", 200))
                bot.time_left = float(msg.get("time_bank_ms", 30000))
            elif t == "new_hand":
                bot.new_hand(msg)
            elif t == "discard_result":
                bot.discard_result(msg)
            elif t == "action_request":
                reply = bot.decide(msg)
            elif t == "discard_request":
                reply = bot.discard(msg)
            elif t == "match_end":
                break
        except Exception as ex:
            if t == "action_request":
                reply = {"type": "action",
                         "action": "check" if "check" in msg.get("legal_actions", []) else "fold"}
            elif t == "discard_request":
                reply = {"type": "discard", "card": msg["hole_cards"][0]}
            try:
                sys.stderr.write("error: %r\n" % (ex,))
            except Exception:
                pass
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
