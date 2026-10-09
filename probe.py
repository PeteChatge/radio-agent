"""OWRX WS-Probe: Handshake + Nachrichten-Dump (Ticket 01)."""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8073/ws/"
DUMP_SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0


async def main():
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send("SERVER DE CLIENT client=openwebrx.js type=receiver")
        print("HANDSHAKE-SENT", flush=True)
        try:
            async with asyncio.timeout(DUMP_SECS):
                async for msg in ws:
                    if isinstance(msg, bytes):
                        print(f"BIN len={len(msg)} head={msg[:16].hex()}", flush=True)
                    else:
                        try:
                            d = json.loads(msg)
                            t = d.get("type", "?")
                            keys = sorted(d.keys())
                            body = msg if len(msg) < 600 else msg[:600] + "…"
                            print(f"TXT type={t} keys={keys}\n  {body}", flush=True)
                        except json.JSONDecodeError:
                            print(f"RAW {msg[:200]}", flush=True)
        except TimeoutError:
            print("DUMP-DONE", flush=True)


asyncio.run(main())
