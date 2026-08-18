import os, json, requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

from market_regime import detect_market_regime
from smc_strategy import find_smc_setup, find_swings, find_order_blocks, find_fvg
from ichimoku_strategy import calculate_ichimoku, get_ichimoku_signal
from filters import is_near_news, mtf_confirm

LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_USER_ID = os.environ['LINE_USER_ID']
TWELVEDATA_API_KEY = os.environ.get('TWELVEDATA_API_KEY')

STATE_FILE = "pending_orders.json"
MAX_AGE_HOURS = 12
MIN_RR = 2.0

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
    return [o for o in orders if (o['status']=='active' and 
            (datetime.fromisoformat(o['created_time']) + timedelta(hours=o['expiry_hours']) > now))]

def has_nearby(orders, entry, pip_dist=5.0):
    for o in orders:
        if o['status']=='active' and abs(o['entry'] - entry) < pip_dist * 0.1:
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

def analyze_with_chatgpt(setup, df_h1):
    try:
        import openai
    except ImportError:
        return None
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None
    openai.api_key = api_key

    recent_data = df_h1.tail(10)[['Open','High','Low','Close']].to_string()
    prompt = f"""
    วิเคราะห์สัญญาณเทรดทองคำ (XAUUSD) ด้วยกลยุทธ์ SMC/Ichimoku + Price Action

    ข้อมูลราคา 10 แท่ง H1:
    {recent_data}

    สัญญาณที่ระบบพบ:
    - Direction: {setup['type']}
    - Entry: {setup['entry']:.2f}
    - SL: {setup['sl']:.2f}
    - TP: {setup['tp']:.2f}
    - Invalidation: {setup['invalidation']:.2f}
    - Strategy: {setup['strategy']}
    - Fibonacci Level: {setup.get('fib_level', 'N/A')}
    - SR Confluence: {setup.get('sr_level', 'N/A')}
    - OTE Zone: {setup.get('ote_zone', 'N/A')}

    จงประเมินความน่าเชื่อถือของสัญญาณนี้ ให้คะแนน 0-100
    ตอบเป็น JSON เท่านั้น: {{"score": 75, "opinion": "....", "risk": "..."}}
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือผู้เชี่ยวชาญด้านการเทรดทองคำด้วย SMC และ Ichimoku"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=200
        )
        content = response['choices'][0]['message']['content']
        return json.loads(content)
    except Exception as e:
        print(f"ChatGPT error: {e}")
        return None

def send_morning_status():
    now = datetime.now(timezone.utc)
    now_thai = now + timedelta(hours=7)
    msg = (f"☀️ ระบบ Gold SMC+Ichimoku ทำงานปกติ\n"
           f"วันที่: {now_thai.strftime('%d/%m/%Y')}\n"
           f"เวลา (ไทย): {now_thai.strftime('%H:%M')} น.\n"
           f"กลยุทธ์: SMC + Ichimoku (H4+H1+M15)\n"
           f"รอบการทำงาน: ทุก 15 นาที\n"
           f"--------------------------------\n"
           f"จะแจ้งเตือนเมื่อพบ Setup ตามเงื่อนไข")
    send_line_message(msg)

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
        print(f"⚠️ ไม่มีข้อมูล Volume จาก Twelve Data สำหรับ {symbol} - จะใช้ Volume=0")
    numeric_cols = ["Open","High","Low","Close","Volume"]
    df[numeric_cols] = df[numeric_cols].astype(float)
    df = df.set_index("datetime")
    return df

def refine_entry_with_m15(setup, df_m15):
    """ปรับจุดเข้าด้วย M15 โดยหาจุด FVG/OB ระหว่างราคาปัจจุบันกับ Entry เดิม"""
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

    fvg_list = find_fvg(df_m15, direction='bullish' if setup['type']=='BUY_LIMIT' else 'bearish')
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
        new_entry = min(candidates) if setup['type']=='BUY_LIMIT' else max(candidates)
        temp_order = {'type': setup['type'], 'entry': new_entry, 'sl': setup['sl'], 'tp': setup['tp']}
        if calculate_rr(temp_order) >= MIN_RR:
            setup['entry'] = new_entry
            setup['strategy'] += " + M15 Refined"
    return setup

def confirm_with_h4_ichimoku(df_h4, signal):
    if df_h4 is None or df_h4.empty:
        return True
    tenkan, kijun, senkou_a, senkou_b, _ = calculate_ichimoku(df_h4)
    current_price = df_h4['Close'].iloc[-1]
    if signal == "BUY":
        return current_price > max(senkou_a.iloc[-1], senkou_b.iloc[-1]) and tenkan.iloc[-1] > kijun.iloc[-1]
    elif signal == "SELL":
        return current_price < min(senkou_a.iloc[-1], senkou_b.iloc[-1]) and tenkan.iloc[-1] < kijun.iloc[-1]
    return False

def trigger_with_m15_ichimoku(df_m15, signal):
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

def main():
    now_utc = datetime.now(timezone.utc)
    now_thai = now_utc + timedelta(hours=7)
    print(f"=== ระบบ Gold SMC+Ichimoku เริ่มทำงาน === เวลาไทย: {now_thai.strftime('%d/%m/%Y %H:%M:%S')} น.")

    if now_thai.hour == 7 and now_thai.minute < 60:
        send_morning_status()

    print("กำลังดึงข้อมูลจาก Twelve Data...")
    try:
        df_h4 = get_twelvedata(symbol="XAU/USD", interval="4h", outputsize=120)
        df_h1 = get_twelvedata(symbol="XAU/USD", interval="1h", outputsize=120)
        df_m15 = get_twelvedata(symbol="XAU/USD", interval="15m", outputsize=240)
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
                   f"Entry: {o['entry']}\n"
                   f"Invalidation: {o['invalidation']}\n"
                   f"เหตุผล: ราคาทะลุจุด Invalidation")
            send_line_message(msg)
            print(f"ยกเลิกสัญญาณ {o['id']}")
    if triggered:
        for o in triggered:
            msg = (f"⚡ ราคามาถึง Entry ของ {o['type']}\n"
                   f"Entry: {o['entry']}\n"
                   f"SL: {o['sl']}\n"
                   f"TP: {o['tp']}\n"
                   f"โปรดตรวจสอบคำสั่งของคุณ")
            send_line_message(msg)
            print(f"Triggered: {o['id']}")

    pending = clean_expired(pending, now_utc)
    pending = [o for o in pending if o['status'] == 'active']
    save_state(pending)

    # ตรวจสอบสภาพตลาดและ MTF (สำหรับ SMC)
    regime_h4 = detect_market_regime(df_h4)
    regime_h1 = detect_market_regime(df_h1)
    print(f"📊 สภาพตลาด H4: {regime_h4}")
    print(f"📊 สภาพตลาด H1: {regime_h1}")

    setup = None

    # ====== 1. ลองหา SMC Setup ก่อน ======
    if mtf_confirm(df_h1, df_h4):
        print("🔍 กำลังหาสัญญาณ SMC...")
        setup = find_smc_setup(df_h1)
        if setup:
            print("✅ พบ SMC Setup")
            if is_near_news():
                print("ใกล้ข่าวสำคัญ - ข้าม")
                setup = None
            else:
                setup = refine_entry_with_m15(setup, df_m15)
    else:
        print("❌ MTF ไม่ยืนยันสำหรับ SMC - ข้าม SMC")

    # ====== 2. ถ้าไม่มี SMC ให้ลอง Ichimoku ======
    if setup is None:
        print("🔍 กำลังหาสัญญาณ Ichimoku...")
        signal = get_ichimoku_signal(df_h1)
        if signal:
            # ยืนยันด้วย H4 และ Trigger M15
            if confirm_with_h4_ichimoku(df_h4, signal) and trigger_with_m15_ichimoku(df_m15, signal):
                if is_near_news():
                    print("ใกล้ข่าวสำคัญ - ข้าม Ichimoku")
                else:
                    # สร้าง setup จาก Ichimoku
                    tenkan, kijun, senkou_a, senkou_b, _ = calculate_ichimoku(df_h1)
                    if signal == "BUY":
                        entry = df_h1['Close'].iloc[-1]  # ราคาปัจจุบัน เป็นจุดเข้าแบบ market?  แต่ยังไม่ดี ควรใช้ M15 low?
                        sl = min(senkou_a.iloc[-1], senkou_b.iloc[-1]) - 3.0  # ใต้ Kumo
                        tp = entry + (entry - sl) * MIN_RR
                        invalidation = min(senkou_a.iloc[-1], senkou_b.iloc[-1])
                        setup = {
                            'type': 'BUY_LIMIT',
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'invalidation': invalidation,
                            'strategy': f'Ichimoku + H4 + M15 ({signal})',
                            'fib_level': 'N/A',
                            'sr_level': 'N/A',
                            'ote_zone': 'N/A'
                        }
                    else:
                        entry = df_h1['Close'].iloc[-1]
                        sl = max(senkou_a.iloc[-1], senkou_b.iloc[-1]) + 3.0
                        tp = entry - (sl - entry) * MIN_RR
                        invalidation = max(senkou_a.iloc[-1], senkou_b.iloc[-1])
                        setup = {
                            'type': 'SELL_LIMIT',
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'invalidation': invalidation,
                            'strategy': f'Ichimoku + H4 + M15 ({signal})',
                            'fib_level': 'N/A',
                            'sr_level': 'N/A',
                            'ote_zone': 'N/A'
                        }
                    # ปรับ entry ด้วย M15 (ใช้ราคาปัจจุบัน อาจไม่ดี) ควรให้ refined? ง่าย ๆ ใช้ราคาปัจจุบันเป็น pending ไม่ได้
                    # แก้เป็นใช้ M15 ระดับ low/high
                    if signal == "BUY":
                        m15_low = df_m15['Low'].iloc[-1]
                        setup['entry'] = m15_low  # ตั้ง Buy Limit ที่ low ล่าสุด
                    else:
                        m15_high = df_m15['High'].iloc[-1]
                        setup['entry'] = m15_high
                    # ตรวจ RR
                    if calculate_rr(setup) < MIN_RR:
                        print("RR ต่ำเกินไป - ข้าม Ichimoku")
                        setup = None
            else:
                print("Ichimoku ไม่ผ่าน H4/M15 Trigger")
                setup = None
        else:
            print("ไม่มีสัญญาณ Ichimoku")

    # ====== 3. ถ้าไม่มี Setup จบ ======
    if setup is None:
        print("❌ ไม่พบ Setup ตามเงื่อนไข")
        return

    # AI Filter (ถ้ามี)
    gpt_extra = ""
    if os.environ.get('OPENAI_API_KEY'):
        print("🤖 กำลังวิเคราะห์ด้วย ChatGPT...")
        gpt_result = analyze_with_chatgpt(setup, df_h1)
        if gpt_result:
            score = gpt_result.get('score', 0)
            print(f"ChatGPT Score: {score}")
            if score < 70:
                print("❌ สัญญาณไม่ผ่าน AI")
                return
            gpt_extra = (f"\n🤖 AI Score: {score}\n"
                         f"ความเห็น: {gpt_result.get('opinion')}\n"
                         f"ความเสี่ยง: {gpt_result.get('risk')}")

    # ป้องกันซ้ำ
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

    rr = calculate_rr(order)
    rr_str = f"{rr:.2f}"
    msg = (f"📊 GOLD SIGNAL (Pro)\n"
           f"Direction: {order['type']}\n"
           f"Entry: {order['entry']}\n"
           f"SL: {order['sl']}\n"
           f"TP: {order['tp']}\n"
           f"Invalidation: {order['invalidation']}\n"
           f"RR: 1:{rr_str}\n"
           f"Strategy: {order['strategy']}\n"
           f"Fibonacci: {order['fib_level']}\n"
           f"S/R: {order['sr_level']}\n"
           f"OTE Zone: {order['ote_zone']}\n"
           f"Expiry: {MAX_AGE_HOURS}h\n"
           f"{gpt_extra}\n"
           f"⚠️ รอราคามาที่ Entry แล้ววางคำสั่งด้วยตนเอง\n"
           f"❌ หากราคาทะลุ Invalidation ก่อนถึง Entry ให้ยกเลิกสัญญาณ")
    status = send_line_message(msg)
    if status == 200:
        print("✅ ส่งสัญญาณสำเร็จ!")
    else:
        print("❌ ส่งไม่สำเร็จ")

    print(f"ราคา Invalidation: {order['invalidation']}")

if __name__ == "__main__":
    main()