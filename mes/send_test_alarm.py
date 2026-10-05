import json
import paho.mqtt.client as mqtt

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect("localhost", 1883)
c.loop_start()
msg = {"station_id": "ST-040", "ts": "2026-10-04T10:26:40Z",
       "code": "TORQUE_LOW", "severity": "warning"}
for _ in range(2):                      # send the same alarm twice
    c.publish("plant/line1/ST-040/alarm", json.dumps(msg), qos=1).wait_for_publish()
c.loop_stop()