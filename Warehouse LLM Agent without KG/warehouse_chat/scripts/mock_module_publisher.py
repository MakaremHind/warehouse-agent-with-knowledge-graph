import paho.mqtt.client as mqtt
import json
import time
import os

def get_snapshot_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "snapshot.json")

SNAPSHOT_PATH = get_snapshot_path()
print("[mock_modules] Loading snapshot from:", SNAPSHOT_PATH)

with open(SNAPSHOT_PATH, "r") as f:
    snap = json.load(f)

modules = snap["base_01/base_module_visualization"]

client = mqtt.Client()
client.connect("localhost", 1883)

print("[mock_modules] Ready. Publishing module visualization...")

while True:
    client.publish("base_01/base_module_visualization", json.dumps(modules))
    print("[mock_modules] published modules")
    time.sleep(1.5)
