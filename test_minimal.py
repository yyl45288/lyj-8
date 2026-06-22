import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))

@app.get("/test1")
async def test1(request: Request):
    try:
        return templates.TemplateResponse("base.html", {"request": request})
    except Exception as e:
        import traceback
        return {"error": str(e), "tb": traceback.format_exc()}

@app.get("/test2")
async def test2(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        import traceback
        return {"error": str(e), "tb": traceback.format_exc()}

client = TestClient(app)

for path in ["/test1", "/test2"]:
    print(f"\n=== {path} ===")
    r = client.get(path)
    if r.status_code == 200:
        if isinstance(r.json(), dict) and "error" in r.json():
            print("ERROR:", r.json()["error"])
            print(r.json()["tb"][:3000])
        else:
            print(f"OK: {len(r.content)} bytes")
    else:
        print(f"Status {r.status_code}: {r.text[:1000]}")
