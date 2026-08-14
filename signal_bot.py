import os, json, requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# โหลดค่าจาก .env
load_dotenv()

from market_regime import detect_market_regime
from smc_strategy import find_swings, find_order_blocks, find_fvg
from filters import is_valid_ob, volume_confirmation, is_choppy, is_near_news, mtf_confirm

LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_USER_ID = os.environ['LINE_USER_ID']

SYMBOL = "GC=F"
TIMEFRAME_H1 = "1h"
TIMEFRAME_H4 = "4h"
STATE_FILE = "pending_orders.json"
MAX_AGE_HOURS = 12

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
    return [o for o in orders if (o['status']=='active' and 
            (datetime.fromisoformat(o['created_time']) + timedelta(hours=o['expiry_hours']) > now))]

def has_nearby(orders, entry, pip_dist=5.0):
    for o in orders:
        if o['status']=='active' and abs(o['entry'] - entry) < pip_dist * 0.1:
            return True
    return False

def send_morning_status():
    """ส่งข้อความแจ้งเตือนตอน 7 โมงเช้าเวลาไทย ว่าระบบยังทำงานอยู่"""
    now = datetime.now(timezone.utc)
    now_thai = now + timedelta(hours=7)  # แปลงเป็นเวลาไทย (UTC+7)
    
    msg = (f"☀️ ระบบ Gold SMC Signal ทำงานปกติ\n"
           f"วันที่: {now_thai.strftime('%d/%m/%Y')}\n"
           f"เวลา (ไทย): {now_thai.strftime('%H:%M')} น.\n"
           f"สถานะ: กำลังติดตามตลาดทองคำ\n"
           f"กลยุทธ์: SMC (Order Block + FVG)\n"
           f"รอบการทำงาน: ทุก 1 ชั่วโมง\n"
           f"--------------------------------\n"
           f"จะแจ้งเตือนเมื่อพบสัญญาณที่ผ่านเงื่อนไข")
    
    send_line_message(msg)
    print(f"✅ ส่งข้อความแจ้งเตือนตอนเช้าแล้ว (เวลาไทย: {now_thai.strftime('%H:%M')} น.)")

def main():
    now = datetime.now(timezone.utc)
    now_thai = now + timedelta(hours=7)
    
    print(f"=== ระบบเริ่มทำงาน === เวลาไทย: {now_thai.strftime('%d/%m/%Y %H:%M:%S')} น.")
    
    # ====== เช็คว่าตอนนี้เป็น 7 โมงเช้าเวลาไทยหรือไม่ ======
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
    
    print(f"H1 Data: {len(df_h1)} candles")
    print(f"H4 Data: {len(df_h4)} candles")
    
    regime_h1 = detect_market_regime(df_h1)
    regime_h4 = detect_market_regime(df_h4)
    print(f"📊 สภาพตลาด H1: {regime_h1}")
    print(f"📊 สภาพตลาด H4: {regime_h4}")
    
    if not mtf_confirm(df_h1, df_h4):
        print("❌ MTF ไม่ยืนยัน - ไม่ส่งสัญญาณ")
        return
    
    regime = regime_h1
    print(f"✅ สภาพตลาด: {regime}")
    
    if regime in ["INSUFFICIENT_DATA", "UNCLEAR"]:
        print("ข้อมูลไม่เพียงพอ")
        return
    
    pending = load_state()
    now_utc = datetime.now(timezone.utc)
    pending = clean_expired(pending, now_utc)
    
    new_order = None
    swings_high, swings_low = find_swings(df_h1)
    
    if regime == "TREND_UP":
        obs = find_order_blocks(df_h1, swings_low, mode='Bullish')
        for top, bottom, idx in reversed(obs):
            if is_valid_ob(df_h1, idx) and volume_confirmation(df_h1, idx):
                entry = bottom
                sl = entry - 5.0
                tp = swings_high[-1][1] + (swings_high[-1][1] - entry) * 2
                new_order = {"type": "BUY_LIMIT", "entry": entry, "sl": sl, "tp": tp}
                break
    elif regime == "TREND_DOWN":
        obs = find_order_blocks(df_h1, swings_high, mode='Bearish')
        for top, bottom, idx in reversed(obs):
            if is_valid_ob(df_h1, idx) and volume_confirmation(df_h1, idx):
                entry = top
                sl = entry + 5.0
                tp = swings_low[-1][1] - (entry - swings_low[-1][1]) * 2
                new_order = {"type": "SELL_LIMIT", "entry": entry, "sl": sl, "tp": tp}
                break
    elif regime == "RANGE":
        pass
    elif regime == "HIGH_VOL":
        print("🔍 กำลังหา FVG ในช่วง High Volatility...")
        
        bullish_fvg = find_fvg(df_h1, direction='bullish')
        bearish_fvg = find_fvg(df_h1, direction='bearish')
        
        current_price = df_h1['Close'].iloc[-1]
        
        for top, bottom, idx in reversed(bullish_fvg):
            if bottom < current_price and (current_price - bottom) < 30.0:
                entry = bottom
                sl = entry - 5.0
                tp = top + (top - entry) * 2
                new_order = {"type": "BUY_LIMIT", "entry": entry, "sl": sl, "tp": tp}
                break
        
        if not new_order:
            for top, bottom, idx in reversed(bearish_fvg):
                if top > current_price and (top - current_price) < 30.0:
                    entry = top
                    sl = entry + 5.0
                    tp = bottom - (entry - bottom) * 2
                    new_order = {"type": "SELL_LIMIT", "entry": entry, "sl": sl, "tp": tp}
                    break
    
    if new_order:
        if regime != "HIGH_VOL" and is_choppy(df_h1):
            print("ตลาดเงียบ - ข้าม")
            return
        
        if is_near_news():
            print("ใกล้ข่าวสำคัญ - ข้าม")
            return
        
        if not has_nearby(pending, new_order['entry']):
            order_id = f"GOLD_{new_order['type']}_{now_utc.strftime('%Y%m%d_%H%M')}"
            order = {
                "id": order_id,
                "symbol": "XAUUSD",
                "type": new_order['type'],
                "entry": round(new_order['entry'], 2),
                "sl": round(new_order['sl'], 2),
                "tp": round(new_order['tp'], 2),
                "created_time": now_utc.isoformat(),
                "expiry_hours": MAX_AGE_HOURS,
                "status": "active"
            }
            pending.append(order)
            save_state(pending)
            
            msg = (f"📊 GOLD SMC SIGNAL (Manual Entry)\n"
                   f"Direction: {order['type']}\n"
                   f"Entry: {order['entry']}\n"
                   f"SL: {order['sl']}\n"
                   f"TP: {order['tp']}\n"
                   f"Strategy: {regime}\n"
                   f"Expiry: {MAX_AGE_HOURS}h from now\n"
                   f"⚠️ Please place order manually.")
            
            status_code = send_line_message(msg)
            if status_code == 200:
                print("✅ ส่งสัญญาณสำเร็จ!")
            else:
                print("❌ ส่งไม่สำเร็จ")
        else:
            print("มี pending ใกล้เคียงอยู่แล้ว")
    else:
        print("ไม่พบสัญญาณที่ผ่านเงื่อนไข")
    
    pending = clean_expired(pending, now_utc)
    save_state(pending)

if __name__ == "__main__":
    main()