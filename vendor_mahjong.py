"""Mahjong hand generation and verification for Riichi Mahjong."""

import random
from itertools import combinations

# Tile encoding: (suit, number)
# suit: 'w'=万, 'p'=筒, 's'=索, 'z'=字
# number: 1-9 for wan/pin/sou, 1-7 for honors

HONOR_NAMES = {1: "東", 2: "南", 3: "西", 4: "北", 5: "白", 6: "発", 7: "中"}
HONOR_TO_NUM = {v: k for k, v in HONOR_NAMES.items()}

ALL_TILES = []
for s in "wps":
    for n in range(1, 10):
        ALL_TILES.append((s, n))
for n in range(1, 8):
    ALL_TILES.append(("z", n))


def tile_str(t):
    s, n = t
    if s == "z":
        return f"z{HONOR_NAMES.get(n, str(n))}"
    return f"{s}{n}"


def hand_str(tiles, separate_win=True):
    """Convert tile list to display string. Winning tile (last) separated if separate_win=True."""
    if len(tiles) != 14:
        suits = {"w": [], "p": [], "s": [], "z": []}
        for s, n in tiles:
            suits[s].append(n)
        parts = []
        for s in "wpsz":
            if suits[s]:
                nums = suits[s]
                nums.sort()
                if s == "z":
                    parts.append("z" + "".join(HONOR_NAMES.get(n, str(n)) for n in nums))
                else:
                    parts.append(s + "".join(str(n) for n in nums))
        return " ".join(parts)

    suit_order = {"w": 0, "p": 1, "s": 2, "z": 3}
    first13 = sorted(tiles[:13], key=lambda t: (suit_order.get(t[0], 99), t[1]))
    win_tile = tiles[13]

    suits = {"w": [], "p": [], "s": [], "z": []}
    for s, n in first13:
        suits[s].append(n)
    parts = []
    for s in "wpsz":
        if suits[s]:
            nums = suits[s]
            nums.sort()
            if s == "z":
                parts.append("z" + "".join(HONOR_NAMES.get(n, str(n)) for n in nums))
            else:
                parts.append(s + "".join(str(n) for n in nums))

    main = " ".join(parts)
    wt = describe_tile(win_tile) if separate_win else tile_str(win_tile)
    return f"{main} 河底[{wt}]" if separate_win else f"{main} {tile_str(win_tile)}"


def parse_hand(text):
    """Parse a guess string like 'w112233 s445566 p77' or 'z東東東 w123...' into tile list."""
    tiles = []
    text = text.strip().lower()
    parts = text.split()
    for part in parts:
        if not part:
            continue
        suit = part[0]
        rest = part[1:]
        if suit not in "wpsz":
            continue
        if suit == "z":
            for ch in rest:
                if ch.isdigit():
                    n = int(ch)
                else:
                    n = HONOR_TO_NUM.get(ch)
                    if n is None:
                        continue
                tiles.append((suit, n))
        else:
            for ch in rest:
                if ch.isdigit():
                    tiles.append((suit, int(ch)))
    return tiles


def has_yaku(tiles):
    """Check if the 14-tile hand has at least one valid yaku."""
    return len(find_yaku(tiles)) > 0


