from http.server import HTTPServer, BaseHTTPRequestHandler
import uuid
import json
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from libs.data import LightweightDataTable
from entries import ENTRIES

def handleProfile(self, entry, profile, winner_headers):
    print(f"Player {profile['name']} ({profile['id']}) authentication successful, entry: {entry}")
    data = LightweightDataTable("profiles.csv")
    pid = data.query_by_bc(entry, profile["id"])
    if pid == None:
        print(f"Profile {profile['name']} not found, adding new one")
        if data.exists_c(profile["id"]):
            pid = uuid.uuid4().hex
            data.add(pid, entry, profile["id"], profile["name"])
            print(f"Profile {profile['id']} already exists, mapped to {pid}")
        else:
            data.add(profile["id"], entry, profile["id"], profile["name"])
    
    pid = data.query_by_bc(entry, profile["id"])
    profile["id"] = pid
    if data.exists_d(pid, profile["name"]):
        suffix = f"_{entry}"
        name = f"{profile['name'][:max(0, 16 - len(suffix))]}{suffix}"
        while data.exists_d(pid, name):
            random_entry = "".join(random.choices(string.ascii_lowercase, k=len(entry)))
            random_suffix = f"_{random_entry}"
            name = f"{profile['name'][:max(0, 16 - len(random_suffix))]}{random_suffix}"
        print(f"Profile {profile['name']} already exists, renaming to {name}")
        profile["name"] = name
    data.update_d_by_a(pid, profile["name"])

    response_body = json.dumps(profile).encode("utf-8")
    hop_by_hop_headers = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }

    # print(f"Response headers: {winner_headers}")
    # print(f"Response profile: {json.dumps(profile, ensure_ascii=False)}")
    self.send_response(200)
    for header_name, header_value in winner_headers.items():
        if header_name.lower() in hop_by_hop_headers:
            continue
        if header_name.lower() == "content-type":
            continue
        self.send_header(header_name, header_value)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(response_body)))
    self.end_headers()
    self.wfile.write(response_body)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/hasJoined"):
            query_suffix = self.path[len("/hasJoined"):]
            targets = {target_id: f"{api}{query_suffix}" for target_id, api in ENTRIES.items()}
            if not targets:
                print("No targets found")
                self.send_response(204)
                self.end_headers()
                return

            winner_id = None
            winner_data = None
            winner_headers = {}

            def fetch_target(target_id, target_url):
                try:
                    with urlopen(target_url, timeout=3) as response:
                        return target_id, response.getcode(), response.read(), dict(response.headers.items())
                except HTTPError as e:
                    return target_id, e.code, e.read(), dict(e.headers.items())
                except (URLError, TimeoutError):
                    return target_id, None, b"", {}

            with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                future_map = {
                    executor.submit(fetch_target, target_id, target_url): target_id
                    for target_id, target_url in targets.items()
                }
                try:
                    for future in as_completed(future_map, timeout=3):
                        target_id, status_code, response_data, response_headers = future.result()
                        if status_code == 200:
                            try:
                                parsed_data = json.loads(response_data.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                continue
                            winner_id = target_id
                            winner_data = parsed_data
                            winner_headers = response_headers
                            for other_future in future_map:
                                if other_future is not future:
                                    other_future.cancel()
                            break
                except FuturesTimeoutError:
                    pass

            if winner_id is not None:
                print(f"Success to Fetch: {winner_id}")
                # print(f"Winner headers: {winner_headers}")
                # print(f"Winner data: {json.dumps(winner_data, ensure_ascii=False)}")
                handleProfile(self, winner_id, winner_data, winner_headers)
            else:
                print("No valid response from any target")
                self.send_response(204)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        self.send_response(405)
        self.end_headers()
        self.wfile.write(b'{"path": "/hasJoined"}')

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 2268), Handler)
    print("Server running on port 2268")
    server.serve_forever()
