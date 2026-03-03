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

GRAPH_TOP    = 49
GRAPH_BOTTOM = 109
GRAPH_LEFT   = 0
GRAPH_RIGHT  = 295

def draw_trend_arrow(epd, trend, x, y, size=28):
    """Draw a graphical trend arrow in a (size x size) box at (x, y)."""
    mid = size // 2
    ah = size // 3

    def filled_right_head(rx, my):
        for dy in range(-ah, ah + 1):
            epd.hline(rx - ah, my + dy, ah - abs(dy) + 1, 0x00)

    def filled_up_head(mx, ty):
        for dx in range(-ah, ah + 1):
            epd.vline(mx + dx, ty + abs(dx), ah - abs(dx) + 1, 0x00)

    def filled_down_head(mx, by):
        for dx in range(-ah, ah + 1):
            epd.vline(mx + dx, by - ah, ah - abs(dx) + 1, 0x00)

    if trend == "flat":
        for d in (-1, 0, 1):
            epd.hline(x, y + mid + d, size - ah, 0x00)
        filled_right_head(x + size, y + mid)

    elif trend == "singleup":
        for d in (-1, 0, 1):
            epd.vline(x + mid + d, y + ah, size - ah, 0x00)
        filled_up_head(x + mid, y)

    elif trend == "singledown":
        for d in (-1, 0, 1):
            epd.vline(x + mid + d, y, size - ah, 0x00)
        filled_down_head(x + mid, y + size)

    elif trend == "fortyfiveup":
        for d in (-1, 0, 1):
            epd.line(x, y + size + d, x + size - ah // 2, y + ah // 2 + d, 0x00)
        for dy in range(ah + 1):
            epd.hline(x + size - ah + dy, y + dy, ah - dy + 1, 0x00)

    elif trend == "fortyfivedown":
        for d in (-1, 0, 1):
            epd.line(x, y + d, x + size - ah // 2, y + size - ah // 2 + d, 0x00)
        for dy in range(ah + 1):
            epd.hline(x + size - ah + dy, y + size - dy, ah - dy + 1, 0x00)

    elif trend == "doubleup":
        off = ah // 2
        for cx in (x + mid - off, x + mid + off):
            for d in (-1, 0, 1):
                epd.vline(cx + d, y + ah, size - ah, 0x00)
            filled_up_head(cx, y)

    elif trend == "doubledown":
        off = ah // 2
        for cx in (x + mid - off, x + mid + off):
            for d in (-1, 0, 1):
                epd.vline(cx + d, y, size - ah, 0x00)
            filled_down_head(cx, y + size)

history = []  # list of mmol float values

WIFI_STATUS = {
    0: "Idle", 1: "Connecting", 3: "Got IP",
    -1: "Wrong password", -2: "No AP found", -3: "Connect fail",
    201: "No AP found", 202: "Auth fail", 203: "Fail",
}

def connect_wifi(networks, epd):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected() and any(wlan.config('ssid') == s for s, _ in networks):
        return wlan
    wlan.disconnect()
    time.sleep(1)
    for idx, (ssid, password) in enumerate(networks):
        label = "WiFi " + str(idx + 1) + "/" + str(len(networks))
        epd.fill(0xff)
        epd.text(label, 2, 5, 0x00)
        epd.text(ssid, 2, 20, 0x00)
        epd.text("Connecting...", 2, 35, 0x00)
        epd.display(epd.buffer)
        print("Connecting to", ssid, "...")
        wlan.connect(ssid, password)
        for _ in range(20):
            if wlan.isconnected():
                break
            time.sleep(1)
        if wlan.isconnected():
            print("Connected:", wlan.ifconfig())
            return wlan
        status = wlan.status()
        msg = WIFI_STATUS.get(status, "Unknown") + " (" + str(status) + ")"
        print("Failed on", ssid, "- status:", msg)
        epd.fill(0xff)
        epd.text(label + " failed:", 2, 5, 0x00)
        epd.text(ssid, 2, 20, 0x00)
        epd.text(msg, 2, 35, 0x00)
        if idx + 1 < len(networks):
            epd.text("Trying next...", 2, 50, 0x00)
        epd.display(epd.buffer)
        time.sleep(2)
    raise RuntimeError("WiFi failed on all networks")

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
    trend = data.get('trend', '')

    epd.fill(0xff)

    # Large glucose value top-left
    scale = 5
    draw_large(epd, mmol, 2, 2, scale)

    # Trend arrow to the right
    draw_trend_arrow(epd, trend, 2 + len(mmol) * 8 * scale + 6, 2, size=36)

    # Separator line
    epd.hline(0, GRAPH_TOP - 3, 296, 0x00)

    # Graph
    draw_graph(epd, history)

    epd.display(epd.buffer)

# --- Main ---
env = load('.env')
networks = [(env['WIFI_SSID'], env['WIFI_PASSWORD'])]
if 'WIFI_SSID2' in env:
    networks.append((env['WIFI_SSID2'], env['WIFI_PASSWORD2']))

epd = EPD_2in9_Landscape()
epd.Clear(0xff)
connect_wifi(networks, epd)

while True:
    try:
        connect_wifi(networks, epd)
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
