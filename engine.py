# -*- coding: utf-8 -*-
"""M0 · 日本麻将四人局最小内核（隔离开发，不接生产实例）

分层
  L1 牌理引擎  vendor_mahjong（零依赖，取自 mahjong_guess）
  L2 规则层    和牌判定 / 役种 / 简化点数
  L3 状态机    牌墙 / 发牌 / 摸打 / 轮转 / 流局 / 结算
  L4 玩家策略  最小 AI（向听最小化打牌）；真人接口留白

M0 明确不做（留给后续里程碑）
  - 鸣牌（吃/碰/杠）、立直/振听、宝牌/里宝
  - 符数精算、连庄、多家和/头跳
  - 群内交互、SQLite 持久化、观战渲染

牌表示：与 vendor_mahjong 一致，(suit, num)。suit ∈ {'w','p','s','z'}。
"""

import random

import vendor_mahjong as MJ

# ---------------------------------------------------------------- 基础

IDX = {t: i for i, t in enumerate(MJ.ALL_TILES)}          # 34 种牌 → 0..33
TILE_OF = {i: t for t, i in IDX.items()}
WIND_OF_SEAT = {0: 1, 1: 2, 2: 3, 3: 4}                   # 东南西北
ROUND_WIND = 1                                            # 东场


def build_wall():
    """136 张全牌墙。"""
    return [t for t in MJ.ALL_TILES for _ in range(4)]


def tile_name(t):
    return MJ.tile_str(t)


def hand_text(tiles):
    return " ".join(tile_name(t) for t in sorted(tiles, key=lambda x: IDX[x]))


def _counts34(tiles):
    c = [0] * 34
    for t in tiles:
        c[IDX[t]] += 1
    return c


# ---------------------------------------------------------------- L2 向听

def _shanten_standard(counts):
    """标准型向听：8 - 2*面子 - 搭子 - 将。"""
    best = 8

    def rec(i, melds, taatsu, pair):
        nonlocal best
        if melds + taatsu > 4:
            return
        if i >= 34:
            v = 8 - 2 * melds - taatsu - pair
            if v < best:
                best = v
            return
        if counts[i] == 0:
            rec(i + 1, melds, taatsu, pair)
            return

        is_num = i < 27
        num = i % 9 if is_num else 0

        if counts[i] >= 3:                                   # 刻子
            counts[i] -= 3
            rec(i, melds + 1, taatsu, pair)
            counts[i] += 3
        if counts[i] >= 2:                                   # 对子 → 将 / 搭子
            counts[i] -= 2
            if not pair:
                rec(i, melds, taatsu, 1)
            rec(i, melds, taatsu + 1, pair)
            counts[i] += 2
        if is_num and num <= 6 and counts[i + 1] and counts[i + 2]:   # 顺子
            counts[i] -= 1
            counts[i + 1] -= 1
            counts[i + 2] -= 1
            rec(i, melds + 1, taatsu, pair)
            counts[i] += 1
            counts[i + 1] += 1
            counts[i + 2] += 1
        if is_num and num <= 7 and counts[i + 1]:            # 两面/边张搭子
            counts[i] -= 1
            counts[i + 1] -= 1
            rec(i, melds, taatsu + 1, pair)
            counts[i] += 1
            counts[i + 1] += 1
        if is_num and num <= 6 and counts[i + 2]:            # 嵌张搭子
            counts[i] -= 1
            counts[i + 2] -= 1
            rec(i, melds, taatsu + 1, pair)
            counts[i] += 1
            counts[i + 2] += 1

        counts[i] -= 1                                       # 弃掉这一张再看
        rec(i, melds, taatsu, pair)
        counts[i] += 1

    rec(0, 0, 0, 0)
    return best


ORPHAN_IDX = set()
for _s in "wps":
    ORPHAN_IDX.add(IDX[(_s, 1)])
    ORPHAN_IDX.add(IDX[(_s, 9)])
for _n in range(1, 8):
    ORPHAN_IDX.add(IDX[("z", _n)])


