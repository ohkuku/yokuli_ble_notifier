from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Deque, List, Optional, Tuple

if TYPE_CHECKING:
    from adapter_restart import AdapterRestartCoordinator
    from devices.base import BaseBleDevice

from config_loader import BluetoothConfig

logger = logging.getLogger("status")


class _RingLogHandler(logging.Handler):
    """In-memory ring buffer handler for dashboard log panes."""

    def __init__(self, normal_buffer: Deque[str], raw_buffer: Deque[str]) -> None:
        super().__init__(level=logging.INFO)
        self.normal_buffer = normal_buffer
        self.raw_buffer = raw_buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = time.strftime("%H:%M:%S", time.localtime(record.created))
            msg = record.getMessage()
            line = f"{ts} [{record.name}] {msg}"
            self.normal_buffer.append(line)
            if "[DEBUG:" in msg:
                self.raw_buffer.append(line)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Embedded dashboard HTML
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BLE Monitor</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0f172a; color: #e2e8f0;
  font-family: ui-monospace, 'Cascadia Code', monospace;
  min-height: 100vh; padding: 20px 16px;
}
header { margin-bottom: 20px; }
header h1 { font-size: 1.15rem; color: #94a3b8; font-weight: 500; }
header .ts { font-size: 0.7rem; color: #475569; margin-top: 4px; }
section-title {
  display: block; font-size: 0.65rem; text-transform: uppercase;
  letter-spacing: 0.08em; color: #475569; margin-bottom: 10px;
}
/* ── Device grid ── */
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px; margin-bottom: 18px;
}
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; }
}
.card {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 12px; padding: 18px; transition: opacity 0.3s;
}
.card.offline { opacity: 0.45; }
.card-main {
  display: flex;
  gap: 14px;
  align-items: stretch;
}
.card-media {
  width: 140px;
  min-width: 140px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.card-header {
  display: flex; align-items: flex-start;
  justify-content: space-between; gap: 10px; margin-bottom: 14px;
}
.card-info {
  flex: 1;
  min-width: 0;
}
.card-title { font-size: 0.95rem; font-weight: 600; }
.card-sub { font-size: 0.68rem; color: #64748b; margin-top: 3px; line-height: 1.5; }
.thumb {
  width: 132px;
  height: 132px;
  object-fit: cover;
  border: 1px solid #334155;
  border-radius: 10px;
  background: #0b1220;
}
@media (max-width: 600px) {
  .card-main { flex-direction: column; }
  .card-media {
    width: 100%;
    min-width: 0;
  }
  .thumb {
    width: 100%;
    max-width: 220px;
    height: 120px;
    object-fit: contain;
  }
}
.badge {
  display: flex; align-items: center; gap: 5px;
  padding: 4px 9px; border-radius: 999px;
  font-size: 0.65rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.05em;
  border: 1px solid currentColor; white-space: nowrap; flex-shrink: 0;
}
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
.row { display: flex; justify-content: space-between; align-items: center; font-size: 0.76rem; }
.lbl { color: #64748b; }
.val { color: #cbd5e1; }
.warn { color: #fb923c !important; }
.err  { color: #f87171 !important; }
/* ── Buttons ── */
.btn {
  border: none; border-radius: 7px; padding: 7px 14px;
  font-size: 0.72rem; font-weight: 600; cursor: pointer;
  font-family: inherit; transition: opacity 0.15s;
  letter-spacing: 0.03em;
}
.btn:hover { opacity: 0.82; }
.btn:active { opacity: 0.65; }
.btn:disabled { opacity: 0.35; cursor: not-allowed; }
.btn-blue   { background: #1d4ed8; color: #fff; }
.btn-orange { background: #c2410c; color: #fff; }
.btn-red    { background: #991b1b; color: #fff; }
.btn-gray   { background: #334155; color: #94a3b8; }
.card-btns  { display: flex; gap: 8px; flex-wrap: wrap; }
/* ── Bluetooth panel ── */
.bt-panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 12px; padding: 16px; margin-bottom: 14px;
}
.bt-panel-title {
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #475569; margin-bottom: 12px;
}
.bt-rows { display: flex; flex-direction: column; gap: 7px; margin-bottom: 14px; }
.bt-row { display: flex; justify-content: space-between; align-items: center; font-size: 0.76rem; }
/* ── Global controls ── */
.controls {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 12px; padding: 16px; margin-bottom: 14px;
}
.controls-title {
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #475569; margin-bottom: 12px;
}
.ctrl-btns { display: flex; gap: 10px; flex-wrap: wrap; }
/* ── Log panels ── */
.logs-panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 12px; padding: 16px; margin-bottom: 14px;
}
.logs-title {
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #475569;
}
.logs-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; margin-bottom: 10px; flex-wrap: wrap;
}
.switch {
  font-size: 0.72rem; color: #cbd5e1;
  display: inline-flex; align-items: center; gap: 6px;
}
.logbox {
  background: #0b1220;
  border: 1px solid #334155;
  border-radius: 8px;
  height: 180px;
  overflow: auto;
  padding: 8px;
  font-size: 0.7rem;
  line-height: 1.35;
  color: #cbd5e1;
  white-space: pre-wrap;
}
.logbox.raw { border-color: #1d4ed8; }
/* ── Toast message ── */
#toast {
  position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
  background: #1e293b; border: 1px solid #475569; border-radius: 8px;
  padding: 10px 18px; font-size: 0.78rem; color: #e2e8f0;
  opacity: 0; pointer-events: none; transition: opacity 0.3s;
  max-width: 90vw; text-align: center; z-index: 100;
}
#toast.show { opacity: 1; }
</style>
</head>
<body>
<header>
  <h1>yokuli BLE Monitor</h1>
  <div class="ts" id="ts">正在连接...</div>
</header>

<div class="bt-panel" id="bt-panel">
  <div class="bt-panel-title">蓝牙适配器</div>
  <div class="bt-rows" id="bt-rows"></div>
  <div class="card-btns">
    <button class="btn btn-orange" id="btn-restart-bt" onclick="doRestartBluetooth()">
      重启蓝牙适配器
    </button>
  </div>
</div>

<div class="controls">
  <div class="controls-title">进程控制</div>
  <div class="ctrl-btns">
    <button class="btn btn-blue"   onclick="doUpdateFromGit()">拉取 GitHub 更新并重启</button>
    <button class="btn btn-orange" onclick="doRestartService()">重启进程</button>
    <button class="btn btn-red"    onclick="doRebootPi()">重启树莓派</button>
  </div>
</div>

<div class="grid" id="grid"></div>

<div class="logs-panel">
  <div class="logs-toolbar">
    <div class="logs-title">运行日志</div>
    <label class="switch">
      <input type="checkbox" id="toggle-raw" onchange="toggleRawPane()">
      显示原始报文（DEBUG）
    </label>
  </div>
  <div id="normal-log" class="logbox">暂无日志</div>
  <div id="raw-wrap" style="display:none; margin-top:10px;">
    <div class="logs-title" style="margin-bottom:8px;">原始报文</div>
    <div id="raw-log" class="logbox raw">暂无原始报文</div>
  </div>
</div>
<div id="toast"></div>

<script>
const COLORS = {
  running:'#22c55e', connected:'#60a5fa', connecting:'#facc15',
  backoff:'#fb923c', disconnected:'#6b7280', stopped:'#ef4444',
};
const STATE_LABELS = {
  running:'运行中', connected:'已连接', connecting:'连接中',
  backoff:'等待重连', disconnected:'未连接', stopped:'已停止',
};
// 设备缩略图（可替换为 base64 data URI；当前先使用外链）
const DEVICE_THUMBS = {
  coulometer: 'https://s.alicdn.com/@sc04/kf/H69a904cba993490b974325272698f6eax.jpg?avif=close&webp=close',
  mppt: 'https://www.burnsco.co.nz/media/catalog/product/4/0/40971_1649161044.jpeg?optimize=high&fit=bounds&height=840&width=840&canvas=840:840',
};

let _actionLock = false;
let _lastNormalLogs = '';
let _lastRawLogs = '';

function toast(msg, ms=3000) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}

function fmtAge(s) {
  if (s > 99999) return '尚无数据';
  if (s < 2)     return s.toFixed(1) + 's';
  if (s < 60)    return s.toFixed(0) + 's 前';
  return (s / 60).toFixed(1) + 'min 前';
}

function fmtSec(s) {
  if (s <= 0)  return '现在可用';
  if (s < 60)  return s.toFixed(0) + 's 后可用';
  return (s / 60).toFixed(1) + 'min 后可用';
}

function toggleRawPane() {
  const checked = document.getElementById('toggle-raw').checked;
  document.getElementById('raw-wrap').style.display = checked ? 'block' : 'none';
}

function setLogLines(elId, lines, key) {
  const el = document.getElementById(elId);
  const text = lines && lines.length ? lines.join('\n') : (key === 'raw' ? '暂无原始报文' : '暂无日志');
  const old = key === 'raw' ? _lastRawLogs : _lastNormalLogs;
  if (old === text) return;

  const nearBottom = (el.scrollTop + el.clientHeight + 8) >= el.scrollHeight;
  el.textContent = text;
  if (key === 'raw') _lastRawLogs = text;
  else _lastNormalLogs = text;
  if (nearBottom) el.scrollTop = el.scrollHeight;
}

function renderBluetooth(bt) {
  const rows = [
    ['自动重启', bt.auto_restart_enabled
      ? '<span style="color:#22c55e">启用</span>'
      : '<span style="color:#6b7280">禁用</span>'],
    ['冷却时间', bt.cooldown_seconds + 's'],
    ['上次重启', bt.last_restart_ago == null ? '从未' : fmtAge(bt.last_restart_ago)],
    ['下次可用', fmtSec(bt.cooldown_remaining)],
  ];
  document.getElementById('bt-rows').innerHTML = rows.map(([l, v]) =>
    `<div class="bt-row"><span class="lbl">${l}</span><span class="val">${v}</span></div>`
  ).join('');
}

function renderCard(d) {
  const color   = COLORS[d.state] || '#6b7280';
  const label   = STATE_LABELS[d.state] || d.state;
  const offline = d.state === 'stopped' || d.state === 'disconnected';
  const stale   = d.last_data_age > 30;
  const hasErr  = d.fail_count > 0;
  const thumb   = DEVICE_THUMBS[d.key] || '';
  return `<div class="card${offline ? ' offline' : ''}">
    <div class="card-main">
      ${thumb ? `<div class="card-media"><img class="thumb" src="${thumb}" alt="${d.name} thumbnail" /></div>` : ''}
      <div class="card-info">
        <div class="card-header">
          <div>
            <div class="card-title">${d.name}</div>
            <div class="card-sub">${d.key}<br>${d.mac}</div>
          </div>
          <div class="badge" style="color:${color}">
            <div class="dot"></div>${label}
          </div>
        </div>
        <div class="rows">
          <div class="row"><span class="lbl">最后数据</span>
            <span class="val${stale?' warn':''}"> ${fmtAge(d.last_data_age)}</span></div>
          <div class="row"><span class="lbl">连接失败</span>
            <span class="val${hasErr?' err':''}"> ${d.fail_count} 次</span></div>
          <div class="row"><span class="lbl">Signal K 客户端</span>
            <span class="val">${d.signalk_clients}</span></div>
          <div class="row"><span class="lbl">TCP 端口</span>
            <span class="val">${d.tcp_port}</span></div>
        </div>
        <div class="card-btns">
          <button class="btn btn-blue" onclick="doDisconnectDevice('${d.key}')">断连重连</button>
        </div>
      </div>
    </div>
  </div>`;
}

async function postAction(payload, confirmMsg, doubleConfirm) {
  if (_actionLock) { toast('⏳ 有操作正在执行，请稍候'); return; }
  if (confirmMsg && !confirm(confirmMsg)) return;
  if (doubleConfirm && !confirm(doubleConfirm)) return;
  _actionLock = true;
  try {
    const r = await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (d.ok) toast('✓ ' + (d.message || '已执行'));
    else      toast('✗ ' + (d.error || '执行失败'));
  } catch(e) {
    toast('✗ 请求失败：' + e.message);
  } finally {
    _actionLock = false;
  }
}

function doDisconnectDevice(key) {
  postAction({action:'disconnect_device', key});
}
function doRestartBluetooth() {
  postAction(
    {action:'restart_bluetooth'},
    '确认重启蓝牙适配器？\n\n将先断开所有设备，然后重启蓝牙服务，需要约 10 秒，之后设备自动重连。'
  );
}
function doRestartService() {
  postAction(
    {action:'restart_service'},
    '确认重启 yokuli 进程？\n\n进程将立即重启，网页会短暂无响应后自动恢复。',
  );
}
function doUpdateFromGit() {
  postAction(
    {action:'update_from_git'},
    '确认拉取 GitHub main 分支更新并重启？\n\n将执行：git fetch && git pull(main) && ./auto_launch restart',
    '二次确认：如果有本地未提交改动，可能导致更新失败。确定继续吗？'
  );
}
function doRebootPi() {
  postAction(
    {action:'reboot_pi'},
    '确认重启树莓派？\n\n系统将完全重启，所有服务将在启动后自动恢复。',
    '二次确认：树莓派将立即重启，确定吗？'
  );
}

async function poll() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    document.getElementById('grid').innerHTML = data.devices.map(renderCard).join('');
    if (data.bluetooth) renderBluetooth(data.bluetooth);
    setLogLines('normal-log', data.logs || [], 'normal');
    setLogLines('raw-log', data.raw_logs || [], 'raw');
    document.getElementById('ts').textContent =
      '更新于 ' + new Date().toLocaleTimeString('zh-CN', {hour12:false});
  } catch(e) {
    document.getElementById('ts').textContent = '⚠ 无法连接 — ' + e.message;
  }
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class StatusServer:
    """
    Minimal async HTTP server — no extra dependencies.

    GET  /            → HTML dashboard (2s auto-refresh)
    GET  /api/status  → JSON device + bluetooth snapshot
    POST /api/action  → control actions (see _run_action)
    """

    def __init__(
        self,
        port: int,
        devices: "List[BaseBleDevice]",
        bt_config: BluetoothConfig,
        coordinator: "Optional[AdapterRestartCoordinator]" = None,
    ) -> None:
        self.port = port
        self.devices = devices
        self.bt_config = bt_config
        self.coordinator = coordinator
        self._server: Optional[asyncio.AbstractServer] = None
        self._dashboard_logs: Deque[str] = deque(maxlen=180)
        self._dashboard_raw_logs: Deque[str] = deque(maxlen=180)
        self._ring_handler: Optional[_RingLogHandler] = None

    # ── Snapshot ────────────────────────────────────────────────────────────

    def _snapshot(self) -> dict:
        now = time.time()
        coord = self.coordinator
        merged_raw: List[Tuple[float, str]] = []
        if coord is not None and coord._last_restart_time > 0:
            last_ago: Optional[float] = now - coord._last_restart_time
            cooldown_remaining = max(
                0.0, self.bt_config.restart_cooldown_seconds - last_ago
            )
        else:
            last_ago = None
            cooldown_remaining = 0.0

        for device in self.devices:
            merged_raw.extend(getattr(device, "raw_packets", []))
        merged_raw.sort(key=lambda x: x[0])
        raw_logs = [line for _, line in merged_raw[-180:]]
        if self._dashboard_raw_logs:
            # Keep non-BLE DEBUG lines if present, while avoiding unbounded growth.
            raw_logs.extend(list(self._dashboard_raw_logs)[-40:])
            raw_logs = raw_logs[-180:]

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
            ],
            "bluetooth": {
                "auto_restart_enabled": self.bt_config.enable_adapter_restart,
                "cooldown_seconds": self.bt_config.restart_cooldown_seconds,
                "last_restart_ago": round(last_ago, 1) if last_ago is not None else None,
                "cooldown_remaining": round(cooldown_remaining, 1),
            },
            "logs": list(self._dashboard_logs),
            "raw_logs": raw_logs,
        }

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._install_ring_logger()
        self._server = await asyncio.start_server(
            self._handle, "0.0.0.0", self.port
        )
        logger.info(f"Status dashboard: http://0.0.0.0:{self.port}")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._remove_ring_logger()

    def _install_ring_logger(self) -> None:
        if self._ring_handler is not None:
            return
        handler = _RingLogHandler(self._dashboard_logs, self._dashboard_raw_logs)
        logging.getLogger().addHandler(handler)
        self._ring_handler = handler

    def _remove_ring_logger(self) -> None:
        if self._ring_handler is None:
            return
        logging.getLogger().removeHandler(self._ring_handler)
        self._ring_handler = None

    # ── HTTP handler ─────────────────────────────────────────────────────────

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            # Request line
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            parts = line.decode(errors="replace").split()
            if len(parts) < 2:
                return
            method = parts[0].upper()
            path = parts[1].split("?")[0]

            # Headers
            headers: dict = {}
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if h in (b"\r\n", b"\n", b""):
                    break
                if b":" in h:
                    k, _, v = h.decode(errors="replace").partition(":")
                    headers[k.strip().lower()] = v.strip()

            # Body
            body = b""
            if method == "POST":
                cl = int(headers.get("content-length", 0))
                if 0 < cl <= 4096:
                    body = await asyncio.wait_for(
                        reader.readexactly(cl), timeout=5.0
                    )

            # Route
            if method == "GET" and path == "/":
                await self._respond(writer, 200, "text/html; charset=utf-8",
                                    _HTML.encode("utf-8"))
            elif method == "GET" and path == "/api/status":
                data = json.dumps(self._snapshot()).encode("utf-8")
                await self._respond(writer, 200, "application/json", data)
            elif method == "POST" and path == "/api/action":
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                result = await self._run_action(payload)
                data = json.dumps(result).encode("utf-8")
                await self._respond(writer, 200, "application/json", data)
            else:
                await self._respond(writer, 404, "text/plain", b"Not Found")

        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: bytes,
    ) -> None:
        reason = {200: "OK", 404: "Not Found"}.get(status, "")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Cache-Control: no-cache\r\n"
            f"\r\n"
        ).encode("utf-8")
        writer.write(header + body)
        await asyncio.wait_for(writer.drain(), timeout=3.0)

    # ── Action dispatcher ────────────────────────────────────────────────────

    async def _run_action(self, payload: dict) -> dict:
        action = payload.get("action", "")

        if action == "disconnect_device":
            key = payload.get("key", "")
            device = next((d for d in self.devices if d.config.key == key), None)
            if device is None:
                return {"ok": False, "error": f"Unknown device: {key}"}
            device._disconnected_event.set()
            logger.info(f"[action] disconnect_device: {key}")
            return {"ok": True, "message": f"{key} 断连，将自动重连"}

        if action == "restart_bluetooth":
            asyncio.create_task(self._do_restart_bluetooth())
            logger.info("[action] restart_bluetooth triggered")
            return {"ok": True, "message": "蓝牙重启已开始，约 10 秒后设备自动重连"}

        if action == "restart_service":
            asyncio.create_task(self._do_restart_service())
            logger.info("[action] restart_service triggered")
            return {"ok": True, "message": "进程重启中，1 秒后执行"}

        if action == "update_from_git":
            asyncio.create_task(self._do_update_from_git())
            logger.info("[action] update_from_git triggered")
            return {"ok": True, "message": "开始拉取 GitHub main 更新，完成后自动重启"}

        if action == "reboot_pi":
            asyncio.create_task(self._do_reboot_pi())
            logger.info("[action] reboot_pi triggered")
            return {"ok": True, "message": "树莓派重启中，2 秒后执行"}

        return {"ok": False, "error": f"Unknown action: {action}"}

    # ── Action implementations ───────────────────────────────────────────────

    async def _do_restart_bluetooth(self) -> None:
        """
        Manual adapter restart triggered from the web dashboard.
        Delegates to coordinator.force_restart() so the full cleanup
        sequence (signal all devices → bluetoothctl disconnect → restart
        command → settle wait) runs exactly once, and _last_restart_time
        is updated so the auto-restart cooldown stays accurate.
        If no coordinator exists (enable_adapter_restart=false in config),
        a temporary one is created just for this call.
        """
        from adapter_restart import AdapterRestartCoordinator

        coord = self.coordinator
        if coord is None:
            coord = AdapterRestartCoordinator(self.bt_config, self.devices)

        # Use the first device as the "requester" for logging context.
        requester = self.devices[0] if self.devices else None
        if requester is None:
            logger.warning("No devices registered; skipping bluetooth restart")
            return

        await coord.force_restart(requester)

    async def _do_restart_service(self) -> None:
        """Restart the systemd service after a short delay (so HTTP response is sent first)."""
        await asyncio.sleep(1.0)
        logger.info("Restarting yokuli-ble-notifier service ...")
        try:
            proc = await asyncio.create_subprocess_shell(
                "sudo systemctl restart yokuli-ble-notifier",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15.0)
        except Exception as exc:
            logger.error(f"Service restart failed: {exc}")

    async def _do_reboot_pi(self) -> None:
        """Reboot the Raspberry Pi after a short delay."""
        await asyncio.sleep(2.0)
        logger.warning("Rebooting Raspberry Pi ...")
        try:
            proc = await asyncio.create_subprocess_shell(
                "sudo reboot",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except Exception as exc:
            logger.error(f"Reboot failed: {exc}")

    async def _do_update_from_git(self) -> None:
        """
        Pull latest code from GitHub main branch and restart via auto_launch.
        """
        await asyncio.sleep(1.0)
        logger.info("Updating from GitHub main and restarting ...")
        cmd = "git fetch origin main && git pull --ff-only origin main && ./auto_launch restart"
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            if proc.returncode != 0:
                logger.error(
                    f"Update from git failed ({proc.returncode}): "
                    f"{(err or out).decode(errors='replace').strip()}"
                )
            else:
                logger.info("Update from git completed successfully.")
        except Exception as exc:
            logger.error(f"Update from git failed: {exc}")
