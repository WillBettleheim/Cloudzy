"""Preview the static GitHub Pages build exactly where it will live: under /Cloudzy/, with GitHub Pages' 404
behaviour (unknown paths get 404.html with status 404). Listens on localhost only.

Run:  python tools/build_pages.py && python tools/serve_pages.py [--port 8600] [--dir _site]
Then open http://localhost:8600/Cloudzy/
"""

from __future__ import annotations

import argparse
import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "/Cloudzy"


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean == PREFIX:
            clean = PREFIX + "/"
        if not clean.startswith(PREFIX + "/"):
            return str(Path(self.directory) / "__outside_the_project_path__")
        return super().translate_path(clean[len(PREFIX):])

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == PREFIX:                    # GitHub Pages redirects /Cloudzy to /Cloudzy/
            self.send_response(301)
            self.send_header("Location", PREFIX + "/")
            self.end_headers()
            return
        target = Path(self.translate_path(self.path))
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            body = (Path(self.directory) / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--dir", default=str(ROOT / "_site"))
    args = ap.parse_args()
    handler = functools.partial(Handler, directory=args.dir)
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as srv:
        print(f"Serving {args.dir} at http://localhost:{args.port}{PREFIX}/")
        srv.serve_forever()


if __name__ == "__main__":
    main()
