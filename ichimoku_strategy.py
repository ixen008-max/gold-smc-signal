import pandas as pd

def calculate_ichimoku(df, tenkan_period=9, kijun_period=26, senkou_b_period=52):
    high = df['High']
    low = df['Low']
    close = df['Close']

    tenkan = (high.rolling(tenkan_period).max() + low.rolling(tenkan_period).min()) / 2
    kijun = (high.rolling(kijun_period).max() + low.rolling(kijun_period).min()) / 2

    senkou_a = ((tenkan + kijun) / 2).shift(kijun_period)
    senkou_b = ((high.rolling(senkou_b_period).max() + low.rolling(senkou_b_period).min()) / 2).shift(kijun_period)

    chikou = close.shift(-kijun_period)

    return tenkan, kijun, senkou_a, senkou_b, chikou

def get_ichimoku_signal(df):
    if len(df) < 52:
        return None

    tenkan, kijun, senkou_a, senkou_b, chikou = calculate_ichimoku(df)
    current_price = df['Close'].iloc[-1]

    latest_tenkan = tenkan.iloc[-1]
    latest_kijun = kijun.iloc[-1]
    latest_senkou_a = senkou_a.iloc[-1]
    latest_senkou_b = senkou_b.iloc[-1]

    # ตรวจสอบ Chikou Span (จำเป็นต้องมีข้อมูลย้อนหลัง 27 แท่ง)
    if len(df) > 27:
        chikou_above = chikou.iloc[-1] > df['Close'].iloc[-27]
        chikou_below = chikou.iloc[-1] < df['Close'].iloc[-27]
    else:
        chikou_above = False
        chikou_below = False

    # สัญญาณ BUY
    if (current_price > max(latest_senkou_a, latest_senkou_b) and
        latest_tenkan > latest_kijun and
        chikou_above):
        return "BUY"

    # สัญญาณ SELL
    if (current_price < min(latest_senkou_a, latest_senkou_b) and
        latest_tenkan < latest_kijun and
        chikou_below):
        return "SELL"

    return None