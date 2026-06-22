import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

response = client.get("/")
print(f"状态: {response.status_code}")
if response.status_code == 200:
    print(f"响应长度: {len(response.text)}")
    if "<title>" in response.text:
        import re
        m = re.search(r'<title>(.*?)</title>', response.text)
        if m: print(f"标题: {m.group(1)}")
    else:
        print("前200字符:", response.text[:200])
else:
    print(f"错误详情: {response.text}")

print("\n--- 测试其他页面 ---")
for path in ["/orders", "/sorting", "/dispatch", "/inventory"]:
    r = client.get(path)
    print(f"{path}: {r.status_code} {len(r.text)}字符")
    if r.status_code != 200:
        print(f"  错误: {r.text[:500]}")