def find_yaku(tiles, seat_wind=1, round_wind=1):
    """Return list of yaku names present in the hand. Winds affect yakuhai detection."""
    yaku = []
    counts = _count_tiles(tiles)

    # 国士无双: 13 unique orphans + 1 duplicate
    orphans_required = set()
    for s in "wps":
        orphans_required.add((s, 1))
        orphans_required.add((s, 9))
    for n in range(1, 8):
        orphans_required.add(("z", n))
    actual = set(tiles)
    missing = orphans_required - actual
    if len(missing) <= 1 and len(tiles) == 14:
        if len(missing) == 0 and any(c == 2 for c in counts.values()):
            yaku.append("国士无双")
        elif len(missing) == 1:
            yaku.append("国士无双")

    # 字一色: all honors
    if all(s == "z" for s, n in tiles):
        yaku.append("字一色")

    # 小四喜: 3 or 4 wind triplets
    wind_triplets = sum(1 for n in range(1, 5) if counts.get(("z", n), 0) >= 3)
    wind_pairs = sum(1 for n in range(1, 5) if counts.get(("z", n), 0) == 2)
    if wind_triplets == 3 and wind_pairs == 1:
        yaku.append("小四喜")
    elif wind_triplets == 4:
        yaku.append("小四喜")

    # 役牌: triplet of seat wind, round wind, or dragon
    yakuhai_tiles = {("z", seat_wind), ("z", round_wind), ("z", 5), ("z", 6), ("z", 7)}
    for (s, n), c in counts.items():
        if (s, n) in yakuhai_tiles and c >= 3:
            yaku.append("役牌")

    # 平和: 4 sequences + non-yakuhai pair, ryanmen wait
    if _is_valid_standard(tiles):
        for t, c in counts.items():
            if c >= 2 and t not in yakuhai_tiles:
                c2 = {k: v for k, v in counts.items()}
                c2[t] -= 2
                if c2[t] == 0:
                    del c2[t]
                if _can_decompose_into_sequences(c2, 4):
                    yaku.append("平和")
                    break

    # 断幺九: all 2-8, no honors
    if all(s != "z" and 2 <= n <= 8 for s, n in tiles):
        yaku.append("断幺九")

    # 对对和: 4 triplets + 1 pair
    tri = sum(1 for c in counts.values() if c >= 3)
    pr = sum(1 for c in counts.values() if c == 2)
    if tri == 4 and pr == 1:
        yaku.append("对对和")

    # 七对子
    if len(counts) == 7 and all(c == 2 for c in counts.values()):
        yaku.append("七对子")

    # 混一色: one number suit + honors
    suits_present = set(s for s, n in tiles)
    number_suits = suits_present - {"z"}
    if len(number_suits) == 1 and "z" in suits_present and len(suits_present) == 2:
        yaku.append("混一色")

    # 一气通贯: 1-9 straight in one suit
    for s in "wps":
        if all(counts.get((s, n), 0) >= 1 for n in range(1, 10)):
            yaku.append("一气通贯")

    # 三色同刻: same-number triplet in all 3 suits
    for n in range(1, 10):
        if all(counts.get((s, n), 0) >= 3 for s in "wps"):
            yaku.append("三色同刻")

    # 三色同顺: same-number sequence in all 3 suits
    for start in range(1, 8):
        seq = [start, start+1, start+2]
        if all(all(counts.get((s, x), 0) >= 1 for x in seq) for s in "wps"):
            yaku.append("三色同顺")

    # 一盃口
    for s in "wps":
        suit_nums = sorted([n for ss, n in tiles if ss == s])
        for start in range(1, 8):
            seq = [start, start+1, start+2]
            remaining = list(suit_nums)
            ok = True
            for x in seq:
                if x in remaining:
                    remaining.remove(x)
                else:
                    ok = False
                    break
            if ok:
                remaining2 = list(remaining)
                for x in seq:
                    if x in remaining2:
                        remaining2.remove(x)
                    else:
                        ok = False
                        break
            if ok:
                yaku.append("一盃口")
                break

    return yaku


def _can_decompose_into_sequences(counts, remaining_seqs):
    """Check if remaining tiles can form `remaining_seqs` sequences (NO triplets)."""
    if remaining_seqs == 0:
        return len(counts) == 0
    if not counts:
        return False
    for s in "wps":
        for n in range(1, 8):
            seq = [(s, n), (s, n+1), (s, n+2)]
            if all(counts.get(t, 0) >= 1 for t in seq):
                updated = dict(counts)
                for t in seq:
                    updated[t] -= 1
                    if updated[t] == 0:
                        del updated[t]
                if _can_decompose_into_sequences(updated, remaining_seqs - 1):
                    return True
    return False


