import pandas as pd

def find_swings(df, window=5):
    """หา Swing Highs และ Swing Lows"""
    highs, lows = [], []
    for i in range(window, len(df) - window):
        if df['High'].iloc[i] == df['High'].iloc[i-window:i+window+1].max():
            highs.append((i, df['High'].iloc[i]))
        if df['Low'].iloc[i] == df['Low'].iloc[i-window:i+window+1].min():
            lows.append((i, df['Low'].iloc[i]))
    return highs, lows

def detect_bos(df, swings_high, swings_low, lookback=5):
    if len(swings_high) < 2 or len(swings_low) < 2:
        return None

    last_high_idx, last_high_price = swings_high[-1]
    prev_high_idx, prev_high_price = swings_high[-2]
    last_low_idx, last_low_price = swings_low[-1]
    prev_low_idx, prev_low_price = swings_low[-2]

    current_idx = len(df) - 1
    current_high = df['High'].iloc[current_idx]
    current_low = df['Low'].iloc[current_idx]

    if current_high > prev_high_price and current_low > prev_low_price:
        return 'bullish'
    elif current_low < prev_low_price and current_high < prev_high_price:
        return 'bearish'
    return None

def detect_liquidity_sweep(df, swings_high, swings_low, lookback=5):
    if not swings_high or not swings_low:
        return None

    last_low_idx, last_low_price = swings_low[-1]
    last_high_idx, last_high_price = swings_high[-1]

    current_idx = len(df) - 1
    current_low = df['Low'].iloc[current_idx]
    current_high = df['High'].iloc[current_idx]
    current_close = df['Close'].iloc[current_idx]

    if current_low < last_low_price and current_close > last_low_price:
        return 'bullish'
    if current_high > last_high_price and current_close < last_high_price:
        return 'bearish'
    return None

def find_fvg(df, direction='bullish'):
    fvg_list = []
    for i in range(1, len(df) - 1):
        if direction == 'bullish':
            if df['Low'].iloc[i+1] > df['High'].iloc[i-1]:
                top = df['Low'].iloc[i+1]
                bottom = df['High'].iloc[i-1]
                fvg_list.append((top, bottom, i))
        elif direction == 'bearish':
            if df['High'].iloc[i+1] < df['Low'].iloc[i-1]:
                top = df['Low'].iloc[i-1]
                bottom = df['High'].iloc[i+1]
                fvg_list.append((top, bottom, i))
    return fvg_list

def find_order_blocks(df, swings, mode='Bullish'):
    obs = []
    for idx, price in swings:
        if idx < 1 or idx > len(df) - 2:
            continue
        candle = df.iloc[idx-1]
        if mode == 'Bullish':
            if candle['Close'] < candle['Open']:
                top = candle['Open']
                bottom = candle['Close']
                obs.append((top, bottom, idx-1))
        elif mode == 'Bearish':
            if candle['Close'] > candle['Open']:
                top = candle['Close']
                bottom = candle['Open']
                obs.append((top, bottom, idx-1))
    return obs

def calculate_fibonacci(swing_high, swing_low):
    diff = swing_high - swing_low
    levels = {
        '0.0': swing_high,
        '0.236': swing_high - 0.236 * diff,
        '0.382': swing_high - 0.382 * diff,
        '0.5': swing_high - 0.5 * diff,
        '0.618': swing_high - 0.618 * diff,
        '0.786': swing_high - 0.786 * diff,
        '1.0': swing_low,
        '-0.272': swing_high + 0.272 * diff,
        '-0.618': swing_high + 0.618 * diff,
    }
    return levels

def find_sr_levels(df, lookback=3):
    swings_high, swings_low = find_swings(df)
    sr_levels = []
    for idx, price in swings_high[-lookback:]:
        sr_levels.append({'price': price, 'type': 'resistance', 'idx': idx})
    for idx, price in swings_low[-lookback:]:
        sr_levels.append({'price': price, 'type': 'support', 'idx': idx})
    return sr_levels

