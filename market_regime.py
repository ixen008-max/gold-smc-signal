import pandas as pd
import numpy as np

def detect_market_regime(df, adx_period=14):
    if len(df) < adx_period:
        return "INSUFFICIENT_DATA"
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(adx_period).mean()
    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = 100 * (pd.Series(plus_dm).rolling(adx_period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(adx_period).mean() / atr)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10))
    adx = dx.rolling(adx_period).mean()
    current_adx = adx.iloc[-1]
    current_plus = plus_di.iloc[-1]
    current_minus = minus_di.iloc[-1]

    swings_high, swings_low = [], []
    window = 5
    for i in range(window, len(df)-window):
        if high.iloc[i] == max(high.iloc[i-window:i+window+1]):
            swings_high.append((i, high.iloc[i]))
        if low.iloc[i] == min(low.iloc[i-window:i+window+1]):
            swings_low.append((i, low.iloc[i]))

    if len(swings_high) < 2 or len(swings_low) < 2:
        return "UNCLEAR"

    last_highs = swings_high[-2:]
    last_lows = swings_low[-2:]
    trend_up = (last_highs[-1][1] > last_highs[-2][1]) and (last_lows[-1][1] > last_lows[-2][1])
    trend_down = (last_lows[-1][1] < last_lows[-2][1]) and (last_highs[-1][1] < last_highs[-2][1])

    if current_adx > 25 and current_plus > current_minus and trend_up:
        return "TREND_UP"
    elif current_adx > 25 and current_minus > current_plus and trend_down:
        return "TREND_DOWN"
    elif current_adx < 20:
        return "RANGE"
    else:
        return "HIGH_VOL"