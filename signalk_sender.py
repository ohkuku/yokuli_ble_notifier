from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import List, Optional


class SignalKTcpServer:
    """
    Listens on a TCP port and broadcasts Signal K delta messages to all
    connected clients (e.g. a Signal K server configured to use this
    service as a TCP source).

    Each delta message is a JSON object followed by a newline, using the
    Signal K delta format:
        {"context": "...", "updates": [{"source": {...}, "timestamp": "...",
         "values": [{"path": "...", "value": ...}, ...]}]}
    """

    def __init__(self, port: int, vessel_id: str, source_label: str) -> None:
        self.port = port
        self.vessel_id = vessel_id
        self.source_label = source_label
        self._writers: List[asyncio.StreamWriter] = []
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, "0.0.0.0", self.port
        )
        print(f"[SignalK] Listening on port {self.port} (source: {self.source_label})")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        print(f"[SignalK:{self.port}] Client connected: {addr}")
        self._writers.append(writer)
        try:
            # We don't expect inbound data; just wait until the client closes.
            await reader.read(65536)
        except Exception:
            pass
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            print(f"[SignalK:{self.port}] Client disconnected: {addr}")

    async def send(self, values: List[dict]) -> None:
        """Broadcast a Signal K delta with the given path/value list."""
        if not self._writers or not values:
            return

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        delta = {
            "context": self.vessel_id,
            "updates": [
                {
                    "source": {"label": self.source_label, "type": "sensor"},
                    "timestamp": ts,
                    "values": values,
                }
            ],
        }

        data = (json.dumps(delta) + "\n").encode("utf-8")

        dead: List[asyncio.StreamWriter] = []
        for writer in list(self._writers):
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                dead.append(writer)

        for writer in dead:
            if writer in self._writers:
                self._writers.remove(writer)
