"""UDP test client. Keep TCP test_client.py open and logged in first."""
import json
import socket
import threading
import time

HOST = '127.0.0.1'  # Change to server computer's LAN IP for four-computer demo.
PORT = 5001

token = input('Paste udp_token from your TCP login response: ').strip()
match_id = int(input('Enter match_id from game_started: ').strip())
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2)
sock.sendto(json.dumps({'type': 'udp_hello', 'token': token}).encode(), (HOST, PORT))
try:
    raw, _ = sock.recvfrom(4096)
    reply = json.loads(raw)
    print('SERVER:', reply)
    if reply.get('type') != 'udp_welcome':
        raise SystemExit('UDP authentication failed')
except socket.timeout:
    raise SystemExit('No UDP welcome. Check token, UDP port, and firewall.')

stop = threading.Event()

def listen():
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            raw, _ = sock.recvfrom(4096)
            packet = json.loads(raw)
            if packet.get('type') == 'input_snapshot':
                print('SNAPSHOT:', packet)
        except socket.timeout:
            continue
        except (OSError, ValueError):
            break

threading.Thread(target=listen, daemon=True).start()
print('Enter a=left, d=right, w=jump, s=stop, q=quit. Inputs are sent only when you type.')
seq = 0
try:
    while True:
        cmd = input('UDP> ').strip().lower()
        if cmd == 'q':
            break
        if cmd not in ('a', 'd', 'w', 's'):
            print('Use a/d/w/s/q')
            continue
        seq += 1
        packet = {'type': 'player_input', 'match_id': match_id, 'seq': seq,
                  'left': cmd == 'a', 'right': cmd == 'd', 'jump': cmd == 'w'}
        sock.sendto(json.dumps(packet).encode(), (HOST, PORT))
finally:
    stop.set()
    sock.close()
