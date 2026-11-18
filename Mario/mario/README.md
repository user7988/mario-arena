# Super Platform Arena — Member 1 starter

Python 3.10+; no third-party dependencies for this networking milestone.

## Start server
```bash
python server.py
```

## Start three test clients (separate terminals)
```bash
python test_client.py 127.0.0.1
```
For other computers on the same Wi-Fi, replace 127.0.0.1 with the server computer's LAN IP. Allow TCP port 5000 through the firewall.

## Commands to paste into the test clients
Register each username first, then log in:
```json
{"type":"register","username":"player1","password":"password123"}
{"type":"login","username":"player1","password":"password123"}
```
Use `player2` and `player3` on the other clients.

Player 1:
```json
{"type":"create_room","room":"Arena1"}
```
Players 2 and 3:
```json
{"type":"list_rooms"}
{"type":"join_room","room":"Arena1"}
```
All three:
```json
{"type":"ready","ready":true}
```
Player 1:
```json
{"type":"start_game"}
```

Also test `{"type":"leave_room"}` and closing a client window to simulate disconnection.

## Important limitations / integration work
- This is a **starter**, not the complete graded project. No UDP, physics, scores, or match termination yet.
- TCP uses newline-delimited JSON; clients must parse each line as one message. The server may send unsolicited `room_update` events.
- The UDP teammate must implement authenticated UDP session association (never trust a user-supplied player ID alone).
- The gameplay teammate should own game state and match end; integrate their engine with the TCP `start_game` transition.
- SQLite stores salted PBKDF2 password hashes; use TLS for real-world password transport. This LAN classroom prototype sends passwords over unencrypted TCP.
- Avoid concurrent writes to the same socket without a dedicated send lock/queue in the production integration.
- For robust production code, enforce byte-size limits before reading complete lines, add heartbeat timeouts, and avoid holding a global lock during potentially blocking socket sends.
