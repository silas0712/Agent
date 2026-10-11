def _build_kokushi():
    """国士无双: 13 unique orphans (1,9 + 7 honors) + 1 duplicate."""
    orphans = []
    for s in "wps":
        orphans.append((s, 1))
        orphans.append((s, 9))
    for n in range(1, 8):
        orphans.append(("z", n))
    dup = random.choice(orphans)
    return orphans + [dup]


def _build_honchantaiyao():
    """混全带幺九: every meld contains at least one terminal (1,9) or honor."""
    tiles = []
    for _ in range(4):
        kind = random.random()
        if kind < 0.3:
            t = random.choice("wps")
            new_tiles = [(t, random.choice([1, 2, 3, 7, 8, 9])), (t, random.choice([1, 2, 3])), (t, random.choice([7, 8, 9]))]
            if not _check_tile_limit(new_tiles, tiles):
                continue
            tiles += new_tiles
        elif kind < 0.6:
            su = random.choice("wps")
            start = random.choice([1, 7])
            new_tiles = [(su, start), (su, start+1), (su, start+2)]
            if not _check_tile_limit(new_tiles, tiles):
                continue
            tiles += new_tiles
        else:
            new = _random_triplet(suit="z", existing=tiles)
            if not new:
                continue
            tiles += new
    if len(tiles) < 12:
        return None
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_sanshoku_doukou():
    """三色同刻: same-number triplet in w, p, s."""
    num = random.randint(1, 9)
    tiles = []
    for s in "wps":
        tiles += [(s, num)] * 3
    new = _random_triplet(existing=tiles)
    if not new:
        return None
    tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_ikkitsuukan():
    """一气通贯: 1-9 straight in one suit."""
    suit = random.choice("wps")
    tiles = [(suit, 1), (suit, 2), (suit, 3), (suit, 4), (suit, 5), (suit, 6), (suit, 7), (suit, 8), (suit, 9)]
    new = _random_triplet(existing=tiles)
    if not new:
        return None
    tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_sanshoku_doujun():
    """三色同顺: same sequence across w/p/s."""
    start = random.randint(1, 7)
    tiles = []
    for s in "wps":
        tiles += [(s, start), (s, start+1), (s, start+2)]
    new = _random_triplet(existing=tiles)
    if not new:
        return None
    tiles += new
    new = _random_pair(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_honitsu():
    """混一色: one suit + honors only."""
    suit = random.choice("wps")
    tiles = []
    hon = random.choice([0, 1, 2, 3])
    for _ in range(4 - hon):
        if random.random() < 0.5:
            new = _random_sequence(suit=suit, existing=tiles)
        else:
            new = _random_triplet(suit=suit, existing=tiles)
        if not new:
            return None
        tiles += new
    for _ in range(hon):
        new = _random_triplet(suit="z", existing=tiles)
        if not new:
            return None
        tiles += new
    s = random.choice([suit, "z"])
    new = _random_pair(suit=s, existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_tsuuiisou():
    """字一色: all honor tiles, no tile exceeds 4 copies."""
    tiles = []
    for _ in range(4):
        new = _random_triplet(suit="z", existing=tiles)
        if not new:
            return None
        tiles += new
    new = _random_pair(suit="z", existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


def _build_shousuushi():
    """小四喜: 3 wind triplets + 1 wind pair."""
    winds = [("z", 1), ("z", 2), ("z", 3), ("z", 4)]
    random.shuffle(winds)
    pair_wind = winds[3]
    tiles = []
    for i in range(3):
        tiles += winds[i:i+1] * 3
    tiles += [pair_wind, pair_wind]
    new = _random_triplet(existing=tiles)
    if not new:
        return None
    tiles += new
    return tiles


YAKU_BUILDERS = {
    "断幺九": _build_tanyao,
    "役牌": lambda: _build_yakuhai_with_honor(random.choice([1,2,3,4,5,6,7])),
    "对对和": _build_toitoi,
    "七对子": _build_chiitoitsu,
    "一盃口": _build_iipeikou,
    "平和": _build_pinfu,
    "国士无双": _build_kokushi,
    "三色同刻": _build_sanshoku_doukou,
    "一气通贯": _build_ikkitsuukan,
    "三色同顺": _build_sanshoku_doujun,
    "混一色": _build_honitsu,
    "字一色": _build_tsuuiisou,
    "小四喜": _build_shousuushi,
}


def generate_hand_with_yaku(yaku_name, seat_wind=1, round_wind=1):
    """Generate a hand with a specific yaku. Returns None if not found."""
    if not yaku_name:
        return generate_valid_hand(seat_wind, round_wind)

    builder = YAKU_BUILDERS.get(yaku_name)
    if builder is None:
        for name, b in YAKU_BUILDERS.items():
            if yaku_name in name or name in yaku_name:
                builder = b
                break
        if builder is None:
            return None

    for _ in range(200):
        tiles = builder()
        if tiles and len(tiles) == 14 and yaku_name in find_yaku(tiles, seat_wind, round_wind):
            hand = list(tiles)
            random.shuffle(hand)
            win_tile = hand[-1]
            first13 = sorted(hand[:13], key=lambda t: ({"w":0,"p":1,"s":2,"z":3}[t[0]], t[1]))
            return first13 + [win_tile]
    return None


def validate_hand(tiles):
    """Validate parsed hand. Returns (True, "") or (False, error_message)."""
    if len(tiles) != 14:
        return False, f"手牌必须为 14 张，当前 {len(tiles)} 张。"
    for s, n in tiles:
        if s == "z" and n not in range(1, 8):
            return False, f"无效字牌: z{n}"
        elif s in "wps" and n not in range(1, 10):
            return False, f"无效数牌: {s}{n}"
    return True, ""


def is_valid_hand(tiles):
    """Check if 14 tiles form a valid Riichi winning hand."""
    if len(tiles) != 14:
        return False
    return _is_valid_standard(tiles) or _is_chiitoitsu(tiles) or _is_kokushi(tiles)


def _is_kokushi(tiles):
    """国士无双: 13 unique orphans + 1 duplicate."""
    orphans_needed = set()
    for s in "wps":
        orphans_needed.add((s, 1))
        orphans_needed.add((s, 9))
    for n in range(1, 8):
        orphans_needed.add(("z", n))
    actual = set(tiles)
    missing = orphans_needed - actual
    return len(missing) <= 1 and len(tiles) == 14


def _count_tiles(tiles):
    counts = {}
    for t in tiles:
        counts[t] = counts.get(t, 0) + 1
    return counts


def _is_chiitoitsu(tiles):
    """7 distinct pairs."""
    counts = _count_tiles(tiles)
    return len(counts) == 7 and all(c == 2 for c in counts.values())


def _is_valid_standard(tiles):
    """Check 4 melds + 1 pair by backtracking."""
    counts = _count_tiles(tiles)
    total = sum(counts.values())
    if total != 14:
        return False

    # Try each possible pair
    pairs = []
    for t, c in counts.items():
        if c >= 2:
            pairs.append(t)

    for pair in pairs:
        c = dict(counts)
        c[pair] -= 2
        if c[pair] == 0:
            del c[pair]
        if _can_form_melds(c, 4):
            return True
    return False


def _can_form_melds(counts, remaining_melds):
    """Recursively check if remaining tiles can form `remaining_melds` melds."""
    if remaining_melds == 0:
        return len(counts) == 0

    if not counts:
        return False

    # Try triplet
    for (s, n), c in list(counts.items()):
        if c >= 3:
            updated = dict(counts)
            updated[(s, n)] -= 3
            if updated[(s, n)] == 0:
                del updated[(s, n)]
            if _can_form_melds(updated, remaining_melds - 1):
                return True

    # Try sequence (only for w/p/s suits)
    for s in "wps":
        for n in range(1, 8):
            seq = [(s, n), (s, n+1), (s, n+2)]
            if all(counts.get(t, 0) >= 1 for t in seq):
                updated = dict(counts)
                for t in seq:
                    updated[t] -= 1
                    if updated[t] == 0:
                        del updated[t]
                if _can_form_melds(updated, remaining_melds - 1):
                    return True

    return False


def compare_guess(guess_tiles, target_tiles):
    """Position-based comparison. Target is sorted (first 13 + winning tile at pos 13).
    Guess is compared as-is (user's input order).
    - Green: correct tile at correct position
    - Yellow: tile exists in target but at wrong position
    - Gray: tile not in target
    """
    result = []
    original_counts = {}
    remaining_counts = {}
    for t in target_tiles:
        original_counts[t] = original_counts.get(t, 0) + 1
        remaining_counts[t] = remaining_counts.get(t, 0) + 1

    used = {}
    for i, g in enumerate(guess_tiles):
        t = target_tiles[i] if i < len(target_tiles) else None
        if t is not None and g == t and used.get(g, 0) < original_counts.get(g, 0):
            result.append((g, "correct"))
            used[g] = used.get(g, 0) + 1
            remaining_counts[g] -= 1
        else:
            result.append((g, None))

    for i, (g, status) in enumerate(result):
        if status is None:
            if remaining_counts.get(g, 0) > 0:
                result[i] = (g, "present")
                remaining_counts[g] -= 1
            else:
                result[i] = (g, "absent")

    return result


def describe_tile(tile):
    """Convert a tile tuple to Chinese name."""
    s, n = tile
    if s == "z":
        names = {1: "東", 2: "南", 3: "西", 4: "北", 5: "白", 6: "発", 7: "中"}
        return names.get(n, str(n))
    suits = {"w": "万", "p": "筒", "s": "索"}
    chinese_nums = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    return f"{chinese_nums.get(n, str(n))}{suits.get(s, s)}"


def describe_result(comp_result):
    """Convert comparison result to a human-readable Chinese string."""
    status_names = {"correct": "绿色", "present": "黄色", "absent": "灰色"}
    parts = []
    for tile, status in comp_result:
        name = describe_tile(tile)
        parts.append(f"{name}({status_names[status]})")
    return " ".join(parts)
