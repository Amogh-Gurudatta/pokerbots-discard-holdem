"""Discard Hold'em bot: strategy core (dev version; imports fasteval + preflop_table from src/).

`python3 tools/build.py` inlines both into dist/bot.py (the submission).
Never print to stdout except protocol replies.
"""
import sys, json, time, random

# ==== EVALUATOR (inlined from src/fasteval.py) ====
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


# ==== END EVALUATOR ====

# ==== PREFLOP TABLE (inlined from src/preflop_table.py) ====
# Generated by tools/build_preflop.py (4000 trials/class)
PREFLOP_EQ = {
'2a2b2c': 0.3164,
'3a2a2b': 0.4128,
'3a2b2c': 0.3994,
'3a3b2a': 0.4164,
'3a3b2c': 0.4066,
'3a3b3c': 0.3401,
'4a2a2b': 0.4274,
'4a2b2c': 0.3895,
'4a3a2a': 0.3355,
'4a3a2b': 0.3508,
'4a3a3b': 0.4520,
'4a3b2a': 0.3284,
'4a3b2b': 0.3306,
'4a3b2c': 0.3196,
'4a3b3c': 0.4241,
'4a4b2a': 0.4461,
'4a4b2c': 0.4395,
'4a4b3a': 0.4491,
'4a4b3c': 0.4449,
'4a4b4c': 0.3764,
'5a2a2b': 0.4101,
'5a2b2c': 0.3917,
'5a3a2a': 0.3599,
'5a3a2b': 0.3524,
'5a3a3b': 0.4490,
'5a3b2a': 0.3515,
'5a3b2b': 0.3412,
'5a3b2c': 0.3160,
'5a3b3c': 0.4359,
'5a4a2a': 0.3638,
'5a4a2b': 0.3779,
'5a4a3a': 0.3698,
'5a4a3b': 0.3754,
'5a4a4b': 0.4590,
'5a4b2a': 0.3608,
'5a4b2b': 0.3588,
'5a4b2c': 0.3374,
'5a4b3a': 0.3543,
'5a4b3b': 0.3571,
'5a4b3c': 0.3427,
'5a4b4c': 0.4656,
'5a5b2a': 0.4871,
'5a5b2c': 0.4710,
'5a5b3a': 0.4835,
'5a5b3c': 0.4850,
'5a5b4a': 0.4745,
'5a5b4c': 0.4774,
'5a5b5c': 0.4384,
'6a2a2b': 0.4155,
'6a2b2c': 0.4051,
'6a3a2a': 0.3501,
'6a3a2b': 0.3765,
'6a3a3b': 0.4520,
'6a3b2a': 0.3471,
'6a3b2b': 0.3519,
'6a3b2c': 0.3350,
'6a3b3c': 0.4343,
'6a4a2a': 0.3704,
'6a4a2b': 0.3790,
'6a4a3a': 0.3653,
'6a4a3b': 0.3693,
'6a4a4b': 0.4809,
'6a4b2a': 0.3636,
'6a4b2b': 0.3670,
'6a4b2c': 0.3518,
'6a4b3a': 0.3496,
'6a4b3b': 0.3756,
'6a4b3c': 0.3478,
'6a4b4c': 0.4706,
'6a5a2a': 0.4000,
'6a5a2b': 0.4080,
'6a5a3a': 0.3839,
'6a5a3b': 0.4031,
'6a5a4a': 0.3792,
'6a5a4b': 0.3949,
'6a5a5b': 0.5055,
'6a5b2a': 0.3746,
'6a5b2b': 0.3907,
'6a5b2c': 0.3684,
'6a5b3a': 0.3781,
'6a5b3b': 0.3934,
'6a5b3c': 0.3564,
'6a5b4a': 0.3829,
'6a5b4b': 0.3752,
'6a5b4c': 0.3680,
'6a5b5c': 0.5005,
'6a6b2a': 0.5035,
'6a6b2c': 0.5064,
'6a6b3a': 0.4959,
'6a6b3c': 0.5054,
'6a6b4a': 0.5126,
'6a6b4c': 0.5139,
'6a6b5a': 0.5069,
'6a6b5c': 0.5281,
'6a6b6c': 0.4597,
'7a2a2b': 0.4123,
'7a2b2c': 0.4004,
'7a3a2a': 0.3489,
'7a3a2b': 0.3756,
'7a3a3b': 0.4512,
'7a3b2a': 0.3593,
'7a3b2b': 0.3565,
'7a3b2c': 0.3274,
'7a3b3c': 0.4358,
'7a4a2a': 0.3767,
'7a4a2b': 0.3824,
'7a4a3a': 0.3887,
'7a4a3b': 0.3841,
'7a4a4b': 0.4873,
'7a4b2a': 0.3678,
'7a4b2b': 0.3754,
'7a4b2c': 0.3526,
'7a4b3a': 0.3730,
'7a4b3b': 0.3731,
'7a4b3c': 0.3519,
'7a4b4c': 0.4768,
'7a5a2a': 0.4054,
'7a5a2b': 0.3834,
'7a5a3a': 0.3896,
'7a5a3b': 0.4118,
'7a5a4a': 0.4006,
'7a5a4b': 0.3929,
'7a5a5b': 0.5082,
'7a5b2a': 0.3937,
'7a5b2b': 0.3901,
'7a5b2c': 0.3744,
'7a5b3a': 0.3990,
'7a5b3b': 0.4015,
'7a5b3c': 0.3806,
'7a5b4a': 0.4016,
'7a5b4b': 0.4034,
'7a5b4c': 0.3651,
'7a5b5c': 0.5018,
'7a6a2a': 0.3981,
'7a6a2b': 0.4101,
'7a6a3a': 0.4051,
'7a6a3b': 0.3999,
'7a6a4a': 0.4044,
'7a6a4b': 0.4086,
'7a6a5a': 0.4055,
'7a6a5b': 0.4158,
'7a6a6b': 0.5215,
'7a6b2a': 0.3885,
'7a6b2b': 0.3944,
'7a6b2c': 0.3812,
'7a6b3a': 0.3887,
'7a6b3b': 0.4027,
'7a6b3c': 0.3755,
'7a6b4a': 0.4007,
'7a6b4b': 0.3964,
'7a6b4c': 0.3701,
'7a6b5a': 0.3955,
'7a6b5b': 0.3995,
'7a6b5c': 0.3777,
'7a6b6c': 0.5339,
'7a7b2a': 0.5386,
'7a7b2c': 0.5421,
'7a7b3a': 0.5617,
'7a7b3c': 0.5321,
'7a7b4a': 0.5309,
'7a7b4c': 0.5363,
'7a7b5a': 0.5390,
'7a7b5c': 0.5399,
'7a7b6a': 0.5435,
'7a7b6c': 0.5445,
'7a7b7c': 0.5085,
'8a2a2b': 0.4190,
'8a2b2c': 0.4180,
'8a3a2a': 0.3619,
'8a3a2b': 0.3767,
'8a3a3b': 0.4544,
'8a3b2a': 0.3539,
'8a3b2b': 0.3683,
'8a3b2c': 0.3583,
'8a3b3c': 0.4381,
'8a4a2a': 0.3840,
'8a4a2b': 0.3896,
'8a4a3a': 0.3870,
'8a4a3b': 0.3875,
'8a4a4b': 0.5035,
'8a4b2a': 0.3844,
'8a4b2b': 0.3850,
'8a4b2c': 0.3573,
'8a4b3a': 0.4037,
'8a4b3b': 0.3902,
'8a4b3c': 0.3683,
'8a4b4c': 0.4824,
'8a5a2a': 0.4081,
'8a5a2b': 0.4079,
'8a5a3a': 0.4026,
'8a5a3b': 0.4070,
'8a5a4a': 0.4215,
'8a5a4b': 0.4151,
'8a5a5b': 0.5116,
'8a5b2a': 0.4010,
'8a5b2b': 0.3957,
'8a5b2c': 0.3645,
'8a5b3a': 0.3946,
'8a5b3b': 0.4118,
'8a5b3c': 0.3882,
'8a5b4a': 0.4083,
'8a5b4b': 0.4133,
'8a5b4c': 0.3972,
'8a5b5c': 0.5149,
'8a6a2a': 0.4174,
'8a6a2b': 0.4178,
'8a6a3a': 0.4240,
'8a6a3b': 0.4141,
'8a6a4a': 0.4269,
'8a6a4b': 0.4196,
'8a6a5a': 0.4185,
'8a6a5b': 0.4248,
'8a6a6b': 0.5624,
'8a6b2a': 0.4039,
'8a6b2b': 0.4189,
'8a6b2c': 0.3789,
'8a6b3a': 0.4086,
'8a6b3b': 0.4215,
'8a6b3c': 0.3941,
'8a6b4a': 0.4085,
'8a6b4b': 0.4042,
'8a6b4c': 0.4032,
'8a6b5a': 0.4340,
'8a6b5b': 0.4210,
'8a6b5c': 0.3922,
'8a6b6c': 0.5411,
'8a7a2a': 0.4218,
'8a7a2b': 0.4296,
'8a7a3a': 0.4230,
'8a7a3b': 0.4482,
'8a7a4a': 0.4293,
'8a7a4b': 0.4169,
'8a7a5a': 0.4283,
'8a7a5b': 0.4261,
'8a7a6a': 0.4240,
'8a7a6b': 0.4309,
'8a7a7b': 0.5626,
'8a7b2a': 0.4285,
'8a7b2b': 0.4118,
'8a7b2c': 0.4017,
'8a7b3a': 0.4264,
'8a7b3b': 0.4278,
'8a7b3c': 0.3994,
'8a7b4a': 0.4286,
'8a7b4b': 0.4263,
'8a7b4c': 0.4108,
'8a7b5a': 0.4309,
'8a7b5b': 0.4201,
'8a7b5c': 0.4035,
'8a7b6a': 0.4329,
'8a7b6b': 0.4195,
'8a7b6c': 0.4101,
'8a7b7c': 0.5676,
'8a8b2a': 0.5656,
'8a8b2c': 0.5759,
'8a8b3a': 0.5784,
'8a8b3c': 0.5615,
'8a8b4a': 0.5669,
'8a8b4c': 0.5636,
'8a8b5a': 0.5715,
'8a8b5c': 0.5857,
'8a8b6a': 0.5747,
'8a8b6c': 0.5806,
'8a8b7a': 0.5833,
'8a8b7c': 0.5903,
'8a8b8c': 0.5311,
'9a2a2b': 0.4404,
'9a2b2c': 0.4283,
'9a3a2a': 0.3724,
'9a3a2b': 0.3770,
'9a3a3b': 0.4714,
'9a3b2a': 0.3864,
'9a3b2b': 0.3581,
'9a3b2c': 0.3509,
'9a3b3c': 0.4441,
'9a4a2a': 0.3955,
'9a4a2b': 0.3936,
'9a4a3a': 0.4111,
'9a4a3b': 0.4119,
'9a4a4b': 0.4954,
'9a4b2a': 0.3854,
'9a4b2b': 0.3970,
'9a4b2c': 0.3685,
'9a4b3a': 0.4006,
'9a4b3b': 0.3955,
'9a4b3c': 0.3595,
'9a4b4c': 0.4740,
'9a5a2a': 0.3989,
'9a5a2b': 0.4138,
'9a5a3a': 0.4106,
'9a5a3b': 0.4160,
'9a5a4a': 0.4264,
'9a5a4b': 0.4354,
'9a5a5b': 0.5062,
'9a5b2a': 0.3874,
'9a5b2b': 0.4090,
'9a5b2c': 0.3811,
'9a5b3a': 0.4189,
'9a5b3b': 0.4004,
'9a5b3c': 0.3860,
'9a5b4a': 0.4173,
'9a5b4b': 0.4190,
'9a5b4c': 0.4007,
'9a5b5c': 0.5259,
'9a6a2a': 0.4165,
'9a6a2b': 0.4066,
'9a6a3a': 0.4195,
'9a6a3b': 0.4306,
'9a6a4a': 0.4121,
'9a6a4b': 0.4221,
'9a6a5a': 0.4279,
'9a6a5b': 0.4447,
'9a6a6b': 0.5406,
'9a6b2a': 0.4079,
'9a6b2b': 0.4110,
'9a6b2c': 0.3970,
'9a6b3a': 0.4128,
'9a6b3b': 0.4078,
'9a6b3c': 0.4086,
'9a6b4a': 0.4220,
'9a6b4b': 0.4181,
'9a6b4c': 0.4114,
'9a6b5a': 0.4239,
'9a6b5b': 0.4360,
'9a6b5c': 0.4203,
'9a6b6c': 0.5312,
'9a7a2a': 0.4203,
'9a7a2b': 0.4427,
'9a7a3a': 0.4301,
'9a7a3b': 0.4366,
'9a7a4a': 0.4311,
'9a7a4b': 0.4449,
'9a7a5a': 0.4281,
'9a7a5b': 0.4404,
'9a7a6a': 0.4348,
'9a7a6b': 0.4491,
'9a7a7b': 0.5643,
'9a7b2a': 0.4215,
'9a7b2b': 0.4299,
'9a7b2c': 0.4012,
'9a7b3a': 0.4280,
'9a7b3b': 0.4245,
'9a7b3c': 0.4328,
'9a7b4a': 0.4254,
'9a7b4b': 0.4321,
'9a7b4c': 0.4231,
'9a7b5a': 0.4211,
'9a7b5b': 0.4460,
'9a7b5c': 0.4200,
'9a7b6a': 0.4405,
'9a7b6b': 0.4449,
'9a7b6c': 0.4283,
'9a7b7c': 0.5655,
'9a8a2a': 0.4380,
'9a8a2b': 0.4522,
'9a8a3a': 0.4516,
'9a8a3b': 0.4300,
'9a8a4a': 0.4535,
'9a8a4b': 0.4406,
'9a8a5a': 0.4606,
'9a8a5b': 0.4625,
'9a8a6a': 0.4511,
'9a8a6b': 0.4377,
'9a8a7a': 0.4369,
'9a8a7b': 0.4559,
'9a8a8b': 0.6005,
'9a8b2a': 0.4422,
'9a8b2b': 0.4496,
'9a8b2c': 0.4216,
'9a8b3a': 0.4325,
'9a8b3b': 0.4283,
'9a8b3c': 0.4278,
'9a8b4a': 0.4504,
'9a8b4b': 0.4490,
'9a8b4c': 0.4395,
'9a8b5a': 0.4534,
'9a8b5b': 0.4455,
'9a8b5c': 0.4336,
'9a8b6a': 0.4407,
'9a8b6b': 0.4490,
'9a8b6c': 0.4384,
'9a8b7a': 0.4512,
'9a8b7b': 0.4436,
'9a8b7c': 0.4265,
'9a8b8c': 0.6021,
'9a9b2a': 0.6078,
'9a9b2c': 0.6071,
'9a9b3a': 0.6091,
'9a9b3c': 0.6166,
'9a9b4a': 0.6119,
'9a9b4c': 0.6164,
'9a9b5a': 0.6261,
'9a9b5c': 0.6132,
'9a9b6a': 0.6125,
'9a9b6c': 0.6110,
'9a9b7a': 0.6224,
'9a9b7c': 0.6154,
'9a9b8a': 0.6214,
'9a9b8c': 0.6164,
'9a9b9c': 0.5917,
'Aa2a2b': 0.4690,
'Aa2b2c': 0.4467,
'Aa3a2a': 0.4734,
'Aa3a2b': 0.4771,
'Aa3a3b': 0.4790,
'Aa3b2a': 0.4730,
'Aa3b2b': 0.4584,
'Aa3b2c': 0.4472,
'Aa3b3c': 0.4744,
'Aa4a2a': 0.4791,
'Aa4a2b': 0.4901,
'Aa4a3a': 0.4903,
'Aa4a3b': 0.4975,
'Aa4a4b': 0.5329,
'Aa4b2a': 0.4619,
'Aa4b2b': 0.4784,
'Aa4b2c': 0.4615,
'Aa4b3a': 0.4818,
'Aa4b3b': 0.4691,
'Aa4b3c': 0.4492,
'Aa4b4c': 0.4935,
'Aa5a2a': 0.4884,
'Aa5a2b': 0.4799,
'Aa5a3a': 0.4978,
'Aa5a3b': 0.5014,
'Aa5a4a': 0.4909,
'Aa5a4b': 0.5215,
'Aa5a5b': 0.5401,
'Aa5b2a': 0.4868,
'Aa5b2b': 0.4809,
'Aa5b2c': 0.4706,
'Aa5b3a': 0.4769,
'Aa5b3b': 0.4869,
'Aa5b3c': 0.4716,
'Aa5b4a': 0.4869,
'Aa5b4b': 0.5012,
'Aa5b4c': 0.4846,
'Aa5b5c': 0.5302,
'Aa6a2a': 0.4980,
'Aa6a2b': 0.5029,
'Aa6a3a': 0.5070,
'Aa6a3b': 0.5174,
'Aa6a4a': 0.5118,
'Aa6a4b': 0.5244,
'Aa6a5a': 0.5112,
'Aa6a5b': 0.5060,
'Aa6a6b': 0.5779,
'Aa6b2a': 0.4855,
'Aa6b2b': 0.4873,
'Aa6b2c': 0.4873,
'Aa6b3a': 0.4969,
'Aa6b3b': 0.4768,
'Aa6b3c': 0.4720,
'Aa6b4a': 0.5100,
'Aa6b4b': 0.4949,
'Aa6b4c': 0.4923,
'Aa6b5a': 0.5088,
'Aa6b5b': 0.5064,
'Aa6b5c': 0.4934,
'Aa6b6c': 0.5684,
'Aa7a2a': 0.5164,
'Aa7a2b': 0.5061,
'Aa7a3a': 0.5019,
'Aa7a3b': 0.5230,
'Aa7a4a': 0.5091,
'Aa7a4b': 0.5169,
'Aa7a5a': 0.5256,
'Aa7a5b': 0.5245,
'Aa7a6a': 0.5355,
'Aa7a6b': 0.5196,
'Aa7a7b': 0.6059,
'Aa7b2a': 0.5145,
'Aa7b2b': 0.5061,
'Aa7b2c': 0.4888,
'Aa7b3a': 0.5120,
'Aa7b3b': 0.5082,
'Aa7b3c': 0.4993,
'Aa7b4a': 0.5160,
'Aa7b4b': 0.5180,
'Aa7b4c': 0.4923,
'Aa7b5a': 0.5308,
'Aa7b5b': 0.5148,
'Aa7b5c': 0.4976,
'Aa7b6a': 0.5235,
'Aa7b6b': 0.5076,
'Aa7b6c': 0.5160,
'Aa7b7c': 0.5946,
'Aa8a2a': 0.5019,
'Aa8a2b': 0.5211,
'Aa8a3a': 0.5186,
'Aa8a3b': 0.5393,
'Aa8a4a': 0.5222,
'Aa8a4b': 0.5251,
'Aa8a5a': 0.5405,
'Aa8a5b': 0.5451,
'Aa8a6a': 0.5246,
'Aa8a6b': 0.5334,
'Aa8a7a': 0.5355,
'Aa8a7b': 0.5385,
'Aa8a8b': 0.6358,
'Aa8b2a': 0.5186,
'Aa8b2b': 0.5044,
'Aa8b2c': 0.5054,
'Aa8b3a': 0.5072,
'Aa8b3b': 0.5250,
'Aa8b3c': 0.5102,
'Aa8b4a': 0.5226,
'Aa8b4b': 0.5151,
'Aa8b4c': 0.5169,
'Aa8b5a': 0.5278,
'Aa8b5b': 0.5324,
'Aa8b5c': 0.5106,
'Aa8b6a': 0.5315,
'Aa8b6b': 0.5161,
'Aa8b6c': 0.5208,
'Aa8b7a': 0.5347,
'Aa8b7b': 0.5139,
'Aa8b7c': 0.5145,
'Aa8b8c': 0.6261,
'Aa9a2a': 0.5285,
'Aa9a2b': 0.5310,
'Aa9a3a': 0.5315,
'Aa9a3b': 0.5363,
'Aa9a4a': 0.5260,
'Aa9a4b': 0.5431,
'Aa9a5a': 0.5593,
'Aa9a5b': 0.5421,
'Aa9a6a': 0.5407,
'Aa9a6b': 0.5387,
'Aa9a7a': 0.5399,
'Aa9a7b': 0.5561,
'Aa9a8a': 0.5471,
'Aa9a8b': 0.5556,
'Aa9a9b': 0.6545,
'Aa9b2a': 0.5189,
'Aa9b2b': 0.5290,
'Aa9b2c': 0.5164,
'Aa9b3a': 0.5281,
'Aa9b3b': 0.5098,
'Aa9b3c': 0.5112,
'Aa9b4a': 0.5466,
'Aa9b4b': 0.5339,
'Aa9b4c': 0.5210,
'Aa9b5a': 0.5404,
'Aa9b5b': 0.5215,
'Aa9b5c': 0.5235,
'Aa9b6a': 0.5444,
'Aa9b6b': 0.5370,
'Aa9b6c': 0.5325,
'Aa9b7a': 0.5510,
'Aa9b7b': 0.5350,
'Aa9b7c': 0.5340,
'Aa9b8a': 0.5395,
'Aa9b8b': 0.5456,
'Aa9b8c': 0.5272,
'Aa9b9c': 0.6516,
'AaAb2a': 0.7825,
'AaAb2c': 0.7770,
'AaAb3a': 0.7790,
'AaAb3c': 0.7886,
'AaAb4a': 0.7810,
'AaAb4c': 0.7805,
'AaAb5a': 0.7800,
'AaAb5c': 0.7923,
'AaAb6a': 0.7800,
'AaAb6c': 0.7903,
'AaAb7a': 0.7768,
'AaAb7c': 0.7906,
'AaAb8a': 0.7897,
'AaAb8c': 0.8010,
'AaAb9a': 0.8059,
'AaAb9c': 0.7909,
'AaAbAc': 0.7659,
'AaAbJa': 0.7821,
'AaAbJc': 0.7874,
'AaAbKa': 0.7827,
'AaAbKc': 0.7790,
'AaAbQa': 0.7774,
'AaAbQc': 0.7851,
'AaAbTa': 0.7879,
'AaAbTc': 0.8037,
'AaJa2a': 0.5627,
'AaJa2b': 0.5659,
'AaJa3a': 0.5550,
'AaJa3b': 0.5627,
'AaJa4a': 0.5639,
'AaJa4b': 0.5777,
'AaJa5a': 0.5756,
'AaJa5b': 0.5715,
'AaJa6a': 0.5683,
'AaJa6b': 0.5743,
'AaJa7a': 0.5757,
'AaJa7b': 0.5726,
'AaJa8a': 0.5820,
'AaJa8b': 0.5925,
'AaJa9a': 0.5857,
'AaJa9b': 0.5847,
'AaJaJb': 0.6943,
'AaJaTa': 0.5866,
'AaJaTb': 0.5894,
'AaJb2a': 0.5583,
'AaJb2b': 0.5450,
'AaJb2c': 0.5312,
'AaJb3a': 0.5641,
'AaJb3b': 0.5471,
'AaJb3c': 0.5380,
'AaJb4a': 0.5681,
'AaJb4b': 0.5491,
'AaJb4c': 0.5397,
'AaJb5a': 0.5690,
'AaJb5b': 0.5525,
'AaJb5c': 0.5699,
'AaJb6a': 0.5630,
'AaJb6b': 0.5583,
'AaJb6c': 0.5574,
'AaJb7a': 0.5840,
'AaJb7b': 0.5703,
'AaJb7c': 0.5715,
'AaJb8a': 0.5826,
'AaJb8b': 0.5780,
'AaJb8c': 0.5695,
'AaJb9a': 0.5780,
'AaJb9b': 0.5710,
'AaJb9c': 0.5757,
'AaJbJc': 0.7160,
'AaJbTa': 0.5988,
'AaJbTb': 0.5739,
'AaJbTc': 0.5816,
'AaKa2a': 0.5681,
'AaKa2b': 0.5816,
'AaKa3a': 0.5744,
'AaKa3b': 0.5836,
'AaKa4a': 0.5926,
'AaKa4b': 0.5992,
'AaKa5a': 0.6001,
'AaKa5b': 0.6034,
'AaKa6a': 0.5813,
'AaKa6b': 0.5942,
'AaKa7a': 0.5924,
'AaKa7b': 0.6169,
'AaKa8a': 0.6022,
'AaKa8b': 0.5968,
'AaKa9a': 0.5916,
'AaKa9b': 0.6060,
'AaKaJa': 0.5907,
'AaKaJb': 0.6171,
'AaKaKb': 0.7672,
'AaKaQa': 0.5840,
'AaKaQb': 0.6112,
'AaKaTa': 0.6065,
'AaKaTb': 0.6052,
'AaKb2a': 0.5644,
'AaKb2b': 0.5636,
'AaKb2c': 0.5775,
'AaKb3a': 0.5779,
'AaKb3b': 0.5639,
'AaKb3c': 0.5557,
'AaKb4a': 0.5998,
'AaKb4b': 0.5767,
'AaKb4c': 0.5713,
'AaKb5a': 0.5806,
'AaKb5b': 0.5736,
'AaKb5c': 0.5687,
'AaKb6a': 0.5855,
'AaKb6b': 0.5843,
'AaKb6c': 0.5749,
'AaKb7a': 0.5901,
'AaKb7b': 0.5970,
'AaKb7c': 0.5776,
'AaKb8a': 0.6044,
'AaKb8b': 0.6086,
'AaKb8c': 0.5805,
'AaKb9a': 0.6162,
'AaKb9b': 0.5975,
'AaKb9c': 0.5923,
'AaKbJa': 0.6110,
'AaKbJb': 0.6076,
'AaKbJc': 0.5982,
'AaKbKc': 0.7669,
'AaKbQa': 0.6171,
'AaKbQb': 0.6072,
'AaKbQc': 0.6078,
'AaKbTa': 0.6085,
'AaKbTb': 0.6044,
'AaKbTc': 0.5881,
'AaQa2a': 0.5623,
'AaQa2b': 0.5683,
'AaQa3a': 0.5705,
'AaQa3b': 0.5877,
'AaQa4a': 0.5706,
'AaQa4b': 0.5954,
'AaQa5a': 0.5721,
'AaQa5b': 0.5790,
'AaQa6a': 0.5771,
'AaQa6b': 0.5851,
'AaQa7a': 0.5873,
'AaQa7b': 0.5863,
'AaQa8a': 0.5856,
'AaQa8b': 0.5843,
'AaQa9a': 0.5903,
'AaQa9b': 0.6008,
'AaQaJa': 0.5926,
'AaQaJb': 0.5933,
'AaQaQb': 0.7368,
'AaQaTa': 0.5835,
'AaQaTb': 0.5990,
'AaQb2a': 0.5614,
'AaQb2b': 0.5529,
'AaQb2c': 0.5355,
'AaQb3a': 0.5626,
'AaQb3b': 0.5586,
'AaQb3c': 0.5570,
'AaQb4a': 0.5705,
'AaQb4b': 0.5501,
'AaQb4c': 0.5507,
'AaQb5a': 0.5861,
'AaQb5b': 0.5765,
'AaQb5c': 0.5665,
'AaQb6a': 0.5757,
'AaQb6b': 0.5814,
'AaQb6c': 0.5729,
'AaQb7a': 0.6021,
'AaQb7b': 0.5820,
'AaQb7c': 0.5615,
'AaQb8a': 0.5887,
'AaQb8b': 0.5806,
'AaQb8c': 0.5677,
'AaQb9a': 0.5945,
'AaQb9b': 0.5934,
'AaQb9c': 0.5861,
'AaQbJa': 0.6022,
'AaQbJb': 0.6035,
'AaQbJc': 0.5946,
'AaQbQc': 0.7536,
'AaQbTa': 0.5883,
'AaQbTb': 0.5819,
'AaQbTc': 0.5741,
'AaTa2a': 0.5517,
'AaTa2b': 0.5423,
'AaTa3a': 0.5544,
'AaTa3b': 0.5574,
'AaTa4a': 0.5509,
'AaTa4b': 0.5563,
'AaTa5a': 0.5723,
'AaTa5b': 0.5729,
'AaTa6a': 0.5619,
'AaTa6b': 0.5696,
'AaTa7a': 0.5543,
'AaTa7b': 0.5680,
'AaTa8a': 0.5724,
'AaTa8b': 0.5664,
'AaTa9a': 0.5629,
'AaTa9b': 0.5725,
'AaTaTb': 0.6807,
'AaTb2a': 0.5386,
'AaTb2b': 0.5374,
'AaTb2c': 0.5343,
'AaTb3a': 0.5486,
'AaTb3b': 0.5476,
'AaTb3c': 0.5324,
'AaTb4a': 0.5439,
'AaTb4b': 0.5610,
'AaTb4c': 0.5320,
'AaTb5a': 0.5507,
'AaTb5b': 0.5415,
'AaTb5c': 0.5420,
'AaTb6a': 0.5591,
'AaTb6b': 0.5439,
'AaTb6c': 0.5391,
'AaTb7a': 0.5666,
'AaTb7b': 0.5609,
'AaTb7c': 0.5334,
'AaTb8a': 0.5653,
'AaTb8b': 0.5620,
'AaTb8c': 0.5556,
'AaTb9a': 0.5716,
'AaTb9b': 0.5675,
'AaTb9c': 0.5523,
'AaTbTc': 0.6876,
'Ja2a2b': 0.4540,
'Ja2b2c': 0.4400,
'Ja3a2a': 0.4100,
'Ja3a2b': 0.4189,
'Ja3a3b': 0.4741,
'Ja3b2a': 0.4120,
'Ja3b2b': 0.4064,
'Ja3b2c': 0.4022,
'Ja3b3c': 0.4786,
'Ja4a2a': 0.4266,
'Ja4a2b': 0.4323,
'Ja4a3a': 0.4195,
'Ja4a3b': 0.4370,
'Ja4a4b': 0.5032,
'Ja4b2a': 0.4160,
'Ja4b2b': 0.4146,
'Ja4b2c': 0.3855,
'Ja4b3a': 0.4259,
'Ja4b3b': 0.4144,
'Ja4b3c': 0.3976,
'Ja4b4c': 0.4859,
'Ja5a2a': 0.4283,
'Ja5a2b': 0.4249,
'Ja5a3a': 0.4310,
'Ja5a3b': 0.4283,
'Ja5a4a': 0.4525,
'Ja5a4b': 0.4455,
'Ja5a5b': 0.5280,
'Ja5b2a': 0.4296,
'Ja5b2b': 0.4308,
'Ja5b2c': 0.4036,
'Ja5b3a': 0.4431,
'Ja5b3b': 0.4333,
'Ja5b3c': 0.4188,
'Ja5b4a': 0.4482,
'Ja5b4b': 0.4333,
'Ja5b4c': 0.4273,
'Ja5b5c': 0.5280,
'Ja6a2a': 0.4256,
'Ja6a2b': 0.4196,
'Ja6a3a': 0.4351,
'Ja6a3b': 0.4542,
'Ja6a4a': 0.4479,
'Ja6a4b': 0.4446,
'Ja6a5a': 0.4527,
'Ja6a5b': 0.4637,
'Ja6a6b': 0.5501,
'Ja6b2a': 0.4235,
'Ja6b2b': 0.4225,
'Ja6b2c': 0.4130,
'Ja6b3a': 0.4514,
'Ja6b3b': 0.4441,
'Ja6b3c': 0.4111,
'Ja6b4a': 0.4475,
'Ja6b4b': 0.4400,
'Ja6b4c': 0.4336,
'Ja6b5a': 0.4574,
'Ja6b5b': 0.4420,
'Ja6b5c': 0.4444,
'Ja6b6c': 0.5444,
'Ja7a2a': 0.4400,
'Ja7a2b': 0.4500,
'Ja7a3a': 0.4521,
'Ja7a3b': 0.4570,
'Ja7a4a': 0.4495,
'Ja7a4b': 0.4574,
'Ja7a5a': 0.4721,
'Ja7a5b': 0.4724,
'Ja7a6a': 0.4646,
'Ja7a6b': 0.4724,
'Ja7a7b': 0.5835,
'Ja7b2a': 0.4434,
'Ja7b2b': 0.4329,
'Ja7b2c': 0.4293,
'Ja7b3a': 0.4380,
'Ja7b3b': 0.4300,
'Ja7b3c': 0.4271,
'Ja7b4a': 0.4481,
'Ja7b4b': 0.4532,
'Ja7b4c': 0.4422,
'Ja7b5a': 0.4731,
'Ja7b5b': 0.4685,
'Ja7b5c': 0.4517,
'Ja7b6a': 0.4666,
'Ja7b6b': 0.4552,
'Ja7b6c': 0.4557,
'Ja7b7c': 0.5770,
'Ja8a2a': 0.4676,
'Ja8a2b': 0.4600,
'Ja8a3a': 0.4619,
'Ja8a3b': 0.4616,
'Ja8a4a': 0.4510,
'Ja8a4b': 0.4594,
'Ja8a5a': 0.4799,
'Ja8a5b': 0.4765,
'Ja8a6a': 0.4786,
'Ja8a6b': 0.4764,
'Ja8a7a': 0.4963,
'Ja8a7b': 0.5002,
'Ja8a8b': 0.6188,
'Ja8b2a': 0.4471,
'Ja8b2b': 0.4461,
'Ja8b2c': 0.4547,
'Ja8b3a': 0.4607,
'Ja8b3b': 0.4602,
'Ja8b3c': 0.4455,
'Ja8b4a': 0.4691,
'Ja8b4b': 0.4562,
'Ja8b4c': 0.4602,
'Ja8b5a': 0.4798,
'Ja8b5b': 0.4788,
'Ja8b5c': 0.4602,
'Ja8b6a': 0.4808,
'Ja8b6b': 0.4846,
'Ja8b6c': 0.4542,
'Ja8b7a': 0.4795,
'Ja8b7b': 0.4821,
'Ja8b7c': 0.4655,
'Ja8b8c': 0.6176,
'Ja9a2a': 0.4654,
'Ja9a2b': 0.4761,
'Ja9a3a': 0.4819,
'Ja9a3b': 0.4866,
'Ja9a4a': 0.4905,
'Ja9a4b': 0.4886,
'Ja9a5a': 0.4896,
'Ja9a5b': 0.4984,
'Ja9a6a': 0.4835,
'Ja9a6b': 0.4864,
'Ja9a7a': 0.5040,
'Ja9a7b': 0.5058,
'Ja9a8a': 0.5004,
'Ja9a8b': 0.5054,
'Ja9a9b': 0.6439,
'Ja9b2a': 0.4690,
'Ja9b2b': 0.4698,
'Ja9b2c': 0.4526,
'Ja9b3a': 0.4760,
'Ja9b3b': 0.4894,
'Ja9b3c': 0.4735,
'Ja9b4a': 0.4796,
'Ja9b4b': 0.4723,
'Ja9b4c': 0.4676,
'Ja9b5a': 0.4819,
'Ja9b5b': 0.4870,
'Ja9b5c': 0.4851,
'Ja9b6a': 0.4888,
'Ja9b6b': 0.4794,
'Ja9b6c': 0.4740,
'Ja9b7a': 0.4891,
'Ja9b7b': 0.4971,
'Ja9b7c': 0.4841,
'Ja9b8a': 0.4985,
'Ja9b8b': 0.4943,
'Ja9b8c': 0.4778,
'Ja9b9c': 0.6330,
'JaJb2a': 0.6706,
'JaJb2c': 0.6779,
'JaJb3a': 0.6755,
'JaJb3c': 0.6813,
'JaJb4a': 0.6827,
'JaJb4c': 0.6735,
'JaJb5a': 0.6909,
'JaJb5c': 0.6877,
'JaJb6a': 0.6779,
'JaJb6c': 0.6749,
'JaJb7a': 0.6961,
'JaJb7c': 0.6865,
'JaJb8a': 0.6811,
'JaJb8c': 0.6800,
'JaJb9a': 0.6741,
'JaJb9c': 0.6935,
'JaJbJc': 0.6515,
'JaJbTa': 0.6803,
'JaJbTc': 0.6874,
'JaTa2a': 0.4881,
'JaTa2b': 0.4960,
'JaTa3a': 0.4960,
'JaTa3b': 0.4954,
'JaTa4a': 0.5005,
'JaTa4b': 0.5094,
'JaTa5a': 0.5146,
'JaTa5b': 0.5115,
'JaTa6a': 0.5094,
'JaTa6b': 0.5125,
'JaTa7a': 0.5162,
'JaTa7b': 0.5130,
'JaTa8a': 0.5022,
'JaTa8b': 0.5206,
'JaTa9a': 0.5194,
'JaTa9b': 0.5201,
'JaTaTb': 0.6809,
'JaTb2a': 0.4848,
'JaTb2b': 0.4914,
'JaTb2c': 0.4821,
'JaTb3a': 0.4870,
'JaTb3b': 0.4996,
'JaTb3c': 0.4784,
'JaTb4a': 0.5062,
'JaTb4b': 0.5091,
'JaTb4c': 0.4981,
'JaTb5a': 0.5124,
'JaTb5b': 0.5096,
'JaTb5c': 0.4844,
'JaTb6a': 0.5191,
'JaTb6b': 0.5029,
'JaTb6c': 0.4979,
'JaTb7a': 0.4926,
'JaTb7b': 0.5105,
'JaTb7c': 0.4940,
'JaTb8a': 0.5174,
'JaTb8b': 0.5104,
'JaTb8c': 0.4968,
'JaTb9a': 0.5139,
'JaTb9b': 0.5051,
'JaTb9c': 0.4955,
'JaTbTc': 0.6791,
'Ka2a2b': 0.4662,
'Ka2b2c': 0.4411,
'Ka3a2a': 0.4474,
'Ka3a2b': 0.4509,
'Ka3a3b': 0.4811,
'Ka3b2a': 0.4497,
'Ka3b2b': 0.4510,
'Ka3b2c': 0.4268,
'Ka3b3c': 0.4800,
'Ka4a2a': 0.4547,
'Ka4a2b': 0.4611,
'Ka4a3a': 0.4634,
'Ka4a3b': 0.4724,
'Ka4a4b': 0.5182,
'Ka4b2a': 0.4634,
'Ka4b2b': 0.4664,
'Ka4b2c': 0.4260,
'Ka4b3a': 0.4612,
'Ka4b3b': 0.4504,
'Ka4b3c': 0.4274,
'Ka4b4c': 0.4993,
'Ka5a2a': 0.4510,
'Ka5a2b': 0.4657,
'Ka5a3a': 0.4746,
'Ka5a3b': 0.4575,
'Ka5a4a': 0.4584,
'Ka5a4b': 0.4738,
'Ka5a5b': 0.5282,
'Ka5b2a': 0.4708,
'Ka5b2b': 0.4479,
'Ka5b2c': 0.4313,
'Ka5b3a': 0.4819,
'Ka5b3b': 0.4514,
'Ka5b3c': 0.4455,
'Ka5b4a': 0.4740,
'Ka5b4b': 0.4650,
'Ka5b4c': 0.4329,
'Ka5b5c': 0.5104,
'Ka6a2a': 0.4624,
'Ka6a2b': 0.4745,
'Ka6a3a': 0.4711,
'Ka6a3b': 0.4731,
'Ka6a4a': 0.4813,
'Ka6a4b': 0.4769,
'Ka6a5a': 0.4796,
'Ka6a5b': 0.4880,
'Ka6a6b': 0.5683,
'Ka6b2a': 0.4682,
'Ka6b2b': 0.4713,
'Ka6b2c': 0.4545,
'Ka6b3a': 0.4590,
'Ka6b3b': 0.4629,
'Ka6b3c': 0.4561,
'Ka6b4a': 0.4680,
'Ka6b4b': 0.4788,
'Ka6b4c': 0.4600,
'Ka6b5a': 0.4811,
'Ka6b5b': 0.4751,
'Ka6b5c': 0.4798,
'Ka6b6c': 0.5456,
'Ka7a2a': 0.4688,
'Ka7a2b': 0.4864,
'Ka7a3a': 0.4913,
'Ka7a3b': 0.4855,
'Ka7a4a': 0.4799,
'Ka7a4b': 0.4935,
'Ka7a5a': 0.4999,
'Ka7a5b': 0.5055,
'Ka7a6a': 0.5029,
'Ka7a6b': 0.4901,
'Ka7a7b': 0.6010,
'Ka7b2a': 0.4855,
'Ka7b2b': 0.4596,
'Ka7b2c': 0.4664,
'Ka7b3a': 0.4650,
'Ka7b3b': 0.4851,
'Ka7b3c': 0.4754,
'Ka7b4a': 0.4800,
'Ka7b4b': 0.4820,
'Ka7b4c': 0.4639,
'Ka7b5a': 0.5029,
'Ka7b5b': 0.4863,
'Ka7b5c': 0.4824,
'Ka7b6a': 0.4979,
'Ka7b6b': 0.5012,
'Ka7b6c': 0.4825,
'Ka7b7c': 0.5705,
'Ka8a2a': 0.4978,
'Ka8a2b': 0.4916,
'Ka8a3a': 0.4890,
'Ka8a3b': 0.4996,
'Ka8a4a': 0.5018,
'Ka8a4b': 0.4961,
'Ka8a5a': 0.5030,
'Ka8a5b': 0.5169,
'Ka8a6a': 0.4938,
'Ka8a6b': 0.5052,
'Ka8a7a': 0.5161,
'Ka8a7b': 0.5229,
'Ka8a8b': 0.6101,
'Ka8b2a': 0.4851,
'Ka8b2b': 0.4750,
'Ka8b2c': 0.4640,
'Ka8b3a': 0.4725,
'Ka8b3b': 0.4946,
'Ka8b3c': 0.4718,
'Ka8b4a': 0.4885,
'Ka8b4b': 0.4889,
'Ka8b4c': 0.4696,
'Ka8b5a': 0.4856,
'Ka8b5b': 0.4884,
'Ka8b5c': 0.4899,
'Ka8b6a': 0.5026,
'Ka8b6b': 0.4864,
'Ka8b6c': 0.4760,
'Ka8b7a': 0.4975,
'Ka8b7b': 0.5164,
'Ka8b7c': 0.4885,
'Ka8b8c': 0.6195,
'Ka9a2a': 0.5155,
'Ka9a2b': 0.5146,
'Ka9a3a': 0.5094,
'Ka9a3b': 0.5086,
'Ka9a4a': 0.5106,
'Ka9a4b': 0.5126,
'Ka9a5a': 0.5084,
'Ka9a5b': 0.5250,
'Ka9a6a': 0.5216,
'Ka9a6b': 0.5317,
'Ka9a7a': 0.5102,
'Ka9a7b': 0.5344,
'Ka9a8a': 0.5282,
'Ka9a8b': 0.5540,
'Ka9a9b': 0.6465,
'Ka9b2a': 0.4971,
'Ka9b2b': 0.4894,
'Ka9b2c': 0.4756,
'Ka9b3a': 0.5040,
'Ka9b3b': 0.4943,
'Ka9b3c': 0.4783,
'Ka9b4a': 0.5149,
'Ka9b4b': 0.4919,
'Ka9b4c': 0.4991,
'Ka9b5a': 0.5125,
'Ka9b5b': 0.5128,
'Ka9b5c': 0.4888,
'Ka9b6a': 0.5250,
'Ka9b6b': 0.5168,
'Ka9b6c': 0.5006,
'Ka9b7a': 0.5335,
'Ka9b7b': 0.5302,
'Ka9b7c': 0.5002,
'Ka9b8a': 0.5341,
'Ka9b8b': 0.5329,
'Ka9b8c': 0.5160,
'Ka9b9c': 0.6375,
'KaJa2a': 0.5255,
'KaJa2b': 0.5367,
'KaJa3a': 0.5315,
'KaJa3b': 0.5376,
'KaJa4a': 0.5453,
'KaJa4b': 0.5433,
'KaJa5a': 0.5491,
'KaJa5b': 0.5496,
'KaJa6a': 0.5417,
'KaJa6b': 0.5427,
'KaJa7a': 0.5454,
'KaJa7b': 0.5563,
'KaJa8a': 0.5490,
'KaJa8b': 0.5479,
'KaJa9a': 0.5645,
'KaJa9b': 0.5559,
'KaJaJb': 0.7121,
'KaJaTa': 0.5644,
'KaJaTb': 0.5675,
'KaJb2a': 0.5319,
'KaJb2b': 0.5269,
'KaJb2c': 0.5145,
'KaJb3a': 0.5401,
'KaJb3b': 0.5269,
'KaJb3c': 0.5347,
'KaJb4a': 0.5316,
'KaJb4b': 0.5138,
'KaJb4c': 0.5168,
'KaJb5a': 0.5407,
'KaJb5b': 0.5331,
'KaJb5c': 0.5331,
'KaJb6a': 0.5425,
'KaJb6b': 0.5251,
'KaJb6c': 0.5294,
'KaJb7a': 0.5464,
'KaJb7b': 0.5537,
'KaJb7c': 0.5449,
'KaJb8a': 0.5604,
'KaJb8b': 0.5581,
'KaJb8c': 0.5339,
'KaJb9a': 0.5580,
'KaJb9b': 0.5664,
'KaJb9c': 0.5251,
'KaJbJc': 0.6931,
'KaJbTa': 0.5637,
'KaJbTb': 0.5579,
'KaJbTc': 0.5574,
'KaKb2a': 0.7529,
'KaKb2c': 0.7541,
'KaKb3a': 0.7424,
'KaKb3c': 0.7495,
'KaKb4a': 0.7525,
'KaKb4c': 0.7488,
'KaKb5a': 0.7495,
'KaKb5c': 0.7555,
'KaKb6a': 0.7492,
'KaKb6c': 0.7564,
'KaKb7a': 0.7518,
'KaKb7c': 0.7360,
'KaKb8a': 0.7550,
'KaKb8c': 0.7520,
'KaKb9a': 0.7492,
'KaKb9c': 0.7578,
'KaKbJa': 0.7545,
'KaKbJc': 0.7518,
'KaKbKc': 0.7251,
'KaKbQa': 0.7419,
'KaKbQc': 0.7455,
'KaKbTa': 0.7544,
'KaKbTc': 0.7474,
'KaQa2a': 0.5411,
'KaQa2b': 0.5455,
'KaQa3a': 0.5320,
'KaQa3b': 0.5501,
'KaQa4a': 0.5375,
'KaQa4b': 0.5669,
'KaQa5a': 0.5503,
'KaQa5b': 0.5554,
'KaQa6a': 0.5547,
'KaQa6b': 0.5650,
'KaQa7a': 0.5715,
'KaQa7b': 0.5670,
'KaQa8a': 0.5740,
'KaQa8b': 0.5736,
'KaQa9a': 0.5829,
'KaQa9b': 0.5613,
'KaQaJa': 0.5730,
'KaQaJb': 0.5745,
'KaQaQb': 0.7431,
'KaQaTa': 0.5736,
'KaQaTb': 0.5745,
'KaQb2a': 0.5272,
'KaQb2b': 0.5331,
'KaQb2c': 0.5292,
'KaQb3a': 0.5337,
'KaQb3b': 0.5410,
'KaQb3c': 0.5209,
'KaQb4a': 0.5527,
'KaQb4b': 0.5509,
'KaQb4c': 0.5465,
'KaQb5a': 0.5490,
'KaQb5b': 0.5414,
'KaQb5c': 0.5600,
'KaQb6a': 0.5514,
'KaQb6b': 0.5415,
'KaQb6c': 0.5397,
'KaQb7a': 0.5617,
'KaQb7b': 0.5607,
'KaQb7c': 0.5491,
'KaQb8a': 0.5719,
'KaQb8b': 0.5643,
'KaQb8c': 0.5377,
'KaQb9a': 0.5589,
'KaQb9b': 0.5597,
'KaQb9c': 0.5426,
'KaQbJa': 0.5660,
'KaQbJb': 0.5649,
'KaQbJc': 0.5569,
'KaQbQc': 0.7275,
'KaQbTa': 0.5619,
'KaQbTb': 0.5847,
'KaQbTc': 0.5427,
'KaTa2a': 0.5081,
'KaTa2b': 0.5212,
'KaTa3a': 0.5296,
'KaTa3b': 0.5420,
'KaTa4a': 0.5254,
'KaTa4b': 0.5367,
'KaTa5a': 0.5375,
'KaTa5b': 0.5312,
'KaTa6a': 0.5447,
'KaTa6b': 0.5420,
'KaTa7a': 0.5331,
'KaTa7b': 0.5524,
'KaTa8a': 0.5444,
'KaTa8b': 0.5689,
'KaTa9a': 0.5494,
'KaTa9b': 0.5537,
'KaTaTb': 0.6911,
'KaTb2a': 0.5191,
'KaTb2b': 0.5185,
'KaTb2c': 0.4978,
'KaTb3a': 0.5274,
'KaTb3b': 0.5291,
'KaTb3c': 0.5159,
'KaTb4a': 0.5276,
'KaTb4b': 0.5178,
'KaTb4c': 0.5216,
'KaTb5a': 0.5324,
'KaTb5b': 0.5250,
'KaTb5c': 0.5252,
'KaTb6a': 0.5340,
'KaTb6b': 0.5333,
'KaTb6c': 0.5094,
'KaTb7a': 0.5409,
'KaTb7b': 0.5397,
'KaTb7c': 0.5256,
'KaTb8a': 0.5460,
'KaTb8b': 0.5411,
'KaTb8c': 0.5304,
'KaTb9a': 0.5699,
'KaTb9b': 0.5591,
'KaTb9c': 0.5465,
'KaTbTc': 0.6921,
'Qa2a2b': 0.4544,
'Qa2b2c': 0.4360,
'Qa3a2a': 0.4206,
'Qa3a2b': 0.4271,
'Qa3a3b': 0.4841,
'Qa3b2a': 0.4246,
'Qa3b2b': 0.4320,
'Qa3b2c': 0.4004,
'Qa3b3c': 0.4580,
'Qa4a2a': 0.4310,
'Qa4a2b': 0.4331,
'Qa4a3a': 0.4400,
'Qa4a3b': 0.4456,
'Qa4a4b': 0.4963,
'Qa4b2a': 0.4315,
'Qa4b2b': 0.4339,
'Qa4b2c': 0.4268,
'Qa4b3a': 0.4304,
'Qa4b3b': 0.4489,
'Qa4b3c': 0.4189,
'Qa4b4c': 0.4920,
'Qa5a2a': 0.4430,
'Qa5a2b': 0.4539,
'Qa5a3a': 0.4496,
'Qa5a3b': 0.4560,
'Qa5a4a': 0.4599,
'Qa5a4b': 0.4597,
'Qa5a5b': 0.5286,
'Qa5b2a': 0.4382,
'Qa5b2b': 0.4387,
'Qa5b2c': 0.4284,
'Qa5b3a': 0.4529,
'Qa5b3b': 0.4348,
'Qa5b3c': 0.4299,
'Qa5b4a': 0.4620,
'Qa5b4b': 0.4511,
'Qa5b4c': 0.4514,
'Qa5b5c': 0.5308,
'Qa6a2a': 0.4459,
'Qa6a2b': 0.4695,
'Qa6a3a': 0.4577,
'Qa6a3b': 0.4609,
'Qa6a4a': 0.4586,
'Qa6a4b': 0.4649,
'Qa6a5a': 0.4590,
'Qa6a5b': 0.4635,
'Qa6a6b': 0.5547,
'Qa6b2a': 0.4572,
'Qa6b2b': 0.4441,
'Qa6b2c': 0.4223,
'Qa6b3a': 0.4454,
'Qa6b3b': 0.4344,
'Qa6b3c': 0.4374,
'Qa6b4a': 0.4599,
'Qa6b4b': 0.4571,
'Qa6b4c': 0.4288,
'Qa6b5a': 0.4774,
'Qa6b5b': 0.4515,
'Qa6b5c': 0.4541,
'Qa6b6c': 0.5617,
'Qa7a2a': 0.4472,
'Qa7a2b': 0.4559,
'Qa7a3a': 0.4599,
'Qa7a3b': 0.4525,
'Qa7a4a': 0.4565,
'Qa7a4b': 0.4642,
'Qa7a5a': 0.4846,
'Qa7a5b': 0.4764,
'Qa7a6a': 0.4858,
'Qa7a6b': 0.4854,
'Qa7a7b': 0.5949,
'Qa7b2a': 0.4395,
'Qa7b2b': 0.4375,
'Qa7b2c': 0.4358,
'Qa7b3a': 0.4701,
'Qa7b3b': 0.4426,
'Qa7b3c': 0.4404,
'Qa7b4a': 0.4555,
'Qa7b4b': 0.4622,
'Qa7b4c': 0.4525,
'Qa7b5a': 0.4670,
'Qa7b5b': 0.4624,
'Qa7b5c': 0.4477,
'Qa7b6a': 0.4795,
'Qa7b6b': 0.4816,
'Qa7b6c': 0.4675,
'Qa7b7c': 0.5756,
'Qa8a2a': 0.4719,
'Qa8a2b': 0.4753,
'Qa8a3a': 0.4764,
'Qa8a3b': 0.4647,
'Qa8a4a': 0.4844,
'Qa8a4b': 0.4975,
'Qa8a5a': 0.4771,
'Qa8a5b': 0.4799,
'Qa8a6a': 0.4886,
'Qa8a6b': 0.5122,
'Qa8a7a': 0.4849,
'Qa8a7b': 0.4966,
'Qa8a8b': 0.6054,
'Qa8b2a': 0.4671,
'Qa8b2b': 0.4665,
'Qa8b2c': 0.4542,
'Qa8b3a': 0.4639,
'Qa8b3b': 0.4738,
'Qa8b3c': 0.4531,
'Qa8b4a': 0.4819,
'Qa8b4b': 0.4700,
'Qa8b4c': 0.4536,
'Qa8b5a': 0.4856,
'Qa8b5b': 0.4746,
'Qa8b5c': 0.4659,
'Qa8b6a': 0.5064,
'Qa8b6b': 0.4879,
'Qa8b6c': 0.4616,
'Qa8b7a': 0.4919,
'Qa8b7b': 0.4956,
'Qa8b7c': 0.4809,
'Qa8b8c': 0.6004,
'Qa9a2a': 0.4860,
'Qa9a2b': 0.4970,
'Qa9a3a': 0.5025,
'Qa9a3b': 0.5031,
'Qa9a4a': 0.4999,
'Qa9a4b': 0.5070,
'Qa9a5a': 0.4966,
'Qa9a5b': 0.5101,
'Qa9a6a': 0.5061,
'Qa9a6b': 0.5105,
'Qa9a7a': 0.5156,
'Qa9a7b': 0.5345,
'Qa9a8a': 0.5185,
'Qa9a8b': 0.5160,
'Qa9a9b': 0.6520,
'Qa9b2a': 0.4824,
'Qa9b2b': 0.4700,
'Qa9b2c': 0.4718,
'Qa9b3a': 0.4844,
'Qa9b3b': 0.4728,
'Qa9b3c': 0.4773,
'Qa9b4a': 0.4974,
'Qa9b4b': 0.4773,
'Qa9b4c': 0.4684,
'Qa9b5a': 0.4983,
'Qa9b5b': 0.4953,
'Qa9b5c': 0.4974,
'Qa9b6a': 0.5136,
'Qa9b6b': 0.5045,
'Qa9b6c': 0.4919,
'Qa9b7a': 0.5005,
'Qa9b7b': 0.5168,
'Qa9b7c': 0.4795,
'Qa9b8a': 0.5166,
'Qa9b8b': 0.5132,
'Qa9b8c': 0.5072,
'Qa9b9c': 0.6456,
'QaJa2a': 0.5252,
'QaJa2b': 0.5205,
'QaJa3a': 0.5105,
'QaJa3b': 0.5149,
'QaJa4a': 0.5062,
'QaJa4b': 0.5175,
'QaJa5a': 0.5214,
'QaJa5b': 0.5389,
'QaJa6a': 0.5309,
'QaJa6b': 0.5400,
'QaJa7a': 0.5351,
'QaJa7b': 0.5575,
'QaJa8a': 0.5296,
'QaJa8b': 0.5445,
'QaJa9a': 0.5346,
'QaJa9b': 0.5384,
'QaJaJb': 0.7113,
'QaJaTa': 0.5367,
'QaJaTb': 0.5395,
'QaJb2a': 0.5090,
'QaJb2b': 0.5119,
'QaJb2c': 0.5040,
'QaJb3a': 0.5170,
'QaJb3b': 0.5100,
'QaJb3c': 0.5032,
'QaJb4a': 0.5279,
'QaJb4b': 0.5259,
'QaJb4c': 0.5317,
'QaJb5a': 0.5184,
'QaJb5b': 0.5319,
'QaJb5c': 0.5179,
'QaJb6a': 0.5284,
'QaJb6b': 0.5394,
'QaJb6c': 0.5155,
'QaJb7a': 0.5327,
'QaJb7b': 0.5373,
'QaJb7c': 0.4988,
'QaJb8a': 0.5311,
'QaJb8b': 0.5419,
'QaJb8c': 0.5299,
'QaJb9a': 0.5326,
'QaJb9b': 0.5333,
'QaJb9c': 0.5074,
'QaJbJc': 0.7049,
'QaJbTa': 0.5258,
'QaJbTb': 0.5379,
'QaJbTc': 0.5252,
'QaQb2a': 0.7029,
'QaQb2c': 0.7134,
'QaQb3a': 0.7218,
'QaQb3c': 0.7215,
'QaQb4a': 0.7195,
'QaQb4c': 0.7198,
'QaQb5a': 0.7163,
'QaQb5c': 0.7229,
'QaQb6a': 0.7288,
'QaQb6c': 0.7184,
'QaQb7a': 0.7221,
'QaQb7c': 0.7218,
'QaQb8a': 0.7131,
'QaQb8c': 0.7185,
'QaQb9a': 0.7137,
'QaQb9c': 0.7035,
'QaQbJa': 0.7272,
'QaQbJc': 0.6997,
'QaQbQc': 0.6824,
'QaQbTa': 0.7186,
'QaQbTc': 0.7199,
'QaTa2a': 0.5110,
'QaTa2b': 0.5016,
'QaTa3a': 0.5125,
'QaTa3b': 0.5010,
'QaTa4a': 0.5066,
'QaTa4b': 0.5254,
'QaTa5a': 0.5124,
'QaTa5b': 0.5282,
'QaTa6a': 0.5218,
'QaTa6b': 0.5309,
'QaTa7a': 0.5290,
'QaTa7b': 0.5299,
'QaTa8a': 0.5206,
'QaTa8b': 0.5354,
'QaTa9a': 0.5374,
'QaTa9b': 0.5341,
'QaTaTb': 0.6827,
'QaTb2a': 0.5080,
'QaTb2b': 0.4939,
'QaTb2c': 0.4898,
'QaTb3a': 0.5249,
'QaTb3b': 0.5086,
'QaTb3c': 0.5085,
'QaTb4a': 0.5071,
'QaTb4b': 0.4981,
'QaTb4c': 0.5008,
'QaTb5a': 0.5171,
'QaTb5b': 0.5288,
'QaTb5c': 0.5128,
'QaTb6a': 0.5279,
'QaTb6b': 0.5286,
'QaTb6c': 0.5009,
'QaTb7a': 0.5249,
'QaTb7b': 0.5294,
'QaTb7c': 0.5198,
'QaTb8a': 0.5320,
'QaTb8b': 0.5250,
'QaTb8c': 0.5258,
'QaTb9a': 0.5235,
'QaTb9b': 0.5208,
'QaTb9c': 0.5258,
'QaTbTc': 0.6604,
'Ta2a2b': 0.4537,
'Ta2b2c': 0.4424,
'Ta3a2a': 0.3981,
'Ta3a2b': 0.4060,
'Ta3a3b': 0.4755,
'Ta3b2a': 0.3981,
'Ta3b2b': 0.3836,
'Ta3b2c': 0.3812,
'Ta3b3c': 0.4676,
'Ta4a2a': 0.4019,
'Ta4a2b': 0.4081,
'Ta4a3a': 0.4201,
'Ta4a3b': 0.4225,
'Ta4a4b': 0.4990,
'Ta4b2a': 0.4094,
'Ta4b2b': 0.3931,
'Ta4b2c': 0.3771,
'Ta4b3a': 0.4238,
'Ta4b3b': 0.4032,
'Ta4b3c': 0.3964,
'Ta4b4c': 0.4856,
'Ta5a2a': 0.4079,
'Ta5a2b': 0.4130,
'Ta5a3a': 0.4170,
'Ta5a3b': 0.4306,
'Ta5a4a': 0.4293,
'Ta5a4b': 0.4291,
'Ta5a5b': 0.5199,
'Ta5b2a': 0.4130,
'Ta5b2b': 0.3939,
'Ta5b2c': 0.3726,
'Ta5b3a': 0.4275,
'Ta5b3b': 0.4073,
'Ta5b3c': 0.3927,
'Ta5b4a': 0.4366,
'Ta5b4b': 0.4412,
'Ta5b4c': 0.4189,
'Ta5b5c': 0.5102,
'Ta6a2a': 0.4179,
'Ta6a2b': 0.4270,
'Ta6a3a': 0.4191,
'Ta6a3b': 0.4429,
'Ta6a4a': 0.4283,
'Ta6a4b': 0.4310,
'Ta6a5a': 0.4582,
'Ta6a5b': 0.4490,
'Ta6a6b': 0.5534,
'Ta6b2a': 0.4045,
'Ta6b2b': 0.4129,
'Ta6b2c': 0.4068,
'Ta6b3a': 0.4178,
'Ta6b3b': 0.4093,
'Ta6b3c': 0.4123,
'Ta6b4a': 0.4339,
'Ta6b4b': 0.4348,
'Ta6b4c': 0.4090,
'Ta6b5a': 0.4447,
'Ta6b5b': 0.4529,
'Ta6b5c': 0.4278,
'Ta6b6c': 0.5339,
'Ta7a2a': 0.4305,
'Ta7a2b': 0.4334,
'Ta7a3a': 0.4394,
'Ta7a3b': 0.4512,
'Ta7a4a': 0.4350,
'Ta7a4b': 0.4576,
'Ta7a5a': 0.4594,
'Ta7a5b': 0.4614,
'Ta7a6a': 0.4532,
'Ta7a6b': 0.4631,
'Ta7a7b': 0.5664,
'Ta7b2a': 0.4224,
'Ta7b2b': 0.4316,
'Ta7b2c': 0.4253,
'Ta7b3a': 0.4416,
'Ta7b3b': 0.4221,
'Ta7b3c': 0.4124,
'Ta7b4a': 0.4599,
'Ta7b4b': 0.4412,
'Ta7b4c': 0.4243,
'Ta7b5a': 0.4547,
'Ta7b5b': 0.4582,
'Ta7b5c': 0.4419,
'Ta7b6a': 0.4724,
'Ta7b6b': 0.4765,
'Ta7b6c': 0.4335,
'Ta7b7c': 0.5593,
'Ta8a2a': 0.4464,
'Ta8a2b': 0.4581,
'Ta8a3a': 0.4596,
'Ta8a3b': 0.4542,
'Ta8a4a': 0.4661,
'Ta8a4b': 0.4840,
'Ta8a5a': 0.4509,
'Ta8a5b': 0.4690,
'Ta8a6a': 0.4759,
'Ta8a6b': 0.4475,
'Ta8a7a': 0.4682,
'Ta8a7b': 0.4715,
'Ta8a8b': 0.6109,
'Ta8b2a': 0.4437,
'Ta8b2b': 0.4462,
'Ta8b2c': 0.4280,
'Ta8b3a': 0.4482,
'Ta8b3b': 0.4555,
'Ta8b3c': 0.4449,
'Ta8b4a': 0.4665,
'Ta8b4b': 0.4582,
'Ta8b4c': 0.4415,
'Ta8b5a': 0.4595,
'Ta8b5b': 0.4572,
'Ta8b5c': 0.4555,
'Ta8b6a': 0.4693,
'Ta8b6b': 0.4626,
'Ta8b6c': 0.4442,
'Ta8b7a': 0.4698,
'Ta8b7b': 0.4664,
'Ta8b7c': 0.4570,
'Ta8b8c': 0.6016,
'Ta9a2a': 0.4703,
'Ta9a2b': 0.4761,
'Ta9a3a': 0.4655,
'Ta9a3b': 0.4898,
'Ta9a4a': 0.4834,
'Ta9a4b': 0.4878,
'Ta9a5a': 0.4768,
'Ta9a5b': 0.4788,
'Ta9a6a': 0.4799,
'Ta9a6b': 0.4804,
'Ta9a7a': 0.4905,
'Ta9a7b': 0.4910,
'Ta9a8a': 0.4705,
'Ta9a8b': 0.4898,
'Ta9a9b': 0.6290,
'Ta9b2a': 0.4718,
'Ta9b2b': 0.4599,
'Ta9b2c': 0.4521,
'Ta9b3a': 0.4743,
'Ta9b3b': 0.4784,
'Ta9b3c': 0.4585,
'Ta9b4a': 0.4829,
'Ta9b4b': 0.4774,
'Ta9b4c': 0.4556,
'Ta9b5a': 0.4800,
'Ta9b5b': 0.4701,
'Ta9b5c': 0.4679,
'Ta9b6a': 0.4770,
'Ta9b6b': 0.4851,
'Ta9b6c': 0.4611,
'Ta9b7a': 0.4826,
'Ta9b7b': 0.4779,
'Ta9b7c': 0.4713,
'Ta9b8a': 0.4849,
'Ta9b8b': 0.4734,
'Ta9b8c': 0.4569,
'Ta9b9c': 0.6374,
'TaTb2a': 0.6356,
'TaTb2c': 0.6606,
'TaTb3a': 0.6526,
'TaTb3c': 0.6530,
'TaTb4a': 0.6492,
'TaTb4c': 0.6501,
'TaTb5a': 0.6486,
'TaTb5c': 0.6561,
'TaTb6a': 0.6489,
'TaTb6c': 0.6426,
'TaTb7a': 0.6504,
'TaTb7c': 0.6510,
'TaTb8a': 0.6445,
'TaTb8c': 0.6462,
'TaTb9a': 0.6434,
'TaTb9c': 0.6647,
'TaTbTc': 0.6399,
}

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
# (dev-only BOTP parameter override removed by tools/build.py)

DBG = False  # dev-only debug disabled in build
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
