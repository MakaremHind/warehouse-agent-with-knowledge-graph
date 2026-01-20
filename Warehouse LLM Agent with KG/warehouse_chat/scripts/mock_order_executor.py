import paho.mqtt.client as mqtt
import json
import time
import threading

BROKER = "localhost"
PORT = 1883

client = mqtt.Client()
client.connect(BROKER, PORT)

def on_message(c, u, msg):
    order = json.loads(msg.payload.decode())
    cid = order["header"]["correlation_id"]

    print(f"[mock_order_executor] RECEIVED ORDER {cid}")
    print(f"[mock_order_executor] Simulating execution...")

    # async execution
    def execute():
        time.sleep(3)  # simulate delay

        response = {
            "header": {
                "correlation_id": cid,
                "timestamp": time.time(),
                "version": 1.0
            },
            "success": True,
            "message": "Mock order finished successfully"
        }

        topic = f"base_01/order_request/response/{cid}"
        c.publish(topic, json.dumps(response))
        print(f"[mock_order_executor] PUBLISHED RESULT for {cid}")

    threading.Thread(target=execute).start()

client.subscribe("base_01/order_request")
client.on_message = on_message

print("[mock_order_executor] Ready. Listening for orders...")
client.loop_forever()
