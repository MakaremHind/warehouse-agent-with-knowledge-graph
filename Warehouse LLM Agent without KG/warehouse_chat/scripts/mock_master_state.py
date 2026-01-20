import paho.mqtt.client as mqtt
import json
import time

client = mqtt.Client()
client.connect("localhost", 1883)

print("[mock_master] Sending heartbeat...")

while True:
    msg = {"data": "online"}
    client.publish("master/state", json.dumps(msg))
    print("[mock_master] master/state → online")
    time.sleep(2)