def generate_valid_hand(seat_wind=1, round_wind=1):
    """Generate a valid 14-tile Riichi mahjong hand with at least one yaku."""
    builders = [
        _build_tanyao, _build_chiitoitsu, _build_toitoi,
        _build_iipeikou, _build_pinfu, _build_kokushi,
        _build_sanshoku_doukou, _build_ikkitsuukan,
        _build_sanshoku_doujun, _build_honitsu, _build_tsuuiisou,
        _build_shousuushi,
    ]
    WEIGHTS = [50, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5]
    for _ in range(500):
        builder = random.choices(builders, weights=WEIGHTS, k=1)[0]
        if builder in (_build_yakuhai,):
            # yakuhai needs the specific honor that matches winds
            honor = random.choice([seat_wind, round_wind, 5, 6, 7])
            tiles = _build_yakuhai_with_honor(honor)
        else:
            tiles = builder()
        if tiles and len(tiles) == 14:
            hand = list(tiles)
            random.shuffle(hand)
            win_tile = hand[-1]
            first13 = sorted(hand[:13], key=lambda t: ({"w":0,"p":1,"s":2,"z":3}[t[0]], t[1]))
            return first13 + [win_tile]
    return _build_chiitoitsu() or _build_yakuhai_with_honor(5)


def _build_yakuhai_with_honor(honor_num):
    """Hand with specific honor triplet (for yakuhai with seat/round wind)."""
    tiles = [("z", honor_num)] * 3
    for _ in range(3):
        if random.random() < 0.5:
            new = _random_sequence(existing=tiles)
        else:
            new = _random_triplet(existing=tiles)
        if not new:
            return None
        tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _check_tile_limit(new_tiles, existing):
    """Check if adding new_tiles to existing keeps each tile count <= 4."""
    if not existing:
        return True
    all_counts = {}
    for t in existing:
        all_counts[t] = all_counts.get(t, 0) + 1
    for t in new_tiles:
        all_counts[t] = all_counts.get(t, 0) + 1
        if all_counts[t] > 4:
            return False
    return True


def _random_sequence(suit=None, existing=None, start_range=None):
    """Return a random sequence of 3 tiles, ensuring no tile exceeds 4 copies."""
    for _ in range(100):
        s = suit if suit else random.choice("wps")
        lo, hi = start_range if start_range else (1, 7)
        start = random.randint(lo, hi)
        seq = [(s, start), (s, start+1), (s, start+2)]
        if _check_tile_limit(seq, existing):
            return seq
    suits = [suit] if suit else list("wps")
    lo, hi = start_range if start_range else (1, 7)
    for s in suits:
        for start in range(lo, hi + 1):
            seq = [(s, start), (s, start+1), (s, start+2)]
            if _check_tile_limit(seq, existing):
                return seq
    return []


def _random_triplet(suit=None, num=None, existing=None, num_range=None):
    """Return 3 copies of a tile, ensuring no tile exceeds 4 copies."""
    for _ in range(100):
        s = suit if suit else random.choice("wpsz")
        lo, hi = num_range if num_range else (2, 8) if s != "z" else (1, 7)
        lo, hi = max(lo, 1), min(hi, 7 if s == "z" else 9)
        n = num if num is not None else random.randint(lo, hi)
        trip = [(s, n)] * 3
        if _check_tile_limit(trip, existing):
            return trip
    suits = [suit] if suit else list("wpsz")
    for s in suits:
        lo, hi = num_range if num_range else (1, 9) if s != "z" else (1, 7)
        lo, hi = max(lo, 1), min(hi, 7 if s == "z" else 9)
        for n in range(lo, hi + 1):
            if num is not None and n != num:
                continue
            trip = [(s, n)] * 3
            if _check_tile_limit(trip, existing):
                return trip
    return []


