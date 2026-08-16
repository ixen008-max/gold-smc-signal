import os, requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
USER_ID = os.environ['LINE_USER_ID']

def send_test_message():
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}"
    }
    payload = {
        "to": USER_ID,
        "messages": [
            {
                "type": "text",
                "text": "🧪 ทดสอบการแจ้งเตือนจากระบบของคุณ\nถ้าเห็นข้อความนี้ แสดงว่าทุกอย่างพร้อมทำงาน ✅"
            }
        ]
    }
    resp = requests.post(url, json=payload, headers=headers)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")

if __name__ == "__main__":
    send_test_message()