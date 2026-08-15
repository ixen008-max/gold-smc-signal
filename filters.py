import pandas as pd
from market_regime import detect_market_regime
from datetime import datetime, timezone, timedelta

def is_valid_ob(df, ob_candle_idx):
    candle = df.iloc[ob_candle_idx]
    body = abs(candle['Open'] - candle['Close'])
    candle_range = candle['High'] - candle['Low']
    if candle_range == 0:
        return False
    if body < 0.5 * candle_range:
        return False
    return True

def volume_confirmation(df, ob_idx, factor=1.5):
    vol = df['Volume']
    if vol.iloc[ob_idx] > vol.iloc[max(0, ob_idx-20):ob_idx].mean() * factor:
        return True
    return False

def is_choppy(df, period=14, threshold=0.003):
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    price = close.iloc[-1]
    return (atr / price) < threshold

def is_near_news():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    # บล็อกเฉพาะ NFP (ศุกร์แรกของเดือน 12:30-14:00 UTC)
    if now.weekday() == 4 and 12 <= now.hour <= 14:
        if now.day <= 7:
            return True
    return False

def mtf_confirm(df_h1, df_h4):
    regime_h4 = detect_market_regime(df_h4)
    regime_h1 = detect_market_regime(df_h1)
    if regime_h1 == "TREND_UP" and regime_h4 in ["TREND_UP", "HIGH_VOL", "RANGE"]:
        return True
    if regime_h1 == "TREND_DOWN" and regime_h4 in ["TREND_DOWN", "HIGH_VOL", "RANGE"]:
        return True
    if regime_h1 == "RANGE" and regime_h4 == "RANGE":
        return True
    if regime_h1 == "HIGH_VOL" and regime_h4 in ["HIGH_VOL", "TREND_UP", "TREND_DOWN"]:
        return True
    return False

def is_trading_session():
    """
    ตรวจสอบว่าเป็นช่วงเวลาที่เหมาะแก่การเทรดหรือไม่
    เฉพาะ London (14:00-17:00 น. ไทย) และ New York (20:00-23:00 น. ไทย)
    """
    now_utc = datetime.now(timezone.utc)
    now_thai = now_utc + timedelta(hours=7)
    hour = now_thai.hour
    if (14 <= hour <= 17) or (20 <= hour <= 23):
        return True
    return False