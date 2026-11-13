"""
fasteval.py -- fast pure-Python Hold'em hand evaluator + Monte Carlo equity.

Stdlib only, Python 3.11 compatible, no I/O at import.  Designed to be pasted
into a single bot.py.

CARD ENCODING
    card = rank_index * 4 + suit_index        (0..51)
    rank_index: "23456789TJQKA" -> 0..12      suit_index: "shdc" -> 0..3
    card_to_int("As") == 48, card_to_int("2s") == 0, rank = c >> 2, suit = c & 3

eval7(cards) -> int
    cards: list/tuple of 5, 6 or 7 ints.  Higher is better, equal is a tie.
    score = (category << 20) | 5 x 4-bit tiebreak ranks.  Category 0..8 is
    high card .. straight flush (same order as engine.HAND_NAMES).  The
    ordering is identical to engine.best_hand (best five of all the cards).

    Method: every card maps to one packed integer
        (1 << 3*rank) + (1 << (39 + 4*suit))
    so sum() of the cards gives 13 three-bit rank counters plus 4 four-bit suit
    counters (pre-biased by +3 so that "5 or more of a suit" == bit 3 of the
    field).  Non-flush hands are a single dict lookup on the rank counters
    (precomputed for all 7-card multisets, lazily filled for 5/6 cards);
    flush hands (rare) go through an 8192-entry table on the suit's rank mask.

equity(hero, board, dead, n, opp_sampler=None, rng=random) -> float
    Monte Carlo win probability (ties = 0.5), completing the board to 5 cards.
    See the docstring of equity() for the exact modelling rules / caveats.
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


# ---------------------------------------------------------------- self-test

def _selftest():
    import os
    import sys
    import timeit

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(here)), "Starter Kit"))
    import engine

    rnd = random.Random(12345)
    t_import = time.perf_counter()
    print("python", sys.version.split()[0])

    # ---- (a) correctness vs engine.best_hand
    def mk_hand(kind, size):
        if kind == 0:
            return rnd.sample(range(52), size)
        if kind == 1:   # few ranks -> pairs/trips/quads/full houses/straights
            rs = rnd.sample(range(13), rnd.choice((3, 4, 5, 6)))
            pool = [r * 4 + s for r in rs for s in range(4)]
            return rnd.sample(pool, size)
        if kind == 2:   # few suits -> flushes / straight flushes
            ss = rnd.sample(range(4), rnd.choice((1, 2)))
            pool = [r * 4 + s for r in range(13) for s in ss]
            return rnd.sample(pool, size)
        # consecutive-ish ranks -> straights / wheels
        lo = rnd.randrange(0, 9)
        rs = list(range(lo, lo + 5)) + [12, rnd.randrange(13)]
        pool = [r * 4 + s for r in set(rs) for s in range(4)]
        return rnd.sample(pool, min(size, len(pool)))

    pool_hands = []
    for i in range(36000):
        size = 7 if i % 10 < 8 else rnd.choice((5, 6))
        pool_hands.append(mk_hand(i % 4, size))
    t0 = time.perf_counter()
    eng = []
    for h in pool_hands:
        s = ints_to_cards(h)
        eng.append(engine.best_hand(s[:2], s[2:]))
    mine = [eval7(h) for h in pool_hands]
    print("engine eval of %d hands: %.1fs" % (len(pool_hands), time.perf_counter() - t0))

    # order isomorphism: same engine tuple <=> same score; order agrees
    by_tuple = {}
    for e, m in zip(eng, mine):
        by_tuple.setdefault(e, set()).add(m)
    assert all(len(v) == 1 for v in by_tuple.values()), "same engine tuple, different scores"
    keys = sorted(by_tuple)
    vals = [next(iter(by_tuple[x])) for x in keys]
    assert all(a < b for a, b in zip(vals, vals[1:])), "order mismatch vs engine (monotonicity)"
    cats = {}
    for m in mine:
        cats[m >> 20] = cats.get(m >> 20, 0) + 1
    print("  distinct hand classes seen: %d; category counts: %s" % (len(keys), dict(sorted(cats.items()))))
    assert len(cats) == 9, "not all categories exercised"

    # >= 100k random pair sign comparisons
    npairs = 200000
    bad = 0
    for _ in range(npairs):
        i = rnd.randrange(len(pool_hands))
        j = rnd.randrange(len(pool_hands))
        se = (eng[i] > eng[j]) - (eng[i] < eng[j])
        sm = (mine[i] > mine[j]) - (mine[i] < mine[j])
        if se != sm:
            bad += 1
    print("  pair comparisons: %d mismatches / %d" % (bad, npairs))
    assert bad == 0

    # fresh random 7-card pairs, computed directly (no pooling), smaller set
    bad = 0
    nfresh = 3000
    for _ in range(nfresh):
        a = rnd.sample(range(52), 7)
        b = rnd.sample(range(52), 7)
        ea = engine.best_hand(ints_to_cards(a[:2]), ints_to_cards(a[2:]))
        eb = engine.best_hand(ints_to_cards(b[:2]), ints_to_cards(b[2:]))
        sa, sb = eval7(a), eval7(b)
        if ((ea > eb) - (ea < eb)) != ((sa > sb) - (sa < sb)):
            bad += 1
    print("  fresh random 7-card pairs: %d mismatches / %d" % (bad, nfresh))
    assert bad == 0

    # targeted edge cases (each tuple: hand A, hand B, expected sign of A vs B)
    def E(s):
        return cards_to_ints(s.split())

    cases = [
        ("As 2d 3c 4h 5s 9d Kc", "2s 3d 4c 5h 6s 9d Kc"),   # wheel < 6-high straight
        ("As 2s 3s 4s 5s 9d Kc", "2h 3h 4h 5h 6h 9d Kc"),   # steel wheel < 6-high SF
        ("As 2s 3s 4s 5s 9d Kc", "Kd Kc Kh Ks 2d 3d 9c"),   # steel wheel > quads
        ("As 2d 3c 4h 5s Kd Kc", "Ah Ad Ac Kh Ks 2c 3d"),   # wheel < full house
        ("As Ks Qs Js 2s 3d 4c", "5h 6d 7c 8s 9h Td Jc"),   # flush > straight
        ("Ts Js Qs Ks As 2d 2c", "2h 3d 4c 5s 6h Td Jc"),   # royal vs straight
        ("Ah Kh Qh Jh 9h 2c 3d", "Ah Kh Qh Jh 8h 2c 3d"),   # flush kicker
        ("Ah Kh Qh Jh 9h 2h 3d", "Ah Kh Qh Jh 9h 2c 3d"),   # 6-flush plays top 5 (tie)
        ("Ah Kd Qc Jh 9s 2c 3d", "Ac Ks Qd Jd 8h 2d 3s"),   # high card, 5th kicker
        ("Ah Ad Kc Qh 9s 2c 3d", "Ac As Kd Jd 9h 2d 3s"),   # pair, 2nd kicker
        ("Ah Ad Kc Kh 9s 2c 3d", "Ac As Qd Qs Jh 2d 3s"),   # two pair high
        ("Ah Ad Kc Kh Qs Qc 3d", "Ac As Kd Ks 9h 9d 3s"),   # three pair: kicker Q vs 9
        ("Ah Ad 5c 5h 4s 4c 3d", "Ac As 5d 5s 3h 3c 2s"),   # three pair: top two + kicker
        ("Ah Ad Ac Kh Ks 2c 3d", "Kd Kc Kh Ah Ad 2d 3c"),   # AAAKK > KKKAA
        ("Ah Ad Ac Kh Ks 2c 3d", "Kd Kc Kh Qh Qd 2d 3c"),   # full house
        ("Ah Ad Ac Kh Kd Kc 3d", "Ac As Ad Qh Qs Qc 3s"),   # two trips: AAA+KK > AAA+QQ
        ("2h 2d 2c 2s Ah Kd Qc", "2h 2d 2c 2s Ah Kd Jc"),   # quads + A kicker (tie)
        ("2h 2d 2c 2s Ah Ad Ac", "3h 3d 3c 3s Ah Kd Qc"),   # quads, trips as kicker
        ("9h 9d 9c Kh 3s 4c 5d", "9s 9h 9c Qh 3s 4c 5d"),   # trips kicker
        ("Ah Kd Qc Jh Ts 2c 3d", "Kh Qd Jc Th 9s 2c 3d"),   # broadway > K-high straight
        ("Ah 2d 3c 4h 5s 6c 7d", "2h 3d 4c 5h 6s 7c Kd"),   # 7-high straight, wheel ignored
        ("Ah Kh Qh Jh Th 9h 8h", "Ah Kh Qh Jh Th 2c 3d"),   # royal either way (tie)
        ("8h 9h Th Jh Qh Kc Ad", "7h 8h 9h Th Jh Qc Kd"),   # SF Q-high vs SF J-high
        ("Qs Qh 2s 2h Ac 5d 7c", "Qd Qc 3s 3h Ad 5h 7d"),   # two pair 2s vs 3s
        ("Ks Kh Ac 9d 7c 5h 3d", "Kd Kc Ah 9h 7s 5d 2c"),   # pair, last kicker 3 vs 2
        ("As Ad Ah 5s 5d 5h 2c", "Ks Kd Kh 5c 5s 5d 4c"),   # two trips: AAA55 vs KKK55
    ]
    for sa, sb in cases:
        a, b = E(sa), E(sb)
        ea = engine.best_hand(sa.split()[:2], sa.split()[2:])
        eb = engine.best_hand(sb.split()[:2], sb.split()[2:])
        sma, smb = eval7(a), eval7(b)
        assert ((ea > eb) - (ea < eb)) == ((sma > smb) - (sma < smb)), (sa, sb, ea, eb, sma, smb)
    # asserted expectations on unambiguous cases
    assert eval7(E("As 2d 3c 4h 5s 9d Kc")) < eval7(E("2s 3d 4c 5h 6s 9d Kc"))
    assert eval7(E("Ts Js Qs Ks As 2d 2c")) > eval7(E("Ah Ad Ac Kh Ks 2c 3d"))
    assert eval7(E("2h 2d 2c 2s Ah Kd Qc")) == eval7(E("2s 2d 2c 2h Ac Kh Qd"))
    assert hand_category(eval7(E("As Ks Qs Js Ts 2d 3c"))) == 8
    assert hand_category(eval7(E("As 2s 3s 4s 5s 9d Kc"))) == 8
    # boards that play: both players tie
    board = E("Ah Kh Qh Jh Th")
    assert eval7(board + E("2c 3d")) == eval7(board + E("4c 5d"))
    board = E("9c 9d 8h 8s 7c")
    assert eval7(board + E("2c 3d")) == eval7(board + E("4c 5d"))
    # 5 and 6 card inputs
    assert eval7(E("As Ks Qs Js Ts")) == eval7(E("As Ks Qs Js Ts 2d"))
    assert eval7(E("2s 3s 4s 5s 6s")) > eval7(E("As 2d 3c 4h 5s"))
    print("  targeted edge cases: ok")

    # ---- (b) benchmarks
    hands7 = [rnd.sample(range(52), 7) for _ in range(20000)]
    ev = eval7
    loops = 10
    t = min(timeit.repeat(lambda: [ev(h) for h in hands7], number=1, repeat=loops))
    base = min(timeit.repeat(lambda: [None for h in hands7], number=1, repeat=loops))
    print("eval7 (7 cards): %.0f calls/s  (%.2f us/call incl. list-comp overhead; %.2f us net)"
          % (len(hands7) / t, t / len(hands7) * 1e6, (t - base) / len(hands7) * 1e6))

    def bench_eq(label, hero, board, dead=(), n=20000, **kw):
        best = 1e9
        e = None
        for _ in range(3):
            t0 = time.perf_counter()
            e = equity(hero, board, list(dead), n, rng=random.Random(1), **kw)
            best = min(best, time.perf_counter() - t0)
        print("equity %-38s %.4f  %8.0f sims/s  (%.2f us/sim)" % (label, e, n / best, best / n * 1e6))
        return e

    # ---- (c) sanity
    aa = bench_eq("AA vs random, preflop (3-card opp)", E("As Ah"), [], n=20000)
    # post-discard world 2-card vs 2-card random, no board (n/a in real game, but sanity check)
    flop = E("2c 7d 9h")
    e_flop = bench_eq("AA vs random, flop 2c7d9h", E("As Ah"), flop, n=30000)
    bench_eq("AA vs random, turn", E("As Ah"), E("2c 7d 9h Jd"), n=30000)
    bench_eq("AA vs random, river", E("As Ah"), E("2c 7d 9h Jd 3s"), n=30000)
    bench_eq("72o vs random, flop 2c7d9h", E("7s 2h"), flop, n=30000)
    bench_eq("3-card hero (AsAhKd) flop (max of 3)", E("As Ah Kd"), flop, n=30000)
    bench_eq("3-card hero preflop (AsAhKd)", E("As Ah Kd"), [], n=20000)

    # 2-card heads-up preflop sanity: opp 2 random cards (use sampler that deals 2) -> ~0.85
    def rand2(r, av):
        return tuple(r.sample(av, 2))
    e_aa = equity(E("As Ah"), [], [], 40000, opp_sampler=rand2, rng=random.Random(7))
    print("AA vs random 2-card hand preflop (sampler path): %.4f  (expect ~0.85)" % e_aa)
    assert 0.83 < e_aa < 0.87, e_aa
    # sampler path speed
    t0 = time.perf_counter()
    equity(E("As Ah"), flop, [], 20000, opp_sampler=rand2, rng=random.Random(7))
    dt = time.perf_counter() - t0
    print("equity sampler path, flop: %.0f sims/s" % (20000 / dt))
    # sampler vs builtin on the same spot should agree
    e1 = equity(E("Kh Qh"), flop, [], 60000, rng=random.Random(3))
    e2 = equity(E("Kh Qh"), flop, [], 60000, opp_sampler=rand2, rng=random.Random(4))
    print("KhQh on 2c7d9h: builtin %.4f vs sampler %.4f" % (e1, e2))
    assert abs(e1 - e2) < 0.015
    # 3-card hero on flop >= any single choice
    e3 = equity(E("As Ah Kd"), flop, [], 30000, rng=random.Random(5))
    e2c = equity(E("As Ah"), flop, [], 30000, rng=random.Random(5))
    print("3-card AsAhKd flop %.4f vs AsAh %.4f" % (e3, e2c))
    assert e3 >= e2c - 0.02
    # note: aa (3-card optimistic-opponent approximation) is deliberately well below 0.85
    assert 0.65 < aa < 0.80, aa
    # timed variant
    for budget in (0.0, 0.005, 0.05):
        t0 = time.perf_counter()
        e, tr = equity_timed(E("As Ah"), flop, [], 10 ** 7, budget, rng=random.Random(9))
        dt = time.perf_counter() - t0
        print("equity_timed budget %.3fs -> %.4f, %d trials, took %.4fs" % (budget, e, tr, dt))
    # all-board-known / dead cards
    e = equity(E("As Ah"), E("2c 7d 9h Jd 3s"), E("Kd Ks"), 5000, rng=random.Random(2))
    print("river with dead cards: %.4f" % e)
    print("ALL OK")


if __name__ == "__main__":
    _selftest()
