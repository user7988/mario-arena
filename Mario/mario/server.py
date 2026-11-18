import socket
import threading
import json
import sqlite3
import hashlib
import secrets
import hmac
import time
import queue
import math
import game_engine

HOST = '0.0.0.0'
PORT = 5000
UDP_PORT = 5001
UDP_TICK_RATE = 20
DB = 'accounts.db'
MATCH_SECONDS = 90  # Set to 10 while testing
COUNTDOWN_SECONDS = 3.5  # Server-controlled pre-match delay
MAX_PLAYERS = 4
MIN_COMPETITIVE_PLAYERS = 2
MAX_MESSAGE_BYTES = 8192

lock = threading.RLock()
clients = {}  # socket -> {username, room, outbox}
rooms = {}    # name -> room state
udp_sessions = {}  # token -> TCP connection; validated under lock
udp_peers = {}     # TCP connection -> UDP address
udp_inputs = {}    # (room, username) -> latest validated input


def init_db():
    with sqlite3.connect(DB) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )''')


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), bytes.fromhex(salt), 200000
    ).hex()


def send(conn, payload):
    """Queue a message; never block the shared state lock on socket I/O."""
    state = clients.get(conn)
    if state is None:
        return
    try:
        state['outbox'].put_nowait(payload)
    except queue.Full:
        # A slow client cannot indefinitely stall other players.
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def sender_worker(conn, outbox):
    try:
        while True:
            payload = outbox.get()
            if payload is None:
                break
            data = (json.dumps(payload, separators=(',', ':')) + '\n').encode('utf-8')
            conn.sendall(data)
    except (OSError, TypeError, ValueError):
        pass
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def broadcast_room(name, payload):
    """Call while holding lock."""
    for conn, state in list(clients.items()):
        if state['room'] == name:
            send(conn, payload)


def room_view(name, room):
    remaining = None
    if room['status'] == 'playing':
        remaining = max(0, int(MATCH_SECONDS - (time.monotonic() - room['started_at'])))
    return {
        'name': name,
        'owner': room['owner'],
        'players': list(room['players']),
        'ready': sorted(room['ready']),
        'characters': dict(room['characters']),
        'status': room['status'],
        'capacity': room['capacity'],
        'scores': dict(room['scores']),
        'match_id': room['match_id'],
        'remaining_seconds': remaining,
    }


def notify_room(name):
    if name in rooms:
        broadcast_room(name, {'type': 'room_update', 'room': room_view(name, rooms[name])})


def finish_match(name, match_id, match_token, reason='timer_expired'):
    """Trusted server-only function. Call with lock held."""
    room = rooms.get(name)
    if (not room or room['status'] != 'playing'
            or room['match_id'] != match_id or room['match_token'] != match_token):
        return False

    scores = dict(room['scores'])
    ranking = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    highest = ranking[0][1] if ranking else None
    winners = [username for username, score in ranking if score == highest]
    results = {
        'type': 'game_ended',
        'room': name,
        'match_id': match_id,
        'ranking': [
            {'username': username, 'score': score,
             'rank': 1 + sum(other > score for other in scores.values()),
             'connected': username in room['players']}
            for username, score in ranking
        ],
        'winners': winners,
        'winner': winners[0] if len(winners) == 1 else None,
        'reason': reason,
    }
    broadcast_room(name, results)
    room['status'] = 'waiting'
    room['world'] = None
    room['ready'].clear()
    room['started_at'] = None
    room['match_token'] = None
    for username in list(scores):
        udp_inputs.pop((name, username), None)
    notify_room(name)
    return True


def timer_worker(name, match_id, match_token):
    while True:
        with lock:
            room = rooms.get(name)
            if (not room or room['status'] != 'playing'
                    or room['match_id'] != match_id or room['match_token'] != match_token):
                return
            remaining = MATCH_SECONDS - (time.monotonic() - room['started_at'])
            if remaining <= 0:
                finish_match(name, match_id, match_token)
                return
        time.sleep(min(0.5, remaining))


def record_score(room_name, username, points=1):
    """Only Member 3's trusted SERVER game engine may call this."""
    if type(points) is not int or points <= 0:
        return False
    with lock:
        room = rooms.get(room_name)
        if (not room or room['status'] != 'playing'
                or username not in room['players'] or username not in room['scores']):
            return False
        room['scores'][username] += points
        broadcast_room(room_name, {
            'type': 'score_update', 'room': room_name,
            'scores': dict(room['scores']), 'match_id': room['match_id']
        })
        return True


def leave_room(conn):
    """Call with lock held. Matches continue even if only one player remains."""
    state = clients.get(conn)
    if state is None:
        return
    name, username = state['room'], state['username']
    state['room'] = None
    room = rooms.get(name) if name else None
    if not room:
        return
    if username in room['players']:
        room['players'].remove(username)
    room['ready'].discard(username)
    udp_inputs.pop((name, username), None)
    if room['status'] == 'playing' and room.get('world'):
        room['world']['players'].pop(username, None)
    if not room['players']:
        del rooms[name]
        return
    if room['owner'] == username:
        room['owner'] = room['players'][0]
    # Keep the match running and preserve the departed player's score.
    # Member 3 should remove the character after receiving this update.
    notify_room(name)


def handle(conn, msg):
    action = msg.get('type')
    if not isinstance(action, str):
        return {'type': 'error', 'message': 'Missing message type'}
    state = clients[conn]

    if action in ('register', 'login'):
        if state['username']:
            return {'type': 'error', 'message': 'Already logged in'}
        username, password = msg.get('username'), msg.get('password')
        if not isinstance(username, str) or not (3 <= len(username) <= 20) or not username.isalnum():
            return {'type': 'error', 'message': 'Username must be 3-20 letters/numbers'}
        if not isinstance(password, str) or not (6 <= len(password) <= 128):
            return {'type': 'error', 'message': 'Password must be 6-128 characters'}
        with sqlite3.connect(DB, timeout=10) as db:
            if action == 'register':
                salt = secrets.token_hex(16)
                try:
                    db.execute('INSERT INTO users VALUES (?, ?, ?)',
                               (username, salt, password_hash(password, salt)))
                except sqlite3.IntegrityError:
                    return {'type': 'error', 'message': 'Username already exists'}
                return {'type': 'registered', 'username': username}
            record = db.execute(
                'SELECT salt, password_hash FROM users WHERE username=?', (username,)
            ).fetchone()
        if not record or not hmac.compare_digest(password_hash(password, record[0]), record[1]):
            return {'type': 'error', 'message': 'Incorrect username or password'}
        if any(s['username'] == username for s in clients.values()):
            return {'type': 'error', 'message': 'Account already connected'}
        state['username'] = username
        token = secrets.token_urlsafe(32)
        state['udp_token'] = token
        udp_sessions[token] = conn
        return {'type': 'logged_in', 'username': username, 'udp_token': token, 'udp_port': UDP_PORT}

    if not state['username']:
        return {'type': 'error', 'message': 'Log in first'}

    if action == 'list_rooms':
        return {'type': 'rooms', 'rooms': [room_view(n, r) for n, r in rooms.items()]}

    if action == 'create_room':
        name = msg.get('room')
        capacity = msg.get('capacity', MAX_PLAYERS)
        if not isinstance(name, str) or not (1 <= len(name) <= 30) or not name.strip():
            return {'type': 'error', 'message': 'Room name must be 1-30 characters'}
        if type(capacity) is not int or not (2 <= capacity <= MAX_PLAYERS):
            return {'type': 'error', 'message': 'Capacity must be 2-4'}
        if state['room']:
            return {'type': 'error', 'message': 'Leave your current room first'}
        if name in rooms:
            return {'type': 'error', 'message': 'Room already exists'}
        rooms[name] = {
            'owner': state['username'], 'players': [state['username']],
            'ready': set(), 'status': 'waiting', 'capacity': capacity,
            'scores': {}, 'characters': {state['username']: 'Mario'}, 'match_id': 0, 'match_token': None, 'started_at': None, 'world': None
        }
        state['room'] = name
        notify_room(name)
        return {'type': 'room_created', 'room': room_view(name, rooms[name])}

    if action == 'join_room':
        name = msg.get('room')
        if state['room']:
            return {'type': 'error', 'message': 'Leave your current room first'}
        room = rooms.get(name) if isinstance(name, str) else None
        if not room:
            return {'type': 'error', 'message': 'Room not found'}
        if room['status'] != 'waiting' or len(room['players']) >= room['capacity']:
            return {'type': 'error', 'message': 'Room unavailable or full'}
        room['players'].append(state['username'])
        room['characters'][state['username']] = ['Mario','Luigi','Peach','Toad'][((len(room['players'])-1)%4)]
        state['room'] = name
        notify_room(name)
        return {'type': 'room_joined', 'room': room_view(name, room)}

    if action == 'select_character':
        name = state['room']
        room = rooms.get(name) if name else None
        choice = msg.get('character')
        if not room or room['status'] != 'waiting':
            return {'type': 'error', 'message': 'Select a character while waiting in a room'}
        if choice not in ('Mario', 'Luigi', 'Peach', 'Toad'):
            return {'type': 'error', 'message': 'Unknown character'}
        room['characters'][state['username']] = choice
        notify_room(name)
        return {'type': 'character_selected', 'character': choice}

    if action == 'leave_room':
        if not state['room']:
            return {'type': 'error', 'message': 'Not in a room'}
        leave_room(conn)
        return {'type': 'room_left'}

    if action == 'ready':
        name = state['room']
        room = rooms.get(name) if name else None
        if not room:
            return {'type': 'error', 'message': 'Join a room first'}
        if room['status'] != 'waiting':
            return {'type': 'error', 'message': 'Match already started'}
        if msg.get('ready') is True:
            room['ready'].add(state['username'])
        elif msg.get('ready') is False:
            room['ready'].discard(state['username'])
        else:
            return {'type': 'error', 'message': 'ready must be true or false'}
        notify_room(name)
        return {'type': 'ready_updated', 'ready': state['username'] in room['ready']}

    if action == 'start_game':
        name = state['room']
        room = rooms.get(name) if name else None
        if not room:
            return {'type': 'error', 'message': 'Join a room first'}
        if room['owner'] != state['username']:
            return {'type': 'error', 'message': 'Only the room owner can start'}
        if room['status'] != 'waiting':
            return {'type': 'error', 'message': 'Match already started'}
        if len(room['players']) < MIN_COMPETITIVE_PLAYERS:
            return {'type': 'error', 'message': 'Need at least 2 players to compete'}
        if len(room['ready']) != len(room['players']):
            return {'type': 'error', 'message': 'All players must be ready'}
        room['status'] = 'playing'
        room['match_id'] += 1
        room['match_token'] = secrets.token_hex(16)
        room['started_at'] = time.monotonic() + COUNTDOWN_SECONDS
        room['scores'] = {username: 0 for username in room['players']}
        room['world'] = game_engine.create_world(room['players'])
        for username in room['players']:
            udp_inputs.pop((name, username), None)
        match_id, token = room['match_id'], room['match_token']
        threading.Thread(target=timer_worker, args=(name, match_id, token), daemon=True).start()
        notify_room(name)
        broadcast_room(name, {
            'type': 'game_started', 'room': name, 'match_id': match_id,
            'duration_seconds': MATCH_SECONDS, 'countdown_seconds': COUNTDOWN_SECONDS, 'scores': dict(room['scores'])
        })
        return {'type': 'start_accepted', 'room': name, 'match_id': match_id}

    if action == 'ping':
        return {'type': 'pong'}
    return {'type': 'error', 'message': 'Unknown command'}


def client_worker(conn, addr):
    print('Connected:', addr)
    outbox = queue.Queue(maxsize=256)
    with lock:
        clients[conn] = {'username': None, 'room': None, 'outbox': outbox, 'udp_token': None}
    threading.Thread(target=sender_worker, args=(conn, outbox), daemon=True).start()
    try:
        # Use byte-oriented framing to enforce a real 8 KB line limit.
        buffer = bytearray()
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            while b'\n' in buffer:
                raw, _, remainder = buffer.partition(b'\n')
                buffer = bytearray(remainder)
                if len(raw) > MAX_MESSAGE_BYTES:
                    with lock:
                        send(conn, {'type': 'error', 'message': 'Message too large'})
                    return
                try:
                    msg = json.loads(raw.decode('utf-8'))
                    if not isinstance(msg, dict):
                        raise ValueError('Expected JSON object')
                except (UnicodeError, ValueError):
                    with lock:
                        send(conn, {'type': 'error', 'message': 'Invalid JSON'})
                    continue
                with lock:
                    response = handle(conn, msg)
                    send(conn, response)
            if len(buffer) > MAX_MESSAGE_BYTES:
                with lock:
                    send(conn, {'type': 'error', 'message': 'Message too large'})
                break
    except (ConnectionError, OSError):
        pass
    finally:
        with lock:
            if conn in clients:
                leave_room(conn)
                token = clients[conn].get('udp_token')
                if token:
                    udp_sessions.pop(token, None)
                udp_peers.pop(conn, None)
                del clients[conn]
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        try:
            outbox.put_nowait(None)
        except queue.Full:
            pass
        print('Disconnected:', addr)


def udp_send(sock, address, payload):
    try:
        sock.sendto(json.dumps(payload, separators=(',', ':')).encode('utf-8'), address)
    except (OSError, TypeError, ValueError):
        pass


def udp_worker(sock):
    """Receive validated UDP controls. Never accept client scores or positions."""
    while True:
        try:
            raw, address = sock.recvfrom(2048)
            if len(raw) > 1200:
                continue
            msg = json.loads(raw.decode('utf-8'))
            if not isinstance(msg, dict):
                continue
        except (OSError, UnicodeError, ValueError):
            continue
        action = msg.get('type')
        if action == 'udp_hello':
            token = msg.get('token')
            if not isinstance(token, str):
                continue
            with lock:
                conn = udp_sessions.get(token)
                state = clients.get(conn) if conn else None
                if not state or not state['username']:
                    continue
                udp_peers[conn] = address
                username = state['username']
            udp_send(sock, address, {'type': 'udp_welcome', 'username': username})
            continue
        if action != 'player_input':
            continue
        with lock:
            # UDP address must first be bound to a valid TCP login token.
            conn = next((c for c, peer in udp_peers.items() if peer == address), None)
            state = clients.get(conn) if conn else None
            if not state or not state['room']:
                continue
            name, username = state['room'], state['username']
            room = rooms.get(name)
            if not room or room['status'] != 'playing' or username not in room['players']:
                continue
            seq, match_id = msg.get('seq'), msg.get('match_id')
            if type(seq) is not int or not (0 <= seq <= 2**53) or match_id != room['match_id']:
                continue
            left, right, jump = msg.get('left'), msg.get('right'), msg.get('jump')
            if any(type(v) is not bool for v in (left, right, jump)):
                continue
            key = (name, username)
            previous = udp_inputs.get(key)
            if previous and seq <= previous['seq']:
                continue
            udp_inputs[key] = {'seq': seq, 'left': left, 'right': right,
                               'jump': jump, 'updated_at': time.monotonic()}


def udp_broadcast_worker(sock):
    """20 Hz authoritative simulation and UDP game-state broadcast."""
    tick = 1 / UDP_TICK_RATE
    last = time.monotonic()
    while True:
        time.sleep(tick)
        now = time.monotonic()
        dt = min(now - last, 0.05)
        last = now
        deliveries = []
        with lock:
            for name, room in list(rooms.items()):
                if room['status'] != 'playing' or not room.get('world'):
                    continue
                controls = {}
                for username in room['players']:
                    data = udp_inputs.get((name, username))
                    if data and now - data['updated_at'] < 0.3:
                        controls[username] = {k: data[k] for k in ('left', 'right', 'jump')}
                    else:
                        controls[username] = {'left': False, 'right': False, 'jump': False}
                # No simulation, collisions, pickups or scoring until GO.
                if now >= room['started_at']:
                    game_engine.step(room['world'], controls, dt,
                                     lambda username, points: record_score(name, username, points))
                packet = {'type': 'game_state', 'room': name, 'match_id': room['match_id'],
                          'world': game_engine.snapshot(room['world']), 'scores': dict(room['scores']),
                          'remaining_seconds': max(0, int(MATCH_SECONDS - max(0, now - room['started_at']))),
                           'countdown_remaining': max(0.0, round(room['started_at'] - now, 2))}
                for conn, state in clients.items():
                    if state['room'] == name and conn in udp_peers:
                        deliveries.append((udp_peers[conn], packet))
        for address, packet in deliveries:
            udp_send(sock, address, packet)


def main():
    init_db()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        udp_sock.bind((HOST, UDP_PORT))
        server.listen()
        threading.Thread(target=udp_worker, args=(udp_sock,), daemon=True).start()
        threading.Thread(target=udp_broadcast_worker, args=(udp_sock,), daemon=True).start()
        print(f'TCP server listening on {HOST}:{PORT}')
        print(f'UDP server listening on {HOST}:{UDP_PORT}')
        while True:
            conn, addr = server.accept()
            threading.Thread(target=client_worker, args=(conn, addr), daemon=True).start()


if __name__ == '__main__':
    main()
