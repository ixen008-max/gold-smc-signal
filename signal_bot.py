import os, json, requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

from smc_strategy import find_smc_setup, find_swings, find_order_blocks, find_fvg, calculate_atr
from ichimoku_strategy import calculate_ichimoku, get_ichimoku_signal
from filters import is_near_news

LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_USER_ID = os.environ['LINE_USER_ID']
TWELVEDATA_API_KEY = os.environ.get('TWELVEDATA_API_KEY')

STATE_FILE = "pending_orders.json"
MAX_AGE_HOURS = 12
MIN_RR = 1.67
MIN_SL_DISTANCE = 2.0

TIMEFRAME_MAIN = "1h"
TIMEFRAME_REFINE = "15min"

def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]}
    resp = requests.post(url, json=payload, headers=headers)
    print(f"LINE response: {resp.status_code} - {resp.text}")
    return resp.status_code

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return []

def save_state(orders):
    with open(STATE_FILE, 'w') as f:
        json.dump(orders, f, indent=2)

def clean_expired(orders, now):
    return [o for o in orders if (o['status'] == 'active' and 
            (datetime.fromisoformat(o['created_time']) + timedelta(hours=o['expiry_hours']) > now))]

def has_nearby(orders, entry, pip_dist=5.0):
    for o in orders:
        if o['status'] == 'active' and abs(o['entry'] - entry) < pip_dist * 0.1:
            return True
    return False

def check_pending_invalidations(orders, current_price):
    cancelled = []
    triggered = []
    for o in orders:
        if o['status'] != 'active':
            continue
        if o['type'] == 'BUY_LIMIT':
            if current_price < o.get('invalidation', current_price):
                o['status'] = 'cancelled'
                cancelled.append(o)
            elif current_price <= o['entry']:
                o['status'] = 'triggered'
                triggered.append(o)
        elif o['type'] == 'SELL_LIMIT':
            if current_price > o.get('invalidation', current_price):
                o['status'] = 'cancelled'
                cancelled.append(o)
            elif current_price >= o['entry']:
                o['status'] = 'triggered'
                triggered.append(o)
    return cancelled, triggered

def calculate_rr(order):
    if order['type'] == 'BUY_LIMIT':
        risk = order['entry'] - order['sl']
        reward = order['tp'] - order['entry']
    else:
        risk = order['sl'] - order['entry']
        reward = order['entry'] - order['tp']
    if risk <= 0:
        return 0
    return reward / risk

