import urllib.request
import json

# 获取服务器错误详情
try:
    req = urllib.request.Request("http://localhost:8000/")
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode()
except urllib.error.HTTPError as e:
    error_body = e.read().decode()
    print(f"状态码: {e.code}")
    print(f"错误内容:\n{error_body[:3000]}")
