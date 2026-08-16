import os, json, requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from ichimoku_strategy import calculate_ichimoku, get_ichimoku_signal

load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_USER_ID = os.environ['LINE_USER_ID']
TWELVEDATA_API_KEY = os.environ.get('TWELVEDATA_API_KEY')

PAIRS = ["USD/JPY", "GBP/JPY"]
STATE_FILE = "ichimoku_pending.json"

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
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def get_twelvedata(symbol, interval="1h", outputsize=120):
    if not TWELVEDATA_API_KEY:
        raise Exception("ไม่พบ TWELVEDATA_API_KEY")
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "timezone": "UTC"
    }
    resp = requests.get(url, params=params)
    data = resp.json()
    if "values" not in data:
        raise Exception(f"Twelve Data error: {data}")
    df = pd.DataFrame(data["values"])
    df = df.rename(columns={
        "datetime": "datetime",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume"
    })
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime")
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    numeric_cols = ["Open","High","Low","Close","Volume"]
    df[numeric_cols] = df[numeric_cols].astype(float)
    df = df.set_index("datetime")
    return df

def analyze_with_chatgpt(signal, pair, df):
    try:
        import openai
    except ImportError:
        return None
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None
    openai.api_key = api_key

    recent_data = df.tail(10)[['Open','High','Low','Close']].to_string()
    prompt = f"""
    วิเคราะห์สัญญาณเทรดคู่เงิน {pair} ที่ Timeframe 1H ด้วยกลยุทธ์ Ichimoku + Price Action
    ข้อมูลราคา 10 แท่งล่าสุด:
    {recent_data}

    สัญญาณจากระบบ: {signal}
    จงประเมินความน่าเชื่อถือของสัญญาณนี้ โดยดู Price Action (แท่งเทียน, แนวรับต้าน) ประกอบ
    ให้คะแนน 0-100 และเหตุผลสั้น ๆ
    ตอบเป็น JSON เท่านั้น: {{"score": 75, "opinion": "...."}}
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือผู้เชี่ยวชาญด้านการเทรด Forex ด้วย Ichimoku และ Price Action"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=150
        )
        content = response['choices'][0]['message']['content']
        return json.loads(content)
    except Exception as e:
        print(f"ChatGPT error: {e}")
        return None

def main():
    now_thai = datetime.now(timezone.utc) + timedelta(hours=7)
    print(f"=== ระบบ Ichimoku เริ่มทำงาน === เวลาไทย: {now_thai.strftime('%d/%m/%Y %H:%M:%S')} น.")
    state = load_state()

    for pair in PAIRS:
        try:
            df = get_twelvedata(pair, interval="1h", outputsize=120)
        except Exception as e:
            print(f"❌ ดึงข้อมูล {pair} ล้มเหลว: {e}")
            continue

        signal = get_ichimoku_signal(df)
        if signal is None:
            print(f"{pair}: ไม่มีสัญญาณ")
            continue

        if state.get(pair) == signal:
            print(f"{pair}: สัญญาณ {signal} ซ้ำ ข้าม")
            continue

        tenkan, kijun, senkou_a, senkou_b, _ = calculate_ichimoku(df)
        current_price = df['Close'].iloc[-1]

        ai_extra = ""
        if os.environ.get('OPENAI_API_KEY'):
            ai_result = analyze_with_chatgpt(signal, pair, df)
            if ai_result:
                score = ai_result.get('score', 0)
                if score < 60:
                    print(f"{pair}: AI Score ต่ำ ({score}) - ไม่ส่งสัญญาณ")
                    continue
                ai_extra = f"\n🤖 AI Score: {score}\nความเห็น: {ai_result.get('opinion')}"

        msg = (f"📈 JPY Ichimoku Signal ({pair})\n"
               f"Direction: {signal}\n"
               f"ราคาปัจจุบัน: {current_price:.3f}\n"
               f"Tenkan: {tenkan.iloc[-1]:.3f} | Kijun: {kijun.iloc[-1]:.3f}\n"
               f"Kumo: {senkou_a.iloc[-1]:.3f} / {senkou_b.iloc[-1]:.3f}\n"
               f"Timeframe: H1\n"
               f"{ai_extra}\n"
               f"⚠️ เทรดด้วยตนเอง")

        status = send_line_message(msg)
        if status == 200:
            print(f"✅ ส่งสัญญาณ {pair} สำเร็จ")
            state[pair] = signal
            save_state(state)
        else:
            print(f"❌ ส่งสัญญาณ {pair} ไม่สำเร็จ")

if __name__ == "__main__":
    main()