def calculate_atr(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return atr if not pd.isna(atr) else 1.0

def _volume_ok(df, idx, factor=1.5):
    """ตรวจ Volume ของแท่งที่ idx ว่าสูงกว่าค่าเฉลี่ย"""
    vol = df['Volume']
    if idx < 20:
        return False
    avg_vol = vol.iloc[max(0, idx-20):idx].mean()
    return vol.iloc[idx] > avg_vol * factor

def find_smc_setup(df, regime='TREND_UP'):
    """
    รวมลำดับ SMC: Liquidity Sweep + BOS + FVG/OB + OTE Zone + Volume
    คืนค่า dict setup หรือ None
    """
    swings_high, swings_low = find_swings(df)
    if not swings_high or not swings_low:
        return None

    sweep = detect_liquidity_sweep(df, swings_high, swings_low)
    if sweep is None:
        return None

    bos = detect_bos(df, swings_high, swings_low)
    if bos is None:
        return None

    if sweep == 'bullish' and bos == 'bullish':
        direction = 'BUY'
        invalidation = swings_low[-1][1]
        swing_high = swings_high[-1][1]
        swing_low = invalidation
        fib_levels = calculate_fibonacci(swing_high, swing_low)
        sr_levels = find_sr_levels(df)

        # OTE Zone = 0.618 - 0.79 of range (from high to low)
        ote_top = fib_levels['0.618']
        ote_bottom = fib_levels['0.786']

        fvgs = find_fvg(df, direction='bullish')
        ob_candidates = find_order_blocks(df, swings_low, mode='Bullish')
        entry_zones = []

        for top, bottom, idx in fvgs:
            entry_zones.append({'entry': bottom, 'top': top, 'bottom': bottom, 'source': 'FVG', 'candle_idx': idx})
        for top, bottom, idx in ob_candidates:
            entry_zones.append({'entry': bottom, 'top': top, 'bottom': bottom, 'source': 'OB', 'candle_idx': idx})

        current_price = df['Close'].iloc[-1]
        best_entry = None
        best_score = float('inf')
        for zone in entry_zones:
            entry = zone['entry']
            if entry >= current_price:
                continue
            dist = current_price - entry
            score = dist

            # OTE Zone bonus
            if ote_bottom <= entry <= ote_top:
                score -= 20

            # Fib level proximity
            for level, price in fib_levels.items():
                if abs(entry - price) < 3.0:
                    score -= 5

            # SR proximity
            for sr in sr_levels:
                if abs(entry - sr['price']) < 3.0:
                    score -= 3

            # Volume confirmation
            if not _volume_ok(df, zone['candle_idx']):
                score += 5

            if score < best_score:
                best_score = score
                best_entry = zone

        if best_entry:
            entry = best_entry['entry']
            atr = calculate_atr(df)
            sl = invalidation - 0.5 * atr
            tp = entry + (swing_high - swing_low) * 1.272
            return {
                'type': 'BUY_LIMIT',
                'entry': entry,
                'sl': sl,
                'tp': tp,
                'invalidation': invalidation,
                'strategy': f'SMC + OTE + Vol ({best_entry["source"]})',
                'fib_level': _get_nearest_fib(entry, fib_levels),
                'sr_level': _get_nearest_sr(entry, sr_levels),
                'ote_zone': f"{ote_bottom:.2f} - {ote_top:.2f}"
            }

    elif sweep == 'bearish' and bos == 'bearish':
        direction = 'SELL'
        invalidation = swings_high[-1][1]
        swing_high = invalidation
        swing_low = swings_low[-1][1]
        fib_levels = calculate_fibonacci(swing_high, swing_low)
        sr_levels = find_sr_levels(df)

        ote_top = fib_levels['0.618']
        ote_bottom = fib_levels['0.786']

        fvgs = find_fvg(df, direction='bearish')
        ob_candidates = find_order_blocks(df, swings_high, mode='Bearish')
        entry_zones = []

        for top, bottom, idx in fvgs:
            entry_zones.append({'entry': top, 'top': top, 'bottom': bottom, 'source': 'FVG', 'candle_idx': idx})
        for top, bottom, idx in ob_candidates:
            entry_zones.append({'entry': top, 'top': top, 'bottom': bottom, 'source': 'OB', 'candle_idx': idx})

        current_price = df['Close'].iloc[-1]
        best_entry = None
        best_score = float('inf')
        for zone in entry_zones:
            entry = zone['entry']
            if entry <= current_price:
                continue
            dist = entry - current_price
            score = dist

            if ote_bottom <= entry <= ote_top:
                score -= 20

            for level, price in fib_levels.items():
                if abs(entry - price) < 3.0:
                    score -= 5

            for sr in sr_levels:
                if abs(entry - sr['price']) < 3.0:
                    score -= 3

            if not _volume_ok(df, zone['candle_idx']):
                score += 5

            if score < best_score:
                best_score = score
                best_entry = zone

        if best_entry:
            entry = best_entry['entry']
            atr = calculate_atr(df)
            sl = invalidation + 0.5 * atr
            tp = entry - (swing_high - swing_low) * 1.272
            return {
                'type': 'SELL_LIMIT',
                'entry': entry,
                'sl': sl,
                'tp': tp,
                'invalidation': invalidation,
                'strategy': f'SMC + OTE + Vol ({best_entry["source"]})',
                'fib_level': _get_nearest_fib(entry, fib_levels),
                'sr_level': _get_nearest_sr(entry, sr_levels),
                'ote_zone': f"{ote_bottom:.2f} - {ote_top:.2f}"
            }

    return None

def _get_nearest_fib(price, fib_levels):
    nearest = None
    min_dist = float('inf')
    for level, val in fib_levels.items():
        dist = abs(price - val)
        if dist < min_dist:
            min_dist = dist
            nearest = level
    return nearest

def _get_nearest_sr(price, sr_levels):
    nearest = None
    min_dist = float('inf')
    for sr in sr_levels:
        dist = abs(price - sr['price'])
        if dist < min_dist:
            min_dist = dist
            nearest = f"{sr['type']} @ {sr['price']:.2f}"
    return nearest