import socket
import time
import json


def hexdump(b, width=16):
    for i in range(0, len(b), width):
        chunk = b[i : i + width]
        hexbytes = " ".join(f"{x:02x}" for x in chunk)
        ascii_part = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        print(f"{i:08x}  {hexbytes:<{width*3}}  {ascii_part}")


s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", 9876))

# set a short timeout so we can batch/aggregate packets and only print once a second
s.settimeout(0.1)

last_print = 0.0
last_pkt = None
pkt_count = 0
try:
    while True:
        try:
            data, addr = s.recvfrom(65536)
            last_pkt = (data, addr)
            pkt_count += 1
        except socket.timeout:
            pass

        now = time.time()
        if last_pkt and (now - last_print) >= 1.0:
            data, addr = last_pkt
            print(f"got {pkt_count} packets, last {len(data)} bytes from {addr}")

            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                print("binary data:")
                hexdump(data)
                last_print = now
                last_pkt = None
                pkt_count = 0
                continue

            try:
                obj = json.loads(text)["inputs"]
                print(json.dumps(obj, indent=2))
                last_print = now
                last_pkt = None
                pkt_count = 0
                continue
            except (ValueError, json.JSONDecodeError):
                pass

            # printable text?
            if all(32 <= ord(c) < 127 or c in "\r\n\t" for c in text):
                print(text)
            else:
                print("something wrong cuh")

            last_print = now
            last_pkt = None
            pkt_count = 0
finally:
    s.close()
