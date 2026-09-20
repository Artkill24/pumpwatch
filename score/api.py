"""
API del wallet score.

Solo stdlib: nessun framework da installare.
Avvio:  python api.py          (porta 8000)
        PORT=9000 python api.py

Endpoint:
  GET /api/wallet/<indirizzo>   profilo del creator
  GET /api/mint/<mint>          profilo del creator di quel token
  GET /api/risky                i wallet con più rug
  GET /api/stats                dimensione del dataset
  GET /                         pagina di consultazione
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

from score import profile_creator, profile_mint, top_risky, dataset_stats

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8000))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2))

    def do_GET(self):
        path = unquote(urlparse(self.path).path).rstrip("/") or "/"

        try:
            if path == "/":
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")

            if path == "/api/stats":
                return self._json(200, dataset_stats())

            if path == "/api/risky":
                return self._json(200, {"wallets": top_risky(25)})

            if path.startswith("/api/wallet/"):
                addr = path[len("/api/wallet/"):]
                if not addr:
                    return self._json(400, {"error": "indirizzo mancante"})
                return self._json(200, profile_creator(addr).to_dict())

            if path.startswith("/api/mint/"):
                mint = path[len("/api/mint/"):]
                p = profile_mint(mint)
                if p is None:
                    return self._json(404, {
                        "error": "token non presente nel dataset",
                        "mint": mint})
                return self._json(200, p.to_dict())

            return self._json(404, {"error": "endpoint sconosciuto"})

        except FileNotFoundError:
            self._json(500, {"error": "index.html non trovato"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}")


if __name__ == "__main__":
    s = dataset_stats()
    print(f"dataset: {s['mints']} token, {s['creators']} creator, "
          f"{s['measured']} misurati")
    print(f"in ascolto su http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
