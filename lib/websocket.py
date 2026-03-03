import socket
import ssl
import ubinascii
import os
import struct


class WebSocket:
    def __init__(self, host, path='/', port=443, use_ssl=True):
        self.host = host
        self.path = path
        self.port = port
        self.use_ssl = use_ssl
        self.s = None

    def connect(self):
        addr = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)[0][-1]
        self.s = socket.socket()
        self.s.connect(addr)

        if self.use_ssl:
            self.s = ssl.wrap_socket(self.s, server_hostname=self.host)

        key = ubinascii.b2a_base64(os.urandom(16)).strip().decode()

        request = (
            'GET {} HTTP/1.1\r\n'
            'Host: {}\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            'Sec-WebSocket-Key: {}\r\n'
            'Sec-WebSocket-Version: 13\r\n'
            '\r\n'
        ).format(self.path, self.host, key)

        self.s.write(request.encode())

        # Read response headers
        response = b''
        while b'\r\n\r\n' not in response:
            response += self.s.read(1)

        if b'101' not in response:
            raise Exception('Handshake failed: ' + response.decode())

    def recv(self):
        """Block until a text frame arrives. Returns the decoded string."""
        while True:
            header = self._read_exactly(2)
            if not header or len(header) < 2:
                return None

            opcode = header[0] & 0x0f
            payload_len = header[1] & 0x7f

            if payload_len == 126:
                payload_len = struct.unpack('>H', self._read_exactly(2))[0]
            elif payload_len == 127:
                payload_len = struct.unpack('>Q', self._read_exactly(8))[0]

            data = self._read_exactly(payload_len)

            if opcode == 1:   # text frame
                return data.decode()
            elif opcode == 8: # close
                self.close()
                return None
            elif opcode == 9: # ping — reply with pong
                self.s.write(bytes([0x8a, len(data)]) + data)
            # opcode 0 (continuation) and 10 (pong) are ignored

    def _read_exactly(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.s.read(n - len(buf))
            if not chunk:
                return buf
            buf += chunk
        return buf

    def close(self):
        if self.s:
            self.s.close()
            self.s = None
