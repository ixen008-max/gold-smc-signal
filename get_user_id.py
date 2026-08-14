from flask import Flask, request

app = Flask(__name__)

@app.route('/callback', methods=['POST'])
def callback():
    data = request.json
    print("Received event!")
    for event in data.get('events', []):
        if event.get('type') == 'message':
            user_id = event['source']['userId']
            print(f"\n🎯 YOUR USER ID: {user_id}\n")
            with open('user_id.txt', 'w') as f:
                f.write(user_id)
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)