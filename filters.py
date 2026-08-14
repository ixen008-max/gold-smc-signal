import pandas as pd
from market_regime import detect_market_regime

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
    # ====== แก้: ใช้ timezone-aware ======
    now = datetime.now(timezone.utc)
    
    # ====== ปรับ: บล็อกเฉพาะช่วงข่าวใหญ่จริง ๆ ======
    # Non-Farm Payrolls: วันศุกร์แรกของเดือน 12:30-14:00 UTC
    if now.weekday() == 4 and 12 <= now.hour <= 14:
        # เช็คว่าเป็นวันศุกร์แรกของเดือนหรือไม่
        if now.day <= 7:
            return True
    
    # FOMC: วันพุธที่มีการประชุม (ประมาณ 18:00-20:00 UTC)
    # ไม่สามารถเช็คได้ละเอียดในโค้ดง่าย ๆ จึงไม่บล็อก
    
    return False

def mtf_confirm(df_h1, df_h4):
    regime_h4 = detect_market_regime(df_h4)
    regime_h1 = detect_market_regime(df_h1)
    
    # ====== แบบผ่อนปรน: รับสัญญาณจาก H1 ถ้า H4 ไม่ได้แย้งทิศทาง ======
    if regime_h1 == "TREND_UP" and regime_h4 in ["TREND_UP", "HIGH_VOL", "RANGE"]:
        return True
    if regime_h1 == "TREND_DOWN" and regime_h4 in ["TREND_DOWN", "HIGH_VOL", "RANGE"]:
        return True
    if regime_h1 == "RANGE" and regime_h4 == "RANGE":
        return True
    # ====== เพิ่ม: HIGH_VOL ก็ให้เทรดได้ ถ้า H4 ไม่ใช่ RANGE ======
    if regime_h1 == "HIGH_VOL" and regime_h4 in ["HIGH_VOL", "TREND_UP", "TREND_DOWN"]:
        return True
    
    return False