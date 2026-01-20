import paho.mqtt.client as mqtt
import json
import time
import os

def get_snapshot_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "snapshot.json")

SNAPSHOT_PATH = get_snapshot_path()
print("[mock_camera] Loading snapshot from:", SNAPSHOT_PATH)

with open(SNAPSHOT_PATH, "r") as f:
    snap = json.load(f)

markers = snap["mmh_cam/detected_markers"]
boxes = snap["mmh_cam/detected_boxes"]

client = mqtt.Client()
client.connect("localhost", 1883)

print("[mock_camera] Ready. Publishing camera data...")

while True:
    client.publish("mmh_cam/detected_markers", json.dumps(markers))
    client.publish("mmh_cam/detected_boxes", json.dumps(boxes))
    print("[mock_camera] published markers + boxes")
    time.sleep(1)
