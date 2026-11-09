"""Mobile/desktop visual check via Chrome DevTools Protocol device emulation (dev helper, not a test).

Headless Chrome's --window-size has a ~500px minimum width, so real phone widths need
Emulation.setDeviceMetricsOverride. Usage:
  env -u PYTHONPATH .venv/bin/python scripts/ui_snap.py URL OUT.png WIDTH HEIGHT [mobile|desk] [wait_s] [js]
Prints layout metrics (document scroll width vs viewport) to catch horizontal overflow.
"""

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets


async def main(url, out, width, height, mobile, wait_s, js=None):
    port = 9333
    prof = tempfile.mkdtemp(prefix="ks-chrome-")
    proc = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            n = 0

            async def call(method, **params):
                nonlocal n
                n += 1
                mid = n
                await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        return msg.get("result", {})

            await call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                       deviceScaleFactor=1, mobile=mobile)
            if mobile:
                await call("Emulation.setTouchEmulationEnabled", enabled=True)
            await call("Page.enable")
            await call("Page.navigate", url=url)
            await asyncio.sleep(wait_s)
            if js:
                await call("Runtime.evaluate", expression=js, awaitPromise=True)
                await asyncio.sleep(1.5)
            metrics = await call("Runtime.evaluate", returnByValue=True, expression="""(() => {
              const d = document.documentElement;
              const over = [...document.querySelectorAll('body *')].filter(e => {
                const r = e.getBoundingClientRect(); return r.width && r.right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed';
              }).slice(0, 5).map(e => e.tagName + '.' + [...e.classList].join('.') + ' r=' + Math.round(e.getBoundingClientRect().right));
              return {innerWidth, scrollWidth: d.scrollWidth, overflowing: over};
            })()""")
            print(json.dumps(metrics.get("result", {}).get("value")))
            shot = await call("Page.captureScreenshot", format="png")
            with open(out, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
    finally:
        proc.terminate()


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1], int(a[2]), int(a[3]), len(a) > 4 and a[4] == "mobile",
                     float(a[5]) if len(a) > 5 else 4, a[6] if len(a) > 6 else None))
