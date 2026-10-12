import uvicorn

from . import config

cfg = config.load()
uvicorn.run("app.main:app", host=cfg.server_host, port=cfg.server_port)
