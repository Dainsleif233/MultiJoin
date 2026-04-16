from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import uuid
import json
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from libs.config import load_always_format, load_entries, load_key, load_token_expires_in
from libs.data import ProfilesData

MAX_PROFILE_NAME_LENGTH = 16
PROFILES_PATH = Path(__file__).resolve().parent / "profiles.csv"
ALWAYS_FORMAT = load_always_format()
KEY = load_key()
TOKEN_EXPIRES_IN = load_token_expires_in()
ENTRIES = load_entries()
BIND_TOKENS = {}
BIND_TOKENS_BY_PID = {}
BIND_TOKENS_LOCK = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/bind":
            handle_bind(self, parsed_path.query)
        elif self.path.startswith("/hasJoined?"):
            query_suffix = self.path[len("/hasJoined"):]
            targets = {entry_id: f"{entry['api']}{query_suffix}" for entry_id, entry in ENTRIES.items()}
            if not targets:
                print("[WARN] No entries configured")
                self.send_response(204)
                self.end_headers()
                return

            winner_id = None
            winner_data = None
            winner_headers = {}

            def fetch_target(entry_id, target_url):
                try:
                    with urlopen(target_url, timeout=5) as response:
                        return entry_id, response.getcode(), response.read(), dict(response.headers.items())
                except HTTPError as e:
                    return entry_id, e.code, e.read(), dict(e.headers.items())
                except (URLError, TimeoutError):
                    return entry_id, None, b"", {}

            executor = ThreadPoolExecutor(max_workers=len(targets))
            future_map = {}
            try:
                future_map = {
                    executor.submit(fetch_target, entry_id, target_url): entry_id
                    for entry_id, target_url in targets.items()
                }
                try:
                    for future in as_completed(future_map, timeout=5):
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
            finally:
                for future in future_map:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)

            if winner_id is not None:
                # print(f"Winner headers: {winner_headers}")
                # print(f"Winner data: {json.dumps(winner_data, ensure_ascii=False)}")
                handleProfile(self, winner_id, winner_data, winner_headers)
            else:
                print(f"[MISS] No valid response: {self.path}")
                self.send_response(204)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        self.send_response(405)
        self.end_headers()
        self.wfile.write(b'{"path": "/hasJoined"}')

def send_text(conn: Handler, status_code: int, message: str = ""):
    response_body = message.encode("utf-8")
    conn.send_response(status_code)
    conn.send_header("Content-Type", "text/plain; charset=utf-8")
    conn.send_header("Content-Length", str(len(response_body)))
    conn.end_headers()
    if response_body:
        conn.wfile.write(response_body)

def require_one_param(params, name):
    values = params.get(name)
    if not values or values[0] == "":
        return None
    return values[0]

def cleanup_expired_bind_tokens(now=None):
    if now is None:
        now = time.time()

    expired_tokens = [
        token
        for token, token_data in BIND_TOKENS.items()
        if token_data["expires_at"] <= now
    ]
    for token in expired_tokens:
        token_data = BIND_TOKENS.pop(token, None)
        if token_data is None:
            continue
        if BIND_TOKENS_BY_PID.get(token_data["pid"]) == token:
            del BIND_TOKENS_BY_PID[token_data["pid"]]

def create_bind_token(pid):
    now = time.time()
    token = uuid.uuid4().hex
    with BIND_TOKENS_LOCK:
        cleanup_expired_bind_tokens(now)
        old_token = BIND_TOKENS_BY_PID.get(pid)
        if old_token is not None:
            BIND_TOKENS.pop(old_token, None)
        BIND_TOKENS[token] = {
            "pid": pid,
            "expires_at": now + TOKEN_EXPIRES_IN,
        }
        BIND_TOKENS_BY_PID[pid] = token
    return token

def get_valid_bind_token_pid(token):
    now = time.time()
    with BIND_TOKENS_LOCK:
        cleanup_expired_bind_tokens(now)
        token_data = BIND_TOKENS.get(token)
        if token_data is None:
            return None
        return token_data["pid"]

def remove_bind_token(token):
    with BIND_TOKENS_LOCK:
        token_data = BIND_TOKENS.pop(token, None)
        if token_data is None:
            return
        if BIND_TOKENS_BY_PID.get(token_data["pid"]) == token:
            del BIND_TOKENS_BY_PID[token_data["pid"]]

