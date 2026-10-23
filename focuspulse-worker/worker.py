import os
import json
import time
import threading
import requests
from flask import Flask
from groq import Groq
from supabase import create_client

# Dummy web server for Render's free tier health check
app = Flask(__name__)

@app.route('/')
def health_check():
    return "CloudPulse Worker is live!", 200

# Initialize Cloud Clients
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

def process_queue():
    print("Worker listening for queue events...")
    while True:
        try:
            res = requests.get(
                f"{os.environ['UPSTASH_REDIS_REST_URL']}/lpop/focus_queue",
                headers={"Authorization": f"Bearer {os.environ['UPSTASH_REDIS_REST_TOKEN']}"}
            ).json()
            
            if not res.get("result"):
                time.sleep(2)
                continue

            item = json.loads(res["result"])
            raw_text = item["text"]
            chat_id = item["chat_id"]

            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                response_format={"type": "json_object"},
                messages=[{
                    "role": "system",
                    "content": """Analyze raw log. Return JSON strictly with keys:
                    category: one of ['DEEP_WORK', 'SHALLOW_WORK', 'LEISURE', 'PROCRASTINATION', 'PHYSICAL'],
                    focus_minutes: int,
                    distraction_minutes: int,
                    friction_flag: boolean,
                    friction_reason: string or null"""
                }, {"role": "user", "content": raw_text}]
            )
            
            parsed = json.loads(completion.choices[0].message.content)

            supabase.table("focus_logs").insert({
                "telegram_user_id": chat_id,
                "raw_message": raw_text,
                "category": parsed["category"],
                "focus_minutes": parsed.get("focus_minutes", 0),
                "distraction_minutes": parsed.get("distraction_minutes", 0),
                "friction_flag": parsed.get("friction_flag", False),
                "friction_reason": parsed.get("friction_reason")
            }).execute()

            ack_msg = (
                f"⚡ *Logged to CloudPulse*\n"
                f"• Category: `{parsed['category']}`\n"
                f"• Focus: `{parsed.get('focus_minutes', 0)} mins`\n"
                f"• Distraction: `{parsed.get('distraction_minutes', 0)} mins`"
            )

            requests.post(
                f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage", 
                json={"chat_id": chat_id, "text": ack_msg, "parse_mode": "Markdown"}
            )

        except Exception as e:
            print(f"Error in execution loop: {e}")
            time.sleep(2)

if __name__ == "__main__":
    # Start the worker loop in a background thread
    worker_thread = threading.Thread(target=process_queue, daemon=True)
    worker_thread.start()
    
    # Run Flask server on port provided by Render (defaults to 10000)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
