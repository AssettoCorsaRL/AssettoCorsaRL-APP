import socket, json
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(json.dumps({"reset": True}).encode("utf-8"), ("127.0.0.1", 9877))