def handle_bind(conn: Handler, query: str):
    params = parse_qs(query, keep_blank_values=True)
    action = require_one_param(params, "action")
    key = require_one_param(params, "key")
    pid = require_one_param(params, "pid")

    if action not in {"token", "bind", "unBind"}:
        send_text(conn, 400, "invalid action")
        return
    if not KEY or key != KEY:
        send_text(conn, 403, "invalid key")
        return
    if pid is None:
        send_text(conn, 400, "missing pid")
        return

    data = ProfilesData(PROFILES_PATH)
    try:
        if action == "token":
            handle_bind_token(conn, data, pid)
        elif action == "bind":
            token = require_one_param(params, "token")
            if token is None:
                send_text(conn, 400, "missing token")
                return
            handle_bind_apply(conn, data, pid, token)
        else:
            handle_bind_clear(conn, data, pid)
    except KeyError:
        send_text(conn, 404, "profile not found")

def handle_bind_token(conn: Handler, data: ProfilesData, pid: str):
    with data.latest():
        if not data.exists_profile(pid):
            send_text(conn, 404, "profile not found")
            return
        if not data.is_unbound_profile(pid):
            send_text(conn, 409, "profile already bound")
            return

    token = create_bind_token(pid)
    send_text(conn, 200, token)

def handle_bind_apply(conn: Handler, data: ProfilesData, pid: str, token: str):
    source_pid = get_valid_bind_token_pid(token)
    if source_pid is None:
        send_text(conn, 410, "token expired or not found")
        return

    with data.latest():
        if not data.exists_profile(pid) or not data.exists_profile(source_pid):
            send_text(conn, 404, "profile not found")
            return
        if pid == source_pid:
            send_text(conn, 409, "cannot bind profile to itself")
            return
        if not data.is_unbound_profile(source_pid):
            send_text(conn, 409, "profile already bound")
            return

        data.update_bind_by_profile(source_pid, pid)
        remove_bind_token(token)

    send_text(conn, 204)

def handle_bind_clear(conn: Handler, data: ProfilesData, pid: str):
    with data.latest():
        if not data.exists_profile(pid):
            send_text(conn, 404, "profile not found")
            return
        if data.is_unbound_profile(pid):
            send_text(conn, 409, "profile is not bound")
            return

        data.clear_bind_by_profile(pid)

    send_text(conn, 204)

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

def short_id(value):
    if value is None:
        return "-"
    return f"{value[:8]}..."

def log_profile_result(entry_id, original_name, original_uuid, profile_id, final_name, actions):
    action_text = ",".join(actions) if actions else "existing"
    rename_text = f" name={original_name}->{final_name}" if original_name != final_name else f" name={final_name}"
    print(
        f"[JOIN] entry={entry_id}{rename_text} "
        f"uuid={short_id(original_uuid)} profile={short_id(profile_id)} actions={action_text}"
    )

def handleProfile(conn: Handler, entry_id, profile: Dict[str, str | list], winner_headers: Dict[str, str]):
    original_name = profile["name"]
    original_uuid = profile["id"]
    actions = []
    bind = ""

    data = ProfilesData(PROFILES_PATH)
    with data.latest():
        pid = data.query_profile_by_entry_uuid(entry_id, original_uuid)
        if pid == None:
            actions.append("new")
            if data.exists_uuid(original_uuid):
                pid = uuid.uuid4().hex
                data.add(pid, entry_id, original_uuid, original_name)
                actions.append("mapped_uuid")
            else:
                pid = original_uuid
                data.add(pid, entry_id, original_uuid, original_name)

        profile["id"] = pid
        if ALWAYS_FORMAT or data.exists_name_except_profile(pid, profile["name"]):
            name = make_unique_entry_name(data, pid, entry_id, profile["name"])
            if ALWAYS_FORMAT:
                actions.append("formatted")
            else:
                actions.append("renamed")
            profile["name"] = name
        data.update_name_by_profile(pid, profile["name"])
        bind = data.get_bind_by_profile(pid)
        if bind and data.exists_profile(bind):
            profile["id"] = bind
            actions.append("bound")
        log_profile_result(entry_id, original_name, original_uuid, pid, profile["name"], actions)

    multijoin_data = {
        "profile": pid,
        "entry": entry_id,
        "uuid": original_uuid,
        "name": original_name,
        "bind": bind if profile["id"] == bind else ""
    }
    multijoin = {
        "name": "multijoin",
        "value": json.dumps(multijoin_data, ensure_ascii=False)
    }
    profile["properties"].append(multijoin)

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
    server = ThreadingHTTPServer(("0.0.0.0", 2268), Handler)
    print("Server running on port 2268")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")
    finally:
        server.server_close()
