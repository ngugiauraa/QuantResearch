"""ASGI entry point for Railway. Finds the app whether server.py exposes
`app` directly or builds it through a factory function."""
from quantresearch.api import server as _server

app = getattr(_server, "app", None)

if app is None:
    for _name in ("create_app", "make_app", "build_app", "get_app", "app_factory"):
        _factory = getattr(_server, _name, None)
        if callable(_factory):
            app = _factory()
            break

if app is None:
    _names = [n for n in dir(_server) if not n.startswith("_")]
    raise RuntimeError(f"No app or app factory found in server.py. Names available: {_names}")
