"""Minimal HTTP health server for the worker process.

Runs in a background daemon thread so K8s liveness/readiness probes
can reach the worker without interfering with the pipeline loop.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from logger import setup_logger

logger = setup_logger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):

    pipeline = None

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            body = json.dumps({'status': 'ok'})
            self.wfile.write(body.encode())
        elif self.path == '/status':
            status = self.pipeline.status() if self.pipeline else {}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(status, default=str).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(pipeline, port=8001):
    _HealthHandler.pipeline = pipeline
    server = HTTPServer(('0.0.0.0', port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server listening on port %d", port)
    return server
