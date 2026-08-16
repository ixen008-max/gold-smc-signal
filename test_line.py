import os, requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
USER_ID = os.environ.get('LINE_USER_ID', '')

print(f"Token exists: {bool(TOKEN)}")
print(f"Token length: {len(TOKEN)}")
print(f"Token prefix: {TOKEN[:10]}...")
print(f"User ID exists: {bool(USER_ID)}")
print(f"User ID prefix: {USER_ID[:10]}...")

def send_test_message():
    if not TOKEN or not USER_ID:
        print("Missing TOKEN or USER_ID")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}"
    }
    payload = {
        "to": USER_ID,
        "messages": [{"type": "text", "text": "🧪 Test from GitHub Actions"}]
    }
    resp = requests.post(url, json=payload, headers=headers)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")

if __name__ == "__main__":
    send_test_message()