import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

SESSION_FILE = "chat_sessions.json"

def load_sessions():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_sessions(sessions):
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

class CardWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
        except:
            self.send_response(400)
            self.end_headers()
            return

        # Handle Feishu URL verification challenge
        if "challenge" in data:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"challenge": data["challenge"]}).encode('utf-8'))
            return

        # Handle Card Action
        action = data.get("action", {})
        action_value = action.get("value", {})
        open_id = data.get("open_id") # User who clicked
        chat_id = data.get("open_chat_id") # Chat where it was clicked

        if action_value.get("action") == "switch_model":
            new_model = action_value.get("model")
            
            # Update session
            sessions = load_sessions()
            if chat_id not in sessions:
                sessions[chat_id] = {"model": new_model}
            else:
                sessions[chat_id]["model"] = new_model
            save_sessions(sessions)

            print(f"User {open_id} switched model to {new_model} in chat {chat_id}")

            # Return Toast response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                "toast": {
                    "type": "info",
                    "content": f"模型已成功切换为 {new_model}"
                }
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return

        # Default response
        self.send_response(200)
        self.end_headers()

def run_server(port=5000):
    server_address = ('', port)
    httpd = HTTPServer(server_address, CardWebhookHandler)
    print(f"Starting Card Webhook Server on port {port}...")
    print("Please use ngrok or similar tool to expose this port and configure the URL in Feishu Developer Console.")
    httpd.serve_forever()

if __name__ == '__main__':
    run_server()
