import os
import sys
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates_dir = os.path.join(BASE_DIR, "app", "templates")

env = Environment(
    loader=FileSystemLoader(templates_dir),
    autoescape=select_autoescape(['html', 'xml'])
)

class FakeURL:
    def __call__(self, *args, **kwargs):
        return "#"

class FakeRequest:
    def __init__(self, path="/"):
        self.url = FakeURL()
        self.base_url = FakeURL()
        self.method = "GET"
        self.path = path
        self.headers = {}
        self.query_params = {}
        self.cookies = {}

for template_name in ["base.html", "index.html", "orders.html", "sorting.html", "dispatch.html", "inventory.html"]:
    print(f"\n=== 测试模板: {template_name} ===")
    try:
        template = env.get_template(template_name)
        print(f"✅ 模板加载成功")
        if template_name != "base.html":
            request = FakeRequest("/" if template_name == "index.html" else "/" + template_name.replace(".html", ""))
            try:
                html = template.render(request=request)
                print(f"✅ 模板渲染成功, 长度={len(html)}")
                if "<title>" in html:
                    import re
                    m = re.search(r'<title>(.*?)</title>', html)
                    if m: print(f"   标题: {m.group(1)}")
            except Exception as e:
                import traceback
                print(f"❌ 渲染失败: {e}")
                traceback.print_exc()
    except Exception as e:
        import traceback
        print(f"❌ 加载失败: {e}")
        traceback.print_exc()
