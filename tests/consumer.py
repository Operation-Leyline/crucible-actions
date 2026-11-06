"""Exercise the documented onboarding flow from an independent consumer."""

import argparse
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit


def run(*command, cwd):
    subprocess.run(command, cwd=cwd, check=True)


def prepare(root):
    root.parent.mkdir(parents=True, exist_ok=True)
    run("crucible", "new", "hello-service", "--out", str(root), cwd=root.parent)
    run("git", "init", cwd=root)
    run("cargo", "generate-lockfile", cwd=root)
    (root / "crucible-sdk.toml").write_text('''schema_version = 1
model = "model"
[sdk.ts]
package = "@example/hello"
endpoint = "http://127.0.0.1:8080"
out = "generated/hello"
''', encoding="utf-8")


def request(endpoint, body):
    address = urlsplit(endpoint)
    # One write also works when Windows rejects a request before reading its body.
    message = (f"POST /hello HTTP/1.1\r\nHost: {address.netloc}\r\nContent-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
    with socket.create_connection((address.hostname, address.port), timeout=10) as connection:
        connection.sendall(message)
        with http.client.HTTPResponse(connection) as response:
            response.begin()
            return response.status, json.load(response)


def verify(root):
    run("crucible", "check", "model", cwd=root)
    # This command also verifies that setup selected the service's exact compiler.
    run("crucible", "sdk", "generate", cwd=root)
    assert (root / "generated/hello/package.json").is_file()
    run("cargo", "build", "--locked", cwd=root)
    target = Path(os.environ.get("CARGO_TARGET_DIR", root / "target")).resolve()
    binary = target / "debug" / ("hello-service.exe" if os.name == "nt" else "hello-service")
    log = root / "server.log"
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([str(binary)], env={**os.environ, "CRUCIBLE_ADDR": "127.0.0.1:0"}, stdout=stream, stderr=subprocess.STDOUT)
        try:
            endpoint = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                text = log.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if line.startswith("Listening on "):
                        endpoint = line.removeprefix("Listening on ")
                if endpoint or process.poll() is not None:
                    break
                time.sleep(0.05)
            assert endpoint, log.read_text(encoding="utf-8")
            assert request(endpoint, b'{"name":"World"}') == (200, {"message": "Hello, World!"})
            status, error = request(endpoint, b"{}")
            assert status == 400 and error["__type"] == "ValidationException", (status, error)
            address = urlsplit(endpoint)
            with closing(http.client.HTTPConnection(address.hostname, address.port, timeout=10)) as connection:
                connection.request("GET", "/explorer/")
                response = connection.getresponse()
                assert response.status == 200 and "text/html" in response.getheader("Content-Type", ""), response.status
                assert b"<!doctype html" in response.read().lower()
            print("Fresh consumer passed: matching CLI, model check, SDK generation, explorer, HTTP 200 and modeled 400")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "verify"])
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    {"prepare": prepare, "verify": verify}[args.mode](args.root.resolve())