def get_twelvedata(symbol="XAU/USD", interval="1h", outputsize=120):
    if not TWELVEDATA_API_KEY:
        raise Exception("ไม่พบ TWELVEDATA_API_KEY")
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": interval, "outputsize": outputsize,
              "apikey": TWELVEDATA_API_KEY, "timezone": "UTC"}
    resp = requests.get(url, params=params)
    data = resp.json()
    if "values" not in data:
        raise Exception(f"Twelve Data error: {data}")
    df = pd.DataFrame(data["values"])
    df = df.rename(columns={"datetime": "datetime", "open": "Open", "high": "High",
                            "low": "Low", "close": "Close", "volume": "Volume"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime")
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    numeric_cols = ["Open","High","Low","Close","Volume"]
    df[numeric_cols] = df[numeric_cols].astype(float)
    df = df.set_index("datetime")
    return df

def refine_entry_with_m15(setup, df_m15):
    """ปรับจุดเข้าด้วย M15 สำหรับ SMC โดยหา FVG/OB ใกล้ราคาปัจจุบัน"""
    if df_m15 is None or df_m15.empty:
        return setup
    current_price = df_m15['Close'].iloc[-1]
    entry_original = setup['entry']
    if setup['type'] == 'BUY_LIMIT':
        zone_top = entry_original
        zone_bottom = current_price - 5.0
    else:
        zone_top = current_price + 5.0
        zone_bottom = entry_original

    fvg_list = find_fvg(df_m15, direction='bullish' if setup['type'] == 'BUY_LIMIT' else 'bearish')
    swings_high, swings_low = find_swings(df_m15, window=3)
    if setup['type'] == 'BUY_LIMIT':
        obs = find_order_blocks(df_m15, swings_low, mode='Bullish')
    else:
        obs = find_order_blocks(df_m15, swings_high, mode='Bearish')

    candidates = []
    for top, bottom, idx in fvg_list:
        if setup['type'] == 'BUY_LIMIT':
            if bottom >= zone_bottom and bottom <= zone_top:
                candidates.append(bottom)
        else:
            if top <= zone_top and top >= zone_bottom:
                candidates.append(top)

    for top, bottom, idx in obs:
        if setup['type'] == 'BUY_LIMIT':
            if bottom >= zone_bottom and bottom <= zone_top:
                candidates.append(bottom)
        else:
            if top <= zone_top and top >= zone_bottom:
                candidates.append(top)

    if candidates:
        new_entry = min(candidates) if setup['type'] == 'BUY_LIMIT' else max(candidates)
        temp_order = {'type': setup['type'], 'entry': new_entry, 'sl': setup['sl'], 'tp': setup['tp']}
        if calculate_rr(temp_order) >= MIN_RR:
            setup['entry'] = new_entry
            setup['strategy'] += " + M15 Refined"
    return setup

def trigger_with_m15_ichimoku(df_m15, signal):
    """Trigger ด้วย M15 สำหรับ Ichimoku"""
    if df_m15 is None or df_m15.empty:
        return True
    last3 = df_m15.tail(3)
    if signal == "BUY":
        return (last3['Close'].iloc[-1] > last3['Open'].iloc[-1] and
                last3['Low'].iloc[-1] < last3['Low'].iloc[-2])
    elif signal == "SELL":
        return (last3['Close'].iloc[-1] < last3['Open'].iloc[-1] and
                last3['High'].iloc[-1] > last3['High'].iloc[-2])
    return False

def is_killzone():
    """ตรวจสอบช่วงเวลา London/NY Killzone ตามเวลาไทย"""
    now = datetime.now(timezone.utc) + timedelta(hours=7)
    hour = now.hour
    if (14 <= hour <= 17) or (20 <= hour <= 23):
        return True
    return False

def detect_trend_ichimoku_h1(df_h1):
    """ใช้ Ichimoku H1 กำหนดแนวโน้ม คืนค่า bullish/bearish/sideways"""
    if df_h1 is None or df_h1.empty:
        return 'sideways'

    tenkan, kijun, senkou_a, senkou_b, _ = calculate_ichimoku(df_h1)
    price = df_h1['Close'].iloc[-1]
    above_cloud = price > max(senkou_a.iloc[-1], senkou_b.iloc[-1])
    below_cloud = price < min(senkou_a.iloc[-1], senkou_b.iloc[-1])
    tenkan_above_kijun = tenkan.iloc[-1] > kijun.iloc[-1]
    tenkan_below_kijun = tenkan.iloc[-1] < kijun.iloc[-1]

    if above_cloud and tenkan_above_kijun:
        return 'bullish'
    elif below_cloud and tenkan_below_kijun:
        return 'bearish'
    else:
        return 'sideways'

def silver_bullet_signal(df_h1, df_m15):
    """กลยุทธ์สำรอง Silver Bullet เฉพาะช่วง Killzone"""
    if not is_killzone():
        return None

    trend = detect_trend_ichimoku_h1(df_h1)
    if trend not in ['bullish', 'bearish']:
        return None

    swings_high, swings_low = find_swings(df_m15, window=3)
    if not swings_high or not swings_low:
        return None

    current_price = df_m15['Close'].iloc[-1]
    current_low = df_m15['Low'].iloc[-1]
    current_high = df_m15['High'].iloc[-1]

    setup = None

    if trend == 'bullish':
        last_low_idx, last_low_price = swings_low[-1]
        if current_low < last_low_price and current_price > last_low_price:
            fvgs = find_fvg(df_m15, direction='bullish')
            for top, bottom, idx in reversed(fvgs):
                if bottom < current_price and bottom > last_low_price:
                    entry = bottom
                    sl = last_low_price - 0.5
                    invalidation = last_low_price
                    if entry - sl < MIN_SL_DISTANCE:
                        continue
                    tp = entry + MIN_RR * (entry - sl)
                    setup = {
                        'type': 'BUY_LIMIT',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'invalidation': invalidation,
                        'strategy': 'Silver Bullet + Trend',
                        'fib_level': 'N/A',
                        'sr_level': 'N/A',
                        'ote_zone': 'N/A'
                    }
                    break
    elif trend == 'bearish':
        last_high_idx, last_high_price = swings_high[-1]
        if current_high > last_high_price and current_price < last_high_price:
            fvgs = find_fvg(df_m15, direction='bearish')
            for top, bottom, idx in reversed(fvgs):
                if top > current_price and top < last_high_price:
                    entry = top
                    sl = last_high_price + 0.5
                    invalidation = last_high_price
                    if sl - entry < MIN_SL_DISTANCE:
                        continue
                    tp = entry - MIN_RR * (sl - entry)
                    setup = {
                        'type': 'SELL_LIMIT',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'invalidation': invalidation,
                        'strategy': 'Silver Bullet + Trend',
                        'fib_level': 'N/A',
                        'sr_level': 'N/A',
                        'ote_zone': 'N/A'
                    }
                    break

    return setup

def ichimoku_pullback_signal(df_h1, df_m15):
    """กลยุทธ์ Ichimoku Pullback + M15 Price Action"""
    if df_h1 is None or df_h1.empty or df_m15 is None or df_m15.empty:
        return None

    tenkan, kijun, senkou_a, senkou_b, chikou = calculate_ichimoku(df_h1)
    price_h1 = df_h1['Close'].iloc[-1]

    above_cloud = price_h1 > max(senkou_a.iloc[-1], senkou_b.iloc[-1])
    below_cloud = price_h1 < min(senkou_a.iloc[-1], senkou_b.iloc[-1])
    tenkan_above_kijun = tenkan.iloc[-1] > kijun.iloc[-1]
    tenkan_below_kijun = tenkan.iloc[-1] < kijun.iloc[-1]

    if above_cloud and tenkan_above_kijun:
        bias = 'bullish'
    elif below_cloud and tenkan_below_kijun:
        bias = 'bearish'
    else:
        return None

    swings_high, swings_low = find_swings(df_m15, window=3)
    if not swings_high or not swings_low:
        return None

    current_price = df_m15['Close'].iloc[-1]
    atr_m15 = calculate_atr(df_m15, period=14)

    pullback_zone_top = max(tenkan.iloc[-1], kijun.iloc[-1])
    pullback_zone_bottom = min(tenkan.iloc[-1], kijun.iloc[-1])

    setup = None

    if bias == 'bullish':
        if current_price > pullback_zone_bottom and current_price <= pullback_zone_top + atr_m15:
            last3 = df_m15.tail(3)
            bullish_pa = (
                last3['Close'].iloc[-1] > last3['Open'].iloc[-1] and
                last3['Low'].iloc[-1] < last3['Low'].iloc[-2]
            )
            if bullish_pa:
                entry = current_price
                sl = swings_low[-1][1] - 0.5
                invalidation = sl
                if entry - sl < MIN_SL_DISTANCE:
                    return None
                tp = entry + MIN_RR * (entry - sl)
                setup = {
                    'type': 'BUY_LIMIT',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'invalidation': invalidation,
                    'strategy': 'Ichimoku Pullback + M15 PA',
                    'fib_level': 'N/A',
                    'sr_level': 'N/A',
                    'ote_zone': 'N/A'
                }

    elif bias == 'bearish':
        if current_price < pullback_zone_top and current_price >= pullback_zone_bottom - atr_m15:
            last3 = df_m15.tail(3)
            bearish_pa = (
                last3['Close'].iloc[-1] < last3['Open'].iloc[-1] and
                last3['High'].iloc[-1] > last3['High'].iloc[-2]
            )
            if bearish_pa:
                entry = current_price
                sl = swings_high[-1][1] + 0.5
                invalidation = sl
                if sl - entry < MIN_SL_DISTANCE:
                    return None
                tp = entry - MIN_RR * (sl - entry)
                setup = {
                    'type': 'SELL_LIMIT',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'invalidation': invalidation,
                    'strategy': 'Ichimoku Pullback + M15 PA',
                    'fib_level': 'N/A',
                    'sr_level': 'N/A',
                    'ote_zone': 'N/A'
                }

    return setup

def breakout_retest_signal(df_h1, df_m15):
    """
    กลยุทธ์ M15 Breakout + Retest (ไม่ต้องรอ Killzone)
    ใช้ H1 Ichimoku Trend เป็นตัวกรองทิศทาง
    """
    if df_h1 is None or df_h1.empty or df_m15 is None or df_m15.empty:
        return None

    trend = detect_trend_ichimoku_h1(df_h1)
    if trend not in ['bullish', 'bearish']:
        return None

    swings_high, swings_low = find_swings(df_m15, window=3)
    if not swings_high or not swings_low:
        return None

    current_price = df_m15['Close'].iloc[-1]
    atr_m15 = calculate_atr(df_m15, period=14)
    setup = None

    if trend == 'bullish':
        last_high_idx, last_high_price = swings_high[-1]
        # ราคาเบรก high
        if current_price > last_high_price:
            # รอ retest กลับมาใกล้ last_high
            if abs(current_price - last_high_price) <= atr_m15 * 0.5:
                # ตรวจ M15 แท่งกลับตัว
                last3 = df_m15.tail(3)
                bullish_pa = (
                    last3['Close'].iloc[-1] > last3['Open'].iloc[-1] and
                    last3['Low'].iloc[-1] < last3['Low'].iloc[-2]
                )
                if bullish_pa:
                    entry = current_price
                    sl = swings_low[-1][1] - 0.5  # swing low ล่าสุด
                    invalidation = sl
                    if entry - sl < MIN_SL_DISTANCE:
                        return None
                    tp = entry + MIN_RR * (entry - sl)
                    setup = {
                        'type': 'BUY_LIMIT',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'invalidation': invalidation,
                        'strategy': 'M15 Breakout + Retest (Bullish)',
                        'fib_level': 'N/A',
                        'sr_level': f'Breakout @ {last_high_price:.2f}',
                        'ote_zone': 'N/A'
                    }

    elif trend == 'bearish':
        last_low_idx, last_low_price = swings_low[-1]
        if current_price < last_low_price:
            if abs(current_price - last_low_price) <= atr_m15 * 0.5:
                last3 = df_m15.tail(3)
                bearish_pa = (
                    last3['Close'].iloc[-1] < last3['Open'].iloc[-1] and
                    last3['High'].iloc[-1] > last3['High'].iloc[-2]
                )
                if bearish_pa:
                    entry = current_price
                    sl = swings_high[-1][1] + 0.5
                    invalidation = sl
                    if sl - entry < MIN_SL_DISTANCE:
                        return None
                    tp = entry - MIN_RR * (sl - entry)
                    setup = {
                        'type': 'SELL_LIMIT',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'invalidation': invalidation,
                        'strategy': 'M15 Breakout + Retest (Bearish)',
                        'fib_level': 'N/A',
                        'sr_level': f'Breakout @ {last_low_price:.2f}',
                        'ote_zone': 'N/A'
                    }

    return setup

def send_running_status():
    now = datetime.now(timezone.utc) + timedelta(hours=7)
    msg = (f"🔄 ระบบกำลังทำงาน\n"
           f"เวลาไทย: {now.strftime('%H:%M')} น.\n"
           f"กลยุทธ์: SMC+Ichimoku+Silver Bullet+Pullback+Breakout\n"
           f"Timeframe: {TIMEFRAME_MAIN}/{TIMEFRAME_REFINE}\n"
           f"RR ขั้นต่ำ: {MIN_RR}\n"
           f"รอบ: ทุก 15 นาที")
    send_line_message(msg)

def send_morning_status():
    now = datetime.now(timezone.utc) + timedelta(hours=7)
    msg = (f"☀️ ระบบ Gold Signal ทำงานปกติ\n"
           f"วันที่: {now.strftime('%d/%m/%Y')}\n"
           f"เวลา (ไทย): {now.strftime('%H:%M')} น.\n"
           f"กลยุทธ์: SMC + Ichimoku + Silver Bullet + Pullback + Breakout\n"
           f"Timeframe: {TIMEFRAME_MAIN} + {TIMEFRAME_REFINE}\n"
           f"RR ขั้นต่ำ: {MIN_RR}\n"
           f"--------------------------------\n"
           f"จะแจ้งเตือนเมื่อพบ Setup ตามเงื่อนไข")
    send_line_message(msg)

def main():
    now_utc = datetime.now(timezone.utc)
    now_thai = now_utc + timedelta(hours=7)
    print(f"=== ระบบเริ่มทำงาน === เวลาไทย: {now_thai.strftime('%d/%m/%Y %H:%M:%S')} น.")

    # ส่งสถานะรายชั่วโมง
    if now_thai.minute <= 2:
        send_running_status()

    # ส่งข้อความตอนเช้า
    if now_thai.hour == 7 and now_thai.minute < 60:
        send_morning_status()

    print("กำลังดึงข้อมูลจาก Twelve Data...")
    try:
        df_h1 = get_twelvedata(symbol="XAU/USD", interval=TIMEFRAME_MAIN, outputsize=120)
        df_m15 = get_twelvedata(symbol="XAU/USD", interval=TIMEFRAME_REFINE, outputsize=240)
    except Exception as e:
        print(f"❌ ดึงข้อมูลล้มเหลว: {e}")
        return

    current_price = df_h1['Close'].iloc[-1]
    print(f"ราคาปัจจุบัน: {current_price}")

    # จัดการ pending orders เดิม
    pending = load_state()
    cancelled, triggered = check_pending_invalidations(pending, current_price)
    if cancelled:
        for o in cancelled:
            msg = (f"❌ ยกเลิกสัญญาณ {o['type']}\n"
                   f"Entry: {o['entry']}\nInvalidation: {o['invalidation']}\nเหตุผล: ราคาทะลุจุด Invalidation")
            send_line_message(msg)
    if triggered:
        for o in triggered:
            msg = (f"⚡ ราคามาถึง Entry ของ {o['type']}\nEntry: {o['entry']}\nSL: {o['sl']}\nTP: {o['tp']}\nโปรดตรวจสอบ")
            send_line_message(msg)
    pending = clean_expired(pending, now_utc)
    pending = [o for o in pending if o['status'] == 'active']
    save_state(pending)

    # จำกัด pending ไม่เกิน 2 ไม้
    if len(pending) >= 2:
        print("❌ มี pending ครบ 2 ไม้แล้ว - ไม่เปิดสัญญาณใหม่")
        return

    # ตรวจข่าวสำคัญ (NFP คร่าว ๆ)
    if is_near_news():
        print("⛔ ใกล้ข่าวสำคัญ - ข้ามการเปิดสัญญาณ")
        msg = "⛔ ตรวจพบข่าวสำคัญ ระบบจะไม่เปิดสัญญาณใหม่จนกว่าจะผ่านช่วงข่าว"
        send_line_message(msg)
        return

    # ===== 1. ลองหา SMC =====
    setup = None
    print("🔍 กำลังหา SMC...")
    setup = find_smc_setup(df_h1)
    if setup:
        rr = calculate_rr(setup)
        sl_dist = abs(setup['entry'] - setup['sl'])
        if rr < MIN_RR or sl_dist < MIN_SL_DISTANCE:
            print(f"❌ SMC ไม่ผ่าน RR/SL distance (RR={rr:.2f}, SL={sl_dist:.2f}) - ข้าม")
            setup = None
        else:
            setup = refine_entry_with_m15(setup, df_m15)

    # ===== 2. ถ้า SMC ไม่พบ ลอง Ichimoku แบบเดิม =====
    if setup is None:
        print("🔍 กำลังหา Ichimoku...")
        signal = get_ichimoku_signal(df_h1)
        if signal:
            if trigger_with_m15_ichimoku(df_m15, signal):
                tenkan, kijun, senkou_a, senkou_b, _ = calculate_ichimoku(df_h1)
                if signal == "BUY":
                    entry = df_m15['Low'].iloc[-1]
                    sl = min(senkou_a.iloc[-1], senkou_b.iloc[-1]) - 3.0
                    invalidation = min(senkou_a.iloc[-1], senkou_b.iloc[-1])
                    tp = entry + MIN_RR * (entry - sl)
                    setup = {
                        'type': 'BUY_LIMIT',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'invalidation': invalidation,
                        'strategy': f'Ichimoku + H1 + M15 ({signal})',
                        'fib_level': 'N/A',
                        'sr_level': 'N/A',
                        'ote_zone': 'N/A'
                    }
                else:
                    entry = df_m15['High'].iloc[-1]
                    sl = max(senkou_a.iloc[-1], senkou_b.iloc[-1]) + 3.0
                    invalidation = max(senkou_a.iloc[-1], senkou_b.iloc[-1])
                    tp = entry - MIN_RR * (sl - entry)
                    setup = {
                        'type': 'SELL_LIMIT',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'invalidation': invalidation,
                        'strategy': f'Ichimoku + H1 + M15 ({signal})',
                        'fib_level': 'N/A',
                        'sr_level': 'N/A',
                        'ote_zone': 'N/A'
                    }
                rr = calculate_rr(setup)
                sl_dist = abs(setup['entry'] - setup['sl'])
                if rr < MIN_RR or sl_dist < MIN_SL_DISTANCE:
                    print(f"❌ Ichimoku ไม่ผ่าน RR/SL (RR={rr:.2f}, SL={sl_dist:.2f}) - ข้าม")
                    setup = None
            else:
                print("Ichimoku ไม่ผ่าน M15 Trigger")
                setup = None
        else:
            print("ไม่มีสัญญาณ Ichimoku")

    # ===== 3. ถ้ายังไม่มี setup → Ichimoku Pullback =====
    if setup is None:
        print("🔍 กำลังหา Ichimoku Pullback...")
        setup = ichimoku_pullback_signal(df_h1, df_m15)
        if setup:
            rr = calculate_rr(setup)
            sl_dist = abs(setup['entry'] - setup['sl'])
            if rr < MIN_RR or sl_dist < MIN_SL_DISTANCE:
                print(f"❌ Ichimoku Pullback ไม่ผ่าน RR/SL - ข้าม")
                setup = None

    # ===== 4. ถ้ายังไม่มี setup → Silver Bullet =====
    if setup is None:
        print("🔍 กำลังหา Silver Bullet...")
        setup = silver_bullet_signal(df_h1, df_m15)
        if setup:
            rr = calculate_rr(setup)
            sl_dist = abs(setup['entry'] - setup['sl'])
            if rr < MIN_RR or sl_dist < MIN_SL_DISTANCE:
                print(f"❌ Silver Bullet ไม่ผ่าน RR/SL - ข้าม")
                setup = None

    # ===== 5. ถ้ายังไม่มี setup → Breakout Retest (ใหม่) =====
    if setup is None:
        print("🔍 กำลังหา Breakout + Retest...")
        setup = breakout_retest_signal(df_h1, df_m15)
        if setup:
            rr = calculate_rr(setup)
            sl_dist = abs(setup['entry'] - setup['sl'])
            if rr < MIN_RR or sl_dist < MIN_SL_DISTANCE:
                print(f"❌ Breakout Retest ไม่ผ่าน RR/SL - ข้าม")
                setup = None

    if setup is None:
        print("❌ ไม่พบ Setup ตามเงื่อนไข")
        return

    # ตรวจ RR ขั้นสุดท้าย
    rr = calculate_rr(setup)
    if rr < MIN_RR:
        print(f"❌ RR ต่ำกว่า {MIN_RR} - ไม่ส่ง")
        return

    if has_nearby(pending, setup['entry']):
        print("มี pending ใกล้เคียงอยู่แล้ว")
        return

    # บันทึก pending
    order_id = f"GOLD_{setup['type']}_{now_utc.strftime('%Y%m%d_%H%M')}"
    order = {
        "id": order_id,
        "symbol": "XAUUSD",
        "type": setup['type'],
        "entry": round(setup['entry'], 2),
        "sl": round(setup['sl'], 2),
        "tp": round(setup['tp'], 2),
        "invalidation": round(setup['invalidation'], 2),
        "created_time": now_utc.isoformat(),
        "expiry_hours": MAX_AGE_HOURS,
        "strategy": setup['strategy'],
        "fib_level": setup.get('fib_level', 'N/A'),
        "sr_level": setup.get('sr_level', 'N/A'),
        "ote_zone": setup.get('ote_zone', 'N/A'),
        "status": "active"
    }
    pending.append(order)
    save_state(pending)

    rr_str = f"{rr:.2f}"
    msg = (f"📊 GOLD SIGNAL\n"
           f"Direction: {order['type']}\n"
           f"Entry: {order['entry']}\n"
           f"SL: {order['sl']}\n"
           f"TP: {order['tp']}\n"
           f"Invalidation: {order['invalidation']}\n"
           f"RR: 1:{rr_str}\n"
           f"Strategy: {order['strategy']}\n"
           f"Fibonacci: {order['fib_level']}\n"
           f"S/R: {order['sr_level']}\n"
           f"OTE: {order['ote_zone']}\n"
           f"Expiry: {MAX_AGE_HOURS}h\n"
           f"⚠️ เทรดด้วยตนเอง")
    send_line_message(msg)
    print("✅ ส่งสัญญาณแล้ว")

if __name__ == "__main__":
    main()