def _shanten_chiitoi(counts):
    kinds = sum(1 for c in counts if c >= 1)
    pairs = sum(1 for c in counts if c >= 2)
    return 6 - pairs + max(0, 7 - kinds)


def _shanten_kokushi(counts):
    have = sum(1 for i in ORPHAN_IDX if counts[i] >= 1)
    pair = any(counts[i] >= 2 for i in ORPHAN_IDX)
    return 13 - have - (1 if pair else 0)


_LIB_SUIT = {"w": "m", "p": "p", "s": "s", "z": "z"}


def _to_lib_str(tiles):
    parts = []
    for suit in "wpsz":
        nums = sorted(n for s, n in tiles if s == suit)
        if nums:
            parts.append("".join(str(n) for n in nums) + _LIB_SUIT[suit])
    return "".join(parts)


def _shanten_fallback(tiles):
    c = _counts34(tiles)
    return min(_shanten_standard(c), _shanten_chiitoi(c), _shanten_kokushi(c))


shanten = _shanten_fallback

try:                                                    # L1 主引擎：mahjong 库
    from mahjong.shanten import Shanten as _LibShanten

    def _lib_shanten(tiles):
        # mahjong 库要 34 位计数数组（1-9m/1-9p/1-9s/东南西北白发中）
        return _LibShanten.calculate_shanten(_counts34(tiles))

    _SMOKE = [("w", 1), ("w", 9), ("p", 1), ("p", 9), ("s", 1), ("s", 9),
              ("z", 1), ("z", 2), ("z", 3), ("z", 4), ("z", 5), ("z", 6), ("z", 7)]
    assert _lib_shanten(_SMOKE) == 0                    # 国士十三面 → 听牌
    shanten = _lib_shanten
    ENGINE_SOURCE = "mahjong 库（主）"
except Exception:                                       # noqa: BLE001
    ENGINE_SOURCE = "零依赖自研（备胎）"


# ---------------------------------------------------------------- L2 役 / 点数

HAN_TABLE = {
    "役牌": 1, "平和": 1, "断幺九": 1, "一盃口": 1,
    "对对和": 2, "七对子": 2, "三色同顺": 2, "三色同刻": 2, "一气通贯": 2,
    "混一色": 3,
}
YAKUMAN = {"国士无双", "字一色", "小四喜"}

# 简化点数表（不计符，按 30 符闭合手牌基准）
_RON_BASE = {1: 1000, 2: 2000, 3: 3900, 4: 7700}
_MANGAN = {5: 8000, 6: 12000, 7: 12000, 8: 16000, 9: 16000, 10: 16000,
           11: 24000, 12: 24000}


def judge(hand, seat, tsumo=False):
    """返回 (yaku, han, yakuman, points) 或 None（无役/未和）。"""
    seat_wind = WIND_OF_SEAT[seat]
    yaku = MJ.find_yaku(hand, seat_wind=seat_wind, round_wind=ROUND_WIND)
    if not yaku:
        return None
    han, yakuman = 0, False
    for y in yaku:
        if y in YAKUMAN:
            yakuman = True
        else:
            han += HAN_TABLE.get(y, 0)
    if yakuman:
        points = 32000
    elif han >= 13:
        points = 32000
    else:
        points = _MANGAN.get(han) or _RON_BASE.get(han, 0)
    if points == 0:
        return None
    if seat == 0:                                   # 庄家 ×1.5
        points = int(points * 1.5)
    return yaku, han, yakuman, points


# ---------------------------------------------------------------- L3 状态机

class Player(object):
    def __init__(self, seat, name=None, is_ai=True):
        self.seat = seat
        self.name = name or "P%d" % seat
        self.is_ai = is_ai
        self.hand = []
        self.discards = []

    def sort(self):
        self.hand.sort(key=lambda t: IDX[t])


def _usefulness(counts, i):
    """这张牌在手牌里的“有用度”，越低越该打。"""
    score = (counts[i] - 1) * 2
    if i < 27:
        num = i % 9
        for d in (-2, -1, 1, 2):
            if 0 <= num + d <= 8 and counts[i + d]:
                score += 1
    return score


