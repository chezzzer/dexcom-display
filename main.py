import sys
import network
import time
import socket
import json
import framebuf
import gc

sys.path.append('/lib')

from epaper import EPD_2in9_Landscape
from env import load

DEXCOM_HOST  = 'dexcom.chezzer.dev'
POLL_INTERVAL = 300   # 5 minutes
MAX_HISTORY   = 148   # 2px per point across 296px display (~12hrs)

GRAPH_TOP    = 44
GRAPH_BOTTOM = 124
GRAPH_LEFT   = 0
GRAPH_RIGHT  = 295

TREND_ARROWS = {
    'flat':           '->',
    'rising':         '/^',
    'rising quickly': '^^',
    'falling':        'v/',
    'falling quickly':'vv',
}

history = []  # list of mmol float values

def connect_wifi(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected() and wlan.config('ssid') == ssid:
        return wlan
    wlan.disconnect()
    time.sleep(1)
    wlan.connect(ssid, password)
    print("Connecting to", ssid, "...")
    for _ in range(20):
        if wlan.isconnected():
            break
        time.sleep(1)
    if not wlan.isconnected():
        raise RuntimeError("WiFi failed, status: " + str(wlan.status()))
    print("Connected:", wlan.ifconfig())
    return wlan

def fetch_glucose(retries=5):
    for attempt in range(retries):
        s = None
        try:
            gc.collect()
            addr = socket.getaddrinfo(DEXCOM_HOST, 80, 0, socket.SOCK_STREAM)[0][-1]
            s = socket.socket()
            s.connect(addr)
            s.write(b'GET / HTTP/1.0\r\nHost: ' + DEXCOM_HOST.encode() + b'\r\nConnection: close\r\n\r\n')
            response = b''
            while True:
                chunk = s.read(1024)
                if not chunk:
                    break
                response += chunk
            body = response.split(b'\r\n\r\n', 1)[1]
            return json.loads(body)
        except Exception as e:
            print("Fetch attempt", attempt + 1, "failed:", e)
            if s:
                s.close()
            time.sleep(2)
    raise RuntimeError("All fetch attempts failed")

def draw_large(epd, text, x, y, scale):
    w = len(text) * 8
    buf = bytearray((w + 7) // 8 * 8)
    fb = framebuf.FrameBuffer(buf, w, 8, framebuf.MONO_HLSB)
    fb.fill(0xff)
    fb.text(text, 0, 0, 0x00)
    for cy in range(8):
        for cx in range(w):
            if fb.pixel(cx, cy) == 0:
                epd.fill_rect(x + cx * scale, y + cy * scale, scale, scale, 0x00)

def draw_graph(epd, history):
    if len(history) < 2:
        return

    lo = min(history)
    hi = max(history)
    if hi == lo:
        hi = lo + 1  # avoid divide by zero

    gh = GRAPH_BOTTOM - GRAPH_TOP
    gw = GRAPH_RIGHT - GRAPH_LEFT
    step = gw / (MAX_HISTORY - 1)

    def to_y(val):
        return GRAPH_BOTTOM - int((val - lo) / (hi - lo) * gh)

    # draw y-axis min/max labels (small text)
    epd.text(str(round(hi, 1)), 0, GRAPH_TOP, 0x00)
    epd.text(str(round(lo, 1)), 0, GRAPH_BOTTOM - 8, 0x00)

    # draw line segments
    offset = MAX_HISTORY - len(history)
    for i in range(1, len(history)):
        x1 = int(GRAPH_LEFT + (offset + i - 1) * step)
        y1 = to_y(history[i - 1])
        x2 = int(GRAPH_LEFT + (offset + i) * step)
        y2 = to_y(history[i])
        epd.line(x1, y1, x2, y2, 0x00)

def update_display(epd, data, history):
    mmol  = str(data['mmol'])
    trend = TREND_ARROWS.get(data.get('trend', ''), '?')

    epd.fill(0xff)

    # Large glucose value top-left
    scale = 4
    draw_large(epd, mmol, 2, 2, scale)

    # Trend arrow to the right
    draw_large(epd, trend, 2 + len(mmol) * 8 * scale + 6, 10, scale=2)

    # Separator line
    epd.hline(0, GRAPH_TOP - 3, 296, 0x00)

    # Graph
    draw_graph(epd, history)

    epd.display(epd.buffer)

# --- Main ---
env = load('.env')
connect_wifi(env['WIFI_SSID'], env['WIFI_PASSWORD'])

epd = EPD_2in9_Landscape()
epd.Clear(0xff)

while True:
    try:
        connect_wifi(env['WIFI_SSID'], env['WIFI_PASSWORD'])
        data = fetch_glucose()
        print("Glucose:", data)

        history.append(data['mmol'])
        if len(history) > MAX_HISTORY:
            history.pop(0)

        update_display(epd, data, history)
    except Exception as e:
        print("Error:", e)
        epd.fill(0xff)
        epd.text("Error:", 10, 20, 0x00)
        epd.text(str(e)[:35], 10, 35, 0x00)
        epd.display(epd.buffer)

    time.sleep(POLL_INTERVAL)
