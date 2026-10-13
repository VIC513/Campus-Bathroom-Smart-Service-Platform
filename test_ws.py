import asyncio
import websockets
import json

async def test():
    uri = "ws://127.0.0.1:8000/ws/stalls"
    async with websockets.connect(uri) as ws:
        print("[连接成功] 等待推送...")
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            stalls = data["data"]
            occupied = len([s for s in stalls if s["status"] == "occupied"])
            print(f"[收到] {data['type']}，occupied={occupied}")

asyncio.run(test())