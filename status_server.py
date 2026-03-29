from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from devices.base import BaseBleDevice

logger = logging.getLogger("status")

_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BLE Monitor</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0f172a;
  color: #e2e8f0;
  font-family: ui-monospace, 'Cascadia Code', monospace;
  min-height: 100vh;
  padding: 24px 16px;
}
header { margin-bottom: 24px; }
header h1 { font-size: 1.2rem; color: #94a3b8; font-weight: 500; }
header .ts { font-size: 0.72rem; color: #475569; margin-top: 4px; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 20px;
  transition: opacity 0.3s;
}
.card.offline { opacity: 0.45; }
.card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.card-title { font-size: 1rem; font-weight: 600; }
.card-sub { font-size: 0.7rem; color: #64748b; margin-top: 3px; line-height: 1.5; }
.badge {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  border: 1px solid currentColor;
  white-space: nowrap;
  flex-shrink: 0;
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.rows { display: flex; flex-direction: column; gap: 9px; }
.row { display: flex; justify-content: space-between; align-items: center; font-size: 0.78rem; }
.lbl { color: #64748b; }
.val { color: #cbd5e1; }
.warn { color: #fb923c !important; }
.err  { color: #f87171 !important; }
</style>
</head>
<body>
<header>
  <h1>yokuli BLE Monitor</h1>
  <div class="ts" id="ts">正在连接...</div>
</header>
<div class="grid" id="grid"></div>
<script>
const COLORS = {
  running:      '#22c55e',
  connected:    '#60a5fa',
  connecting:   '#facc15',
  backoff:      '#fb923c',
  disconnected: '#6b7280',
  stopped:      '#ef4444',
};
const LABELS = {
  running:      '运行中',
  connected:    '已连接',
  connecting:   '连接中',
  backoff:      '等待重连',
  disconnected: '未连接',
  stopped:      '已停止',
};

function fmtAge(s) {
  if (s > 99999) return '尚无数据';
  if (s < 2)     return s.toFixed(1) + 's';
  if (s < 60)    return s.toFixed(0) + 's 前';
  return (s / 60).toFixed(1) + 'min 前';
}

function renderCard(d) {
  const color   = COLORS[d.state] || '#6b7280';
  const label   = LABELS[d.state] || d.state;
  const offline = d.state === 'stopped' || d.state === 'disconnected';
  const stale   = d.last_data_age > 30;
  const hasErr  = d.fail_count > 0;
  return '<div class="card' + (offline ? ' offline' : '') + '">'
    + '<div class="card-header">'
    +   '<div>'
    +     '<div class="card-title">' + d.name + '</div>'
    +     '<div class="card-sub">' + d.key + '<br>' + d.mac + '</div>'
    +   '</div>'
    +   '<div class="badge" style="color:' + color + '">'
    +     '<div class="dot"></div>' + label
    +   '</div>'
    + '</div>'
    + '<div class="rows">'
    +   '<div class="row"><span class="lbl">最后数据</span>'
    +     '<span class="val' + (stale ? ' warn' : '') + '">' + fmtAge(d.last_data_age) + '</span></div>'
    +   '<div class="row"><span class="lbl">连接失败</span>'
    +     '<span class="val' + (hasErr ? ' err' : '') + '">' + d.fail_count + ' 次</span></div>'
    +   '<div class="row"><span class="lbl">Signal K 客户端</span>'
    +     '<span class="val">' + d.signalk_clients + '</span></div>'
    +   '<div class="row"><span class="lbl">TCP 端口</span>'
    +     '<span class="val">' + d.tcp_port + '</span></div>'
    + '</div></div>';
}

async function poll() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    document.getElementById('grid').innerHTML = data.devices.map(renderCard).join('');
    document.getElementById('ts').textContent =
      '更新于 ' + new Date().toLocaleTimeString('zh-CN', {hour12: false});
  } catch (e) {
    document.getElementById('ts').textContent = '⚠ 无法连接 — ' + e.message;
  }
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>"""


class StatusServer:
    """
    Minimal async HTTP server serving a device-status dashboard.
    No extra dependencies — implemented with raw asyncio TCP.

    Endpoints:
      GET /            → HTML dashboard (auto-refreshes every 2s)
      GET /api/status  → JSON device status snapshot
    """

    def __init__(self, port: int, devices: "List[BaseBleDevice]") -> None:
        self.port = port
        self.devices = devices
        self._server: Optional[asyncio.AbstractServer] = None

    def _snapshot(self) -> dict:
        return {
            "devices": [
                {
                    "key": d.config.key,
                    "name": d.config.name,
                    "mac": d.config.mac,
                    "state": d.state.value,
                    "fail_count": d.fail_count,
                    "last_data_age": round(d.seconds_since_last_data(), 1),
                    "tcp_port": d.config.tcp_port,
                    "signalk_clients": d.signalk.client_count if d.signalk else 0,
                }
                for d in self.devices
            ]
        }

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, "0.0.0.0", self.port
        )
        logger.info(f"Status dashboard: http://localhost:{self.port}")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            parts = line.decode(errors="replace").split()
            if len(parts) < 2:
                return

            path = parts[1].split("?")[0]

            # Drain request headers.
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if h in (b"\r\n", b"\n", b""):
                    break

            if path == "/":
                body = _HTML.encode("utf-8")
                ct = "text/html; charset=utf-8"
            elif path == "/api/status":
                body = json.dumps(self._snapshot()).encode("utf-8")
                ct = "application/json"
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found")
                await asyncio.wait_for(writer.drain(), timeout=3.0)
                return

            writer.write(
                (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: {ct}\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Cache-Control: no-cache\r\n"
                    f"\r\n"
                ).encode("utf-8") + body
            )
            await asyncio.wait_for(writer.drain(), timeout=3.0)

        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
