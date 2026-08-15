import os, json, requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# โหลดค่าจาก .env
load_dotenv()

from market_regime import detect_market_regime
from smc_strategy import find_smc_setup, calculate_atr
from filters import is_near_news, mtf_confirm, is_trading_session

LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_USER_ID = os.environ['LINE_USER_ID']

SYMBOL = "GC=F"
TIMEFRAME_H1 = "1h"
TIMEFRAME_H4 = "4h"
STATE_FILE = "pending_orders.json"
MAX_AGE_HOURS = 12
MIN_RR = 2.0  # Risk/Reward ขั้นต่ำ

def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
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
            if current_price < o['invalidation']:
                o['status'] = 'cancelled'
                cancelled.append(o)
            elif current_price <= o['entry']:
                o['status'] = 'triggered'
                triggered.append(o)
        elif o['type'] == 'SELL_LIMIT':
            if current_price > o['invalidation']:
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
    """เรียก ChatGPT เพื่อยืนยันสัญญาณ (ถ้ามี API Key)"""
    try:
        import openai
    except ImportError:
        return None

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None
    openai.api_key = api_key

    recent_data = df_h1.tail(5)[['Open','High','Low','Close']].to_string()
    prompt = f"""
    วิเคราะห์สัญญาณเทรดทองคำ (XAUUSD) ด้วยกลยุทธ์ Smart Money Concept

    ข้อมูลราคา 5 แท่งล่าสุด:
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
                {"role": "system", "content": "คุณคือผู้เชี่ยวชาญด้านการเทรดทองคำด้วย SMC"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=150
        )
        content = response['choices'][0]['message']['content']
        result = json.loads(content)
        return result
    except Exception as e:
        print(f"ChatGPT error: {e}")
        return None

def send_morning_status():
    now = datetime.now(timezone.utc)
    now_thai = now + timedelta(hours=7)
    msg = (f"☀️ ระบบ Gold SMC Signal (Pro) ทำงานปกติ\n"
           f"วันที่: {now_thai.strftime('%d/%m/%Y')}\n"
           f"เวลา (ไทย): {now_thai.strftime('%H:%M')} น.\n"
           f"กลยุทธ์: Sweep + BOS + FVG/OB + OTE + Vol\n"
           f"รอบการทำงาน: ทุก 1 ชั่วโมง\n"
           f"--------------------------------\n"
           f"จะแจ้งเตือนเมื่อพบ Setup ตามเงื่อนไข")
    send_line_message(msg)

def main():
    now_utc = datetime.now(timezone.utc)
    now_thai = now_utc + timedelta(hours=7)
    print(f"=== ระบบเริ่มทำงาน === เวลาไทย: {now_thai.strftime('%d/%m/%Y %H:%M:%S')} น.")

    if now_thai.hour == 7 and now_thai.minute < 60:
        print("🕖 ส่งข้อความแจ้งเตือนตอนเช้า...")
        send_morning_status()

    print("กำลังดึงข้อมูลจาก yfinance...")
    df_h1 = yf.download(SYMBOL, period="5d", interval=TIMEFRAME_H1)
    df_h4 = yf.download(SYMBOL, period="20d", interval=TIMEFRAME_H4)

    if df_h1.empty or df_h4.empty:
        print("ไม่สามารถดึงข้อมูลได้")
        return

    df_h1.columns = ['Open','High','Low','Close','Volume']
    df_h4.columns = ['Open','High','Low','Close','Volume']

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

    # ตรวจสอบ Session Filter
    if not is_trading_session():
        print("❌ อยู่นอกช่วงเวลาทำการ (London/NY) - ไม่เปิดสัญญาณใหม่")
        return

    # ตรวจสอบสภาพตลาด
    regime_h1 = detect_market_regime(df_h1)
    regime_h4 = detect_market_regime(df_h4)
    print(f"📊 สภาพตลาด H1: {regime_h1}")
    print(f"📊 สภาพตลาด H4: {regime_h4}")

    if not mtf_confirm(df_h1, df_h4):
        print("❌ MTF ไม่ยืนยัน - ไม่เปิดสัญญาณใหม่")
        return

    print("🔍 กำลังหาลำดับ SMC + OTE + Volume...")
    setup = find_smc_setup(df_h1)

    if setup is None:
        print("❌ ไม่พบ Setup ตามเงื่อนไข")
        return

    if is_near_news():
        print("ใกล้ข่าวสำคัญ - ข้าม")
        return

    # ตรวจสอบ RR ขั้นต่ำ
    temp_order = {
        'type': setup['type'],
        'entry': setup['entry'],
        'sl': setup['sl'],
        'tp': setup['tp']
    }
    rr = calculate_rr(temp_order)
    print(f"Risk/Reward: {rr:.2f}")
    if rr < MIN_RR:
        print(f"❌ RR ต่ำกว่า {MIN_RR} - ข้าม")
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
                print("❌ สัญญาณไม่ผ่าน AI (คะแนนต่ำกว่า 70)")
                return
            gpt_extra = (f"\n🤖 AI Score: {score}\n"
                         f"ความเห็น: {gpt_result.get('opinion')}\n"
                         f"ความเสี่ยง: {gpt_result.get('risk')}")

    if has_nearby(pending, setup['entry']):
        print("มี pending ใกล้เคียงอยู่แล้ว")
        return

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
    msg = (f"📊 GOLD SMC SIGNAL (Pro)\n"
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