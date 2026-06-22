from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
import os
import traceback
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.database import Base, engine, SessionLocal, get_db
from app import models
from app.api.routes import router as api_router
from app.seed_data import seed_all
from sqlalchemy.orm import Session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "app", "templates")

jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(['html', 'xml'])
)

def render_template(template_name: str, context: dict) -> str:
    template = jinja_env.get_template(template_name)
    return template.render(**context)


models.Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    seed_all(db)
finally:
    db.close()

app = FastAPI(title="社区团购履约与调度引擎", version="1.0.0")

app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return HTMLResponse(render_template("index.html", {"current_path": "/"}))

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request):
    return HTMLResponse(render_template("orders.html", {"current_path": "/orders"}))

@app.get("/sorting", response_class=HTMLResponse)
async def sorting_page(request: Request):
    return HTMLResponse(render_template("sorting.html", {"current_path": "/sorting"}))

@app.get("/dispatch", response_class=HTMLResponse)
async def dispatch_page(request: Request):
    return HTMLResponse(render_template("dispatch.html", {"current_path": "/dispatch"}))

@app.get("/inventory", response_class=HTMLResponse)
async def inventory_page(request: Request):
    return HTMLResponse(render_template("inventory.html", {"current_path": "/inventory"}))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=28765, reload=False)
