import socket
s=socket.create_connection(('127.0.0.1',50007),timeout=2)
s.sendall(b'GET\\n')
print(s.recv(65536).decode())
s.close()