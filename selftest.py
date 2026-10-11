# -*- coding: utf-8 -*-
"""M0 自测：① 向听数交叉验证 ② 批量跑局统计 ③ 打一局带过程回放。"""

import random
import sys

import engine as E
import vendor_mahjong as MJ

SUIT_LIB = {"w": "m", "p": "p", "s": "s", "z": "z"}
HONOR_LIB = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7"}


def to_lib_str(tiles):
    parts = []
    for suit in "wpsz":
        nums = sorted(n for s, n in tiles if s == suit)
        if nums:
            if suit == "z":
                parts.append("".join(HONOR_LIB[n] for n in nums) + "z")
            else:
                parts.append("".join(str(n) for n in nums) + SUIT_LIB[suit])
    return "".join(parts)


def check_shanten(n=300):
    print("=" * 60)
    print("① 向听数交叉验证（自研实现 vs mahjong 库）")
    try:
        from mahjong.shanten import Shanten
    except Exception as ex:                                    # noqa: BLE001
        print("  跳过：mahjong 库不可用（%s）" % ex)
        return
    rng = random.Random(20261011)
    wall = E.build_wall()
    bad = []
    for _ in range(n):
        rng.shuffle(wall)
        hand = wall[:13]
        mine = E._shanten_fallback(hand)
        lib = Shanten.calculate_shanten(E._counts34(hand))
        if mine != lib:
            bad.append((to_lib_str(hand), mine, lib))
    print("  样本 %d 手，不一致 %d 手" % (n, len(bad)))
    for s, a, b in bad[:5]:
        print("    %s  自研=%s 库=%s" % (s, a, b))
    # 固定用例
    for s in ["w1112345678999", "w123456789 p123 z11", "w19 p19 s19 z1234567"]:
        h = E.MJ.parse_hand(s)
        print("    用例 %-24s 自研=%s 库=%s" % (
            s, E._shanten_fallback(h), Shanten.calculate_shanten(E._counts34(h))))
    print("  引擎当前使用：%s" % E.ENGINE_SOURCE)


def batch(n=500, seed=7):
    print("=" * 60)
    print("② 批量跑局（%d 局，随机 AI）" % n)
    rng = random.Random(seed)
    stat = {"tsumo": 0, "ron": 0, "ryukyoku": 0}
    turns = 0
    yaku_cnt = {}
    for i in range(n):
        r = E.play_hand(seed=rng.randrange(1 << 30))
        stat[r["type"]] += 1
        turns += r["turns"]
        for y in r["yaku"]:
            yaku_cnt[y] = yaku_cnt.get(y, 0) + 1
    print("  自摸 %d ｜ 荣和 %d ｜ 流局 %d ｜ 平均 %.1f 巡"
          % (stat["tsumo"], stat["ron"], stat["ryukyoku"], turns / n))
    top = sorted(yaku_cnt.items(), key=lambda kv: -kv[1])[:8]
    print("  出现役种 Top: %s" % (top if top else "（无和牌）"))


def show_one(seed=0, limit=4000):
    print("=" * 60)
    print("③ 过程回放：找一局分出胜负的对局")
    for s in range(limit):
        r = E.play_hand(seed=s, verbose=True)
        if r["type"] != "ryukyoku":
            print("  seed=%d" % s)
            for line in r["log"]:
                print("   ", line)
            print("  结算 → %s" % E.format_result(r))
            return r
    print("  未在 %d 个种子里找到和牌局" % limit)
    return None


if __name__ == "__main__":
    check_shanten()
    batch(int(sys.argv[1]) if len(sys.argv) > 1 else 500)
    show_one()