def _random_pair(suit=None, num=None, existing=None, num_range=None):
    """Return 2 copies of a tile, ensuring no tile exceeds 4 copies."""
    for _ in range(100):
        s = suit if suit else random.choice("wpsz")
        lo, hi = num_range if num_range else (2, 8) if s != "z" else (1, 7)
        lo, hi = max(lo, 1), min(hi, 7 if s == "z" else 9)
        n = num if num is not None else random.randint(lo, hi)
        pair = [(s, n)] * 2
        if _check_tile_limit(pair, existing):
            return pair
    suits = [suit] if suit else list("wpsz")
    for s in suits:
        lo, hi = num_range if num_range else (1, 9) if s != "z" else (1, 7)
        lo, hi = max(lo, 1), min(hi, 7 if s == "z" else 9)
        for n in range(lo, hi + 1):
            if num is not None and n != num:
                continue
            pair = [(s, n)] * 2
            if _check_tile_limit(pair, existing):
                return pair
    return []


def _any_sequence(suit, existing=None):
    """Return a random sequence from the given suit, respecting tile limits."""
    return _random_sequence(suit=suit, existing=existing)


def _any_triplet(suit, existing=None):
    num = random.randint(1, 9) if suit != "z" else random.randint(1, 7)
    return _random_triplet(suit=suit, num=num, existing=existing)


def _build_tanyao():
    """4 melds + 1 pair, all tiles 2-8, no honors."""
    tiles = []
    for _ in range(4):
        if random.random() < 0.6:
            new = _random_sequence(existing=tiles, start_range=(2, 6))
        else:
            new = _random_triplet(existing=tiles, num_range=(2, 8))
        if not new:
            return None
        tiles += new
    new = _random_pair(existing=tiles, num_range=(2, 8))
    if not new:
        return None
    tiles += new
    return tiles


def _build_yakuhai():
    """Hand with at least one honor triplet (yakuhai)."""
    tiles = []
    honor_num = random.randint(1, 7)
    tiles += [("z", honor_num)] * 3
    for _ in range(3):
        if random.random() < 0.5:
            new = _random_sequence(existing=tiles)
        else:
            new = _random_triplet(existing=tiles)
        if not new:
            return None
        tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_toitoi():
    """4 triplets + 1 pair (all triplets)."""
    tiles = []
    for _ in range(4):
        new = _random_triplet(existing=tiles)
        if not new:
            return None
        tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_chiitoitsu():
    """7 distinct pairs."""
    tiles = []
    used = set()
    for _ in range(7):
        while True:
            s = random.choice("wpsz")
            n = random.randint(1, 9) if s != "z" else random.randint(1, 7)
            if (s, n) not in used:
                used.add((s, n))
                break
        tiles += [(s, n), (s, n)]
    return tiles


def _build_iipeikou():
    """Two identical sequences + other melds."""
    suit = random.choice("wps")
    start = random.randint(1, 7)
    seq = [(suit, start), (suit, start+1), (suit, start+2)]
    tiles = seq + seq
    for _ in range(2):
        if random.random() < 0.5:
            new = _random_sequence(existing=tiles)
        else:
            new = _random_triplet(existing=tiles)
        if not new:
            return None
        tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_pinfu():
    """All sequences, non-yakuhai pair, two-sided wait."""
    tiles = []
    for _ in range(4):
        new = _random_sequence(existing=tiles)
        if not new:
            return None
        tiles += new
    ps = random.choice("wps")
    pn = random.randint(2, 8)
    pair = [(ps, pn), (ps, pn)]
    if not _check_tile_limit(pair, tiles):
        new = _random_pair(existing=tiles, num_range=(2, 8))
        if not new:
            return None
        tiles += new
    else:
        tiles += pair
    return tiles


# --- 分片提交说明 -----------------------------------------------------------
# 本文件是第三方牌理库（来源见 README）。
# 正文后半保存在同目录 vendor_mahjong_rest.py，此处按序合入本模块命名空间，
# 对外等价于单一模块（`import vendor_mahjong as MJ`）。
import os as _os

_rest_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                           "vendor_mahjong_rest.py")
with open(_rest_path, encoding="utf-8") as _f:
    exec(compile(_f.read(), "vendor_mahjong_rest.py", "exec"), globals())
del _os, _rest_path, _f
