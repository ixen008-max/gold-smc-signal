def find_swings(df, window=5):
    highs, lows = [], []
    for i in range(window, len(df)-window):
        if df['High'].iloc[i] == df['High'].iloc[i-window:i+window+1].max():
            highs.append((i, df['High'].iloc[i]))
        if df['Low'].iloc[i] == df['Low'].iloc[i-window:i+window+1].min():
            lows.append((i, df['Low'].iloc[i]))
    return highs, lows

def find_order_blocks(df, swings, mode='Bullish'):
    obs = []
    for idx, price in swings:
        if idx < 1 or idx > len(df)-2:
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

def find_fvg(df, direction='bullish'):
    """หาจุด FVG (Fair Value Gap)"""
    fvg_list = []
    for i in range(1, len(df)-1):
        if direction == 'bullish':
            # Bullish FVG: Low ของแท่งที่ i+1 > High ของแท่งที่ i-1
            if df['Low'].iloc[i+1] > df['High'].iloc[i-1]:
                top = df['Low'].iloc[i+1]
                bottom = df['High'].iloc[i-1]
                fvg_list.append((top, bottom, i))
        elif direction == 'bearish':
            # Bearish FVG: High ของแท่งที่ i+1 < Low ของแท่งที่ i-1
            if df['High'].iloc[i+1] < df['Low'].iloc[i-1]:
                top = df['Low'].iloc[i-1]
                bottom = df['High'].iloc[i+1]
                fvg_list.append((top, bottom, i))
    return fvg_list