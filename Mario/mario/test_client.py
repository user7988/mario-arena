import socket, json, threading, sys

HOST = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
PORT = 5000
sock = socket.create_connection((HOST, PORT), timeout=10)
sock.settimeout(None)

def receive():
    try:
        with sock.makefile('r', encoding='utf-8') as stream:
            for line in stream:
                print('\nSERVER:', line.strip(), '\n> ', end='', flush=True)
    except OSError:
        pass
    print('\nConnection closed')

threading.Thread(target=receive, daemon=True).start()
print('Connected! Enter JSON commands. Example: {"type":"list_rooms"}')
try:
    while True:
        command = input('> ')
        if command.strip().lower() in ('quit', 'exit'):
            break
        try:
            obj = json.loads(command)
            sock.sendall((json.dumps(obj) + '\n').encode())
        except ValueError:
            print('Please enter valid JSON')
except (EOFError, KeyboardInterrupt, OSError):
    pass
finally:
    sock.close()
