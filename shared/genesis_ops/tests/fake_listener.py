"""Test-only W1 stand-in: writes the w1_ingest heartbeat (instance 'test') every 5 s until killed."""
import sys
import time

from genesis_queue import connect

db = sys.argv[1]
conn = connect("w1_ingest", db)
while True:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO core.heartbeats (component, instance, last_beat_at, started_at, status, version)
                       VALUES ('w1_ingest', 'test', now(), now(), 'ok', 'fake')
                       ON CONFLICT (component, instance) DO UPDATE SET last_beat_at = now(), status = 'ok'""")
    time.sleep(5)
