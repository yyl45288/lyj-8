import urllib.request

def check_page(path):
    try:
        req = urllib.request.Request(f"http://localhost:8000{path}")
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode()
            print(f"✅ /{path.lstrip('/')}: 状态 {r.status}, 字节 {len(html)}")
            if 'DOCTYPE' in html or 'html' in html.lower():
                title_start = html.find('<title>')
                if title_start != -1:
                    title_end = html.find('</title>', title_start)
                    title = html[title_start+7:title_end]
                    print(f"   标题: {title}")
                # 检查关键元素
                if 'sidebar' in html: print("   ✔ 侧边栏样式存在")
                if 'main' in html: print("   ✔ 主内容区存在")
                if 'nav-item' in html: print("   ✔ 导航链接存在")
            return True
    except Exception as e:
        print(f"❌ /{path.lstrip('/')}: {e}")
        return False

def check_api(path):
    try:
        req = urllib.request.Request(f"http://localhost:8000/api{path}")
        with urllib.request.urlopen(req, timeout=10) as r:
            import json
            data = json.loads(r.read().decode())
            print(f"✅ /api{path}: OK")
            if isinstance(data, dict) and 'data' in data:
                d = data['data']
                if isinstance(d, list):
                    print(f"   返回列表长度: {len(d)}")
                elif isinstance(d, dict):
                    print(f"   返回对象: {list(d.keys())[:8]}")
            return True
    except Exception as e:
        print(f"❌ /api{path}: {e}")
        return False

print("=" * 50)
print("【页面渲染检查】")
check_page("/")
check_page("/orders")
check_page("/sorting")
check_page("/dispatch")
check_page("/inventory")

print("\n【API接口检查】")
check_api("/products")
check_api("/leaders")
check_api("/users")
check_api("/inventory/summary")
check_api("/dashboard/stats")
check_api("/orders")
check_api("/sorting/tasks")
check_api("/dispatch/summary")
check_api("/dispatch/routes")
