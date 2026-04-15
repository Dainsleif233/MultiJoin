from http.server import HTTPServer, BaseHTTPRequestHandler
import uuid
import json
import string
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from libs.config import load_always_format, load_entries
from libs.data import ProfilesData

MAX_PROFILE_NAME_LENGTH = 16
ALWAYS_FORMAT = load_always_format()
ENTRIES = load_entries()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/hasJoined"):
            query_suffix = self.path[len("/hasJoined"):]
            targets = {entry_id: f"{entry['api']}{query_suffix}" for entry_id, entry in ENTRIES.items()}
            if not targets:
                print("No targets found")
                self.send_response(204)
                self.end_headers()
                return

            winner_id = None
            winner_data = None
            winner_headers = {}

            def fetch_target(entry_id, target_url):
                try:
                    with urlopen(target_url, timeout=3) as response:
                        return entry_id, response.getcode(), response.read(), dict(response.headers.items())
                except HTTPError as e:
                    return entry_id, e.code, e.read(), dict(e.headers.items())
                except (URLError, TimeoutError):
                    return entry_id, None, b"", {}

            with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                future_map = {
                    executor.submit(fetch_target, entry_id, target_url): entry_id
                    for entry_id, target_url in targets.items()
                }
                try:
                    for future in as_completed(future_map, timeout=3):
                        entry_id, status_code, response_data, response_headers = future.result()
                        if status_code == 200:
                            try:
                                parsed_data = json.loads(response_data.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                continue
                            winner_id = entry_id
                            winner_data = parsed_data
                            winner_headers = response_headers
                            for other_future in future_map:
                                if other_future is not future:
                                    other_future.cancel()
                            break
                except FuturesTimeoutError:
                    pass

            if winner_id is not None:
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

def format_entry_name(entry_id, name):
    return ENTRIES[entry_id]["format"].format(name=name, entry=entry_id)

def truncate_name_for_entry(entry_id, name):
    while name and len(format_entry_name(entry_id, name)) > MAX_PROFILE_NAME_LENGTH:
        name = name[:-1]
    if len(format_entry_name(entry_id, name)) > MAX_PROFILE_NAME_LENGTH:
        raise ValueError(f"Entry '{entry_id}' name format exceeds {MAX_PROFILE_NAME_LENGTH} characters")
    return name

def increment_name(name: str):
    if not name:
        return "a"

    chars = list(name)
    for index in range(len(chars) - 1, -1, -1):
        char = chars[index]
        lower_char = char.lower()
        if lower_char not in string.ascii_lowercase:
            continue
        if lower_char == "z":
            chars[index] = "A" if char.isupper() else "a"
            continue
        chars[index] = chr(ord(char) + 1)
        return "".join(chars)

    chars[-1] = "a"
    return "".join(chars)

def make_unique_entry_name(data: ProfilesData, pid, entry_id, profile_name):
    name = truncate_name_for_entry(entry_id, profile_name)
    attempted_names = set()

    while True:
        candidate = format_entry_name(entry_id, name)
        if candidate in attempted_names:
            raise ValueError(f"No available player name for {profile_name}")
        attempted_names.add(candidate)
        if not data.exists_name_except_profile(pid, candidate):
            return candidate
        name = increment_name(name)

def handleProfile(conn: Handler, entry_id, profile, winner_headers: Dict[str, str]):
    print(f"Player {profile['name']} ({profile['id']}) authentication successful, entry: {entry_id}")
    data = ProfilesData("profiles.csv")
    pid = data.query_profile_by_entry_uuid(entry_id, profile["id"])
    if pid == None:
        print(f"Profile {profile['name']} not found, adding new one")
        if data.exists_uuid(profile["id"]):
            pid = uuid.uuid4().hex
            data.add(pid, entry_id, profile["id"], profile["name"])
            print(f"UUID {profile['id']} already exists, mapped to {pid}")
        else:
            data.add(profile["id"], entry_id, profile["id"], profile["name"])
    
    pid = data.query_profile_by_entry_uuid(entry_id, profile["id"])
    profile["id"] = pid
    if ALWAYS_FORMAT or data.exists_name_except_profile(pid, profile["name"]):
        name = make_unique_entry_name(data, pid, entry_id, profile["name"])
        if ALWAYS_FORMAT:
            print(f"Playername {profile['name']} formatted to {name}")
        else:
            print(f"Playername {profile['name']} already exists, renaming to {name}")
        profile["name"] = name
    data.update_name_by_profile(pid, profile["name"])

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
    conn.send_response(200)
    for header_name, header_value in winner_headers.items():
        if header_name.lower() in hop_by_hop_headers:
            continue
        if header_name.lower() == "content-type":
            continue
        conn.send_header(header_name, header_value)
    conn.send_header("Content-Type", "application/json; charset=utf-8")
    conn.send_header("Content-Length", str(len(response_body)))
    conn.end_headers()
    conn.wfile.write(response_body)

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 2268), Handler)
    print("Server running on port 2268")
    server.serve_forever()
