import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.requests import Request
from starlette.datastructures import Headers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates_dir = os.path.join(BASE_DIR, "app", "templates")

print(f"BASE_DIR: {BASE_DIR}")
print(f"templates_dir: {templates_dir}")
print(f"目录存在: {os.path.exists(templates_dir)}")
if os.path.exists(templates_dir):
    print(f"目录内容: {os.listdir(templates_dir)}")

try:
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=templates_dir)
    print("✅ Jinja2Templates 初始化成功")
    
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"host", b"localhost:8000")],
        "server": ("localhost", 8000),
        "client": ("127.0.0.1", 12345),
        "scheme": "http",
    }
    req = Request(scope)
    
    try:
        resp = templates.TemplateResponse("index.html", {"request": req})
        print(f"✅ index.html 渲染成功, 长度 {len(resp.body)}")
    except Exception as e:
        import traceback
        print(f"❌ index.html 渲染错误:")
        traceback.print_exc()
except Exception as e:
    import traceback
    print(f"❌ 模板加载错误:")
    traceback.print_exc()
