"""Run with: python -m unittest discover -s tests -v; uses localhost ports 5000/5001."""
import json, os, pathlib, socket, subprocess, sys, tempfile, time, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class Integration(unittest.TestCase):
    def test_two_clients_match_and_udp(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=tmp,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            socks=[];streams=[]
            try:
                for attempt in range(50):
                    try:
                        c=socket.create_connection(('127.0.0.1',5000),timeout=.3);socks.append(c);break
                    except OSError:time.sleep(.1)
                else:self.fail('Server did not start')
                socks.append(socket.create_connection(('127.0.0.1',5000),timeout=2))
                for c in socks:c.settimeout(3);streams.append(c.makefile('r'))
                def send(i,packet):socks[i].sendall((json.dumps(packet)+'\n').encode())
                def wait(i,typ):
                    until=time.time()+3
                    while time.time()<until:
                        msg=json.loads(streams[i].readline())
                        if msg.get('type')==typ:return msg
                    self.fail(f'Missing {typ}')
                tokens=[]
                for i in range(2):
                    name=f'tester{i}'
                    send(i,{'type':'register','username':name,'password':'pass12345'});wait(i,'registered')
                    send(i,{'type':'login','username':name,'password':'pass12345'})
                    tokens.append(wait(i,'logged_in')['udp_token'])
                send(0,{'type':'create_room','room':'Arena1'});wait(0,'room_created')
                send(1,{'type':'join_room','room':'Arena1'});wait(1,'room_joined')
                for i in range(2):send(i,{'type':'ready','ready':True});wait(i,'ready_updated')
                send(0,{'type':'start_game'});started=wait(0,'game_started')
                self.assertEqual(started['match_id'],1)
                u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);u.settimeout(3)
                try:
                    u.sendto(json.dumps({'type':'udp_hello','token':tokens[0]}).encode(),('127.0.0.1',5001))
                    data,_=u.recvfrom(8192);self.assertEqual(json.loads(data)['type'],'udp_welcome')
                    u.sendto(json.dumps({'type':'player_input','match_id':1,'seq':1,'left':False,'right':True,'jump':False}).encode(),('127.0.0.1',5001))
                    until=time.time()+3
                    while time.time()<until:
                        data,_=u.recvfrom(8192);msg=json.loads(data)
                        if msg.get('type')=='game_state':
                            self.assertIn('tester0',msg['world']['players'])
                            self.assertGreater(len(msg['world']['coins']),0)
                            break
                    else:self.fail('No game state')
                finally:u.close()
            finally:
                for c in socks:c.close()
                proc.terminate()
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:proc.kill()
if __name__=='__main__':unittest.main()