def choose_discard(hand, rng):
    """最小化打后向听；并列时打最孤立的牌。"""
    best = None
    for t in sorted(set(hand), key=lambda x: IDX[x]):
        rest = list(hand)
        rest.remove(t)
        c = _counts34(rest)
        key = (shanten(rest), _usefulness(c, IDX[t]), rng.random())
        if best is None or key < best[0]:
            best = (key, t)
    return best[1]


def play_hand(seed=None, dealer=0, verbose=False, require_yaku=True,
              names=None, ai=None):
    """打完一局。返回结算 dict。"""
    rng = random.Random(seed)
    wall = build_wall()
    rng.shuffle(wall)

    players = [Player(i, (names or [None] * 4)[i]) for i in range(4)]
    for _ in range(13):
        for p in players:
            p.hand.append(wall.pop())
    for p in players:
        p.sort()

    log = []

    def emit(s):
        if verbose:
            log.append(s)

    for p in players:
        emit("P%d 配牌: %s" % (p.seat, hand_text(p.hand)))

    turn = dealer
    turn_no = 0
    while wall:
        p = players[turn]
        drawn = wall.pop()
        p.hand.append(drawn)
        p.sort()
        turn_no += 1
        emit("P%d 摸 %s | 手牌 %s" % (p.seat, tile_name(drawn), hand_text(p.hand)))

        if MJ.is_valid_hand(p.hand):
            r = judge(p.hand, p.seat, tsumo=True)
            if r or not require_yaku:
                yaku, han, yakuman, pts = r if r else (["无役(测试)"], 0, False, 0)
                return _result("tsumo", p.seat, None, p.hand, drawn, yaku, han,
                               yakuman, pts, turn_no, wall, players, log, emit)

        d = ai(p, rng) if callable(ai) else choose_discard(p.hand, rng)
        p.hand.remove(d)
        p.sort()
        p.discards.append(d)
        emit("P%d 打 %s" % (p.seat, tile_name(d)))

        for k in range(1, 4):
            q = players[(turn + k) % 4]
            h = list(q.hand) + [d]
            if MJ.is_valid_hand(h):
                r = judge(h, q.seat)
                if r or not require_yaku:
                    yaku, han, yakuman, pts = r if r else (["无役(测试)"], 0, False, 0)
                    return _result("ron", q.seat, p.seat, h, d, yaku, han,
                                   yakuman, pts, turn_no, wall, players, log, emit)

        turn = (turn + 1) % 4

    emit("牌墙摸尽 → 流局")
    return _result("ryukyoku", None, None, None, None, [], 0, False, 0,
                   turn_no, wall, players, log, emit)


def _result(kind, winner, loser, hand, win_tile, yaku, han, yakuman, pts,
            turn_no, wall, players, log, emit):
    if kind == "tsumo":
        emit("★ P%d 自摸和牌 役:%s %s番 %d点" % (
            winner, "、".join(yaku), han, pts))
    elif kind == "ron":
        emit("★ P%d 荣和（放铳 P%d） 役:%s %s番 %d点" % (
            winner, loser, "、".join(yaku), han, pts))
    return {
        "type": kind,
        "winner": winner,
        "loser": loser,
        "hand": hand,
        "win_tile": win_tile,
        "yaku": yaku,
        "han": han,
        "yakuman": yakuman,
        "points": pts,
        "turns": turn_no,
        "wall_left": len(wall),
        "players": players,
        "log": log,
    }


def format_result(r):
    if r["type"] == "ryukyoku":
        return "流局（牌墙摸尽，共 %d 巡）" % r["turns"]
    who = "P%d" % r["winner"]
    how = "自摸" if r["type"] == "tsumo" else "荣和（P%d 放铳）" % r["loser"]
    yk = "、".join(r["yaku"])
    tag = "役满" if r["yakuman"] else "%d番" % r["han"]
    return "%s %s：%s ｜ %s ｜ %d点 ｜ %d巡" % (
        who, how, hand_text(r["hand"]), yk + " " + tag, r["points"], r["turns"])
