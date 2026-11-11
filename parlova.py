#!/usr/bin/env python3
"""Parlova command-line tool and MCP server.

Transcribe recordings and YouTube links from your terminal, or let AI apps (Claude Desktop,
Claude Code, Cursor, VS Code…) do it through MCP. Needs Python 3.8+ and nothing else.

    parlova login                       sign in through your browser (no token to copy)
    parlova transcribe meeting.mp3      upload, wait, print the transcript
    parlova transcribe URL --speakers --format srt -o talk.srt
    parlova list                        your recent transcriptions
    parlova get JOB_ID --format docx -o notes.docx
    parlova usage                       minutes used this month
    parlova mcp-config                  print the MCP config for Claude Desktop, Cursor, VS Code…
    parlova mcp                         run as an MCP server (stdio)

`parlova login` stores the token in ~/.config/parlova/config.json, readable only by you.
For servers and CI, use `parlova login --token …` or set PARLOVA_TOKEN instead.
"""

import argparse
import getpass
import json
import os
import platform
import sys
import time
import webbrowser
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "1.0.0"
DEFAULT_URL = "https://parlova.co.za"  # filled in by the site you download this from
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
CONFIG = CONFIG_DIR / "parlova" / "config.json"
OLD_CONFIG = CONFIG_DIR / "anyscribe" / "config.json"  # before the rename to Parlova
FORMATS = ("txt", "srt", "vtt", "csv", "md", "json", "docx", "pdf")
TEXT_FORMATS = ("txt", "srt", "vtt", "csv", "md", "json")
FINISHED = ("completed", "failed", "cancelled")


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Settings and HTTP
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    for path in (CONFIG, OLD_CONFIG):  # a sign-in saved before the rename still works
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return {}


def save_config(data: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(data, indent=2))
    try:
        CONFIG.chmod(0o600)  # the token is a secret
    except OSError:
        pass


def base_url() -> str:
    url = os.environ.get("PARLOVA_URL") or os.environ.get("ANYSCRIBE_URL") or load_config().get("url") or DEFAULT_URL
    if url.startswith("__"):
        raise ApiError("No Parlova address set. Run: parlova login --url https://your-parlova-site")
    return url.rstrip("/")


def token() -> str:
    value = os.environ.get("PARLOVA_TOKEN") or os.environ.get("ANYSCRIBE_TOKEN") or load_config().get("token")
    if not value:
        raise ApiError("Not logged in. Create a token in Settings > Developers, then run: parlova login")
    return value


def request(method: str, path: str, *, body=None, headers=None, raw: bool = False, length: int = None, auth=None):
    """Call the API. Returns parsed JSON, or bytes when raw=True. Raises ApiError with the server's message.
    auth: a token to use instead of the saved one, or False for the sign-in endpoints that need none."""
    req = urllib.request.Request(base_url() + path, data=body, method=method)
    if auth is not False:
        req.add_header("Authorization", f"Bearer {auth or token()}")
    req.add_header("User-Agent", f"parlova-cli/{VERSION}")
    if length is not None:
        req.add_header("Content-Length", str(length))
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error") or detail
        except ValueError:
            pass
        error = ApiError(f"{detail} (HTTP {exc.code})")
        error.status = exc.code
        raise error from None
    except urllib.error.URLError as exc:
        raise ApiError(f"Couldn't reach {base_url()}: {exc.reason}") from None
    return data if raw else json.loads(data or b"null")


def query(**params) -> str:
    return urllib.parse.urlencode({k: ("true" if v is True else v) for k, v in params.items() if v not in (None, False)})


# --------------------------------------------------------------------------- #
# Transcription helpers (shared by the CLI and the MCP server)
# --------------------------------------------------------------------------- #

def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "www.", "youtu.be/", "youtube.com/"))


def submit(source: str, language: str = "auto", speakers: bool = False) -> dict:
    """Start a transcription for a local file or a YouTube link. Returns the new job."""
    if is_url(source):
        url = source if source.startswith("http") else "https://" + source
        body = json.dumps({"urls": [url], "language": language, "diarize": speakers}).encode()
        return request("POST", "/api/jobs", body=body, headers={"Content-Type": "application/json"})[0]
    path = Path(source).expanduser()
    if not path.is_file():
        raise ApiError(f"File not found: {source}")
    with path.open("rb") as handle:
        params = query(filename=path.name, language=language, diarize=speakers)
        return request("POST", f"/api/jobs/upload?{params}", body=handle, length=path.stat().st_size,
                       headers={"Content-Type": "application/octet-stream"})[0]


def wait(job_id: str, timeout: float = None, on_progress=None) -> dict:
    """Poll until the job finishes (or the timeout passes). Returns the latest job."""
    started = time.time()
    while True:
        job = request("GET", f"/api/jobs/{job_id}")
        if job["status"] in FINISHED:
            return job
        if on_progress:
            on_progress(job)
        if timeout is not None and time.time() - started > timeout:
            return job
        time.sleep(3)


def clock(ms) -> str:
    seconds = int((ms or 0) // 1000)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def readable(job: dict) -> str:
    """Transcript with timestamps and speaker names when the plan includes them."""
    names = job.get("speakerNames") or {}
    lines = []
    for p in job.get("paragraphs") or []:
        prefix = f"[{clock(p['start'])}] " if "start" in p else ""
        speaker = p.get("speaker")
        if speaker is not None:
            prefix += f"{names.get(str(speaker)) or f'Speaker {int(speaker) + 1 if str(speaker).isdigit() else speaker}'}: "
        lines.append(prefix + p.get("text", ""))
    return "\n\n".join(lines) or job.get("text") or ""


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #

def cmd_login(args) -> None:
    config = load_config()
    if args.url:
        config["url"] = args.url.rstrip("/")
        save_config(config)
    if args.token is not None:  # servers/CI: a token made under Settings > Developers > Access tokens
        value = args.token or getpass.getpass("Paste your access token: ").strip()
    else:
        value = browser_login(open_browser=not args.no_browser)
    if not value:
        raise ApiError("No token given.")
    usage = request("GET", "/api/usage", auth=value)  # check it works before saving
    config["token"] = value
    save_config(config)
    print(f"Logged in to {base_url()} ({usage['planName']} plan). Saved to {CONFIG} (only you can read it).")


def browser_login(open_browser: bool = True) -> str:
    """Sign in by approving this computer in the browser. Returns the new token."""
    name = (platform.node() or "my computer").split(".")[0]
    body = json.dumps({"client_name": name}).encode()
    start = request("POST", "/api/cli/login/start", body=body, headers={"Content-Type": "application/json"}, auth=False)
    url, code = start["verificationUrl"], start["userCode"]
    print(f"Your sign-in code: {code}", flush=True)
    if open_browser and webbrowser.open(url):
        print("Opened your browser. Check the code matches, then click Approve.", flush=True)
    else:
        print(f"Open this link in your browser, check the code matches, then click Approve:\n  {url}", flush=True)
    print("Waiting for approval… (Ctrl+C to cancel)", flush=True)
    deadline = time.time() + start["expiresIn"]
    poll = json.dumps({"device_code": start["deviceCode"]}).encode()
    while time.time() < deadline:
        time.sleep(start.get("interval", 3))
        try:
            result = request("POST", "/api/cli/login/poll", body=poll, headers={"Content-Type": "application/json"}, auth=False)
        except ApiError as exc:
            if getattr(exc, "status", None) == 429:
                continue
            raise
        if result["status"] == "approved":
            return result["token"]
    raise ApiError("Sign-in timed out. Run parlova login again.")


def cmd_logout(args) -> None:
    config = load_config()
    config.pop("token", None)
    save_config(config)
    print("Logged out on this computer. To switch the token off completely, revoke it under "
          "Settings > Developers > Access tokens.")


def cmd_transcribe(args) -> None:
    fmt = args.format
    for source in args.sources:
        job = submit(source, args.language, args.speakers)
        if args.no_wait:
            print(job["id"])
            continue
        print(f"Transcribing {job.get('title') or job.get('filename') or source} (job {job['id']})…", file=sys.stderr)

        def progress(j):
            print(f"\r  {j.get('stage') or j['status']} {int(j.get('progress') or 0)}%   ", end="", file=sys.stderr)

        job = wait(job["id"], on_progress=progress)
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
        if job["status"] != "completed":
            raise ApiError(f"{source}: {job.get('error') or job['status']}")
        output(job, fmt, args.output if len(args.sources) == 1 else None)


def output(job: dict, fmt: str, path: str = None) -> None:
    if fmt in (None, "text"):
        text = readable(job)
        if path:
            Path(path).write_text(text + "\n")
            print(f"Saved {path}", file=sys.stderr)
        else:
            print(text)
        return
    data = request("GET", f"/api/jobs/{job['id']}/file/{fmt}", raw=True)
    if not path and fmt not in TEXT_FORMATS:
        path = f"{Path(job.get('title') or job['id']).name}.{fmt}"
    if path:
        Path(path).write_bytes(data)
        print(f"Saved {path}", file=sys.stderr)
    else:
        sys.stdout.write(data.decode("utf-8", "replace"))


def cmd_list(args) -> None:
    jobs = request("GET", "/api/jobs")[: args.limit]
    if not jobs:
        print("No transcriptions yet.")
        return
    for job in jobs:
        title = job.get("title") or job.get("filename") or job.get("url") or ""
        when = (job.get("createdAt") or "")[:10]
        length = clock((job.get("duration") or 0) * 1000)
        print(f"{job['id']}  {job['status']:<9}  {when}  {length}  {title}")


def cmd_get(args) -> None:
    job = request("GET", f"/api/jobs/{args.job_id}")
    if job["status"] != "completed":
        raise ApiError(f"Job {args.job_id} is {job['status']}" + (f": {job['error']}" if job.get("error") else ""))
    output(job, args.format, args.output)


def cmd_usage(args) -> None:
    usage = request("GET", "/api/usage")
    now = usage["thisMonth"]
    print(f"{usage['planName']} plan: {now['usedMinutes']} of {now['limitMinutes']} minutes used this month "
          f"({now['remainingMinutes']} left, resets {now['resetsOn']}).")
    for month in usage["months"]:
        if month["recordings"]:
            print(f"  {month['month']}: {month['minutes']} min, {month['recordings']} recording(s)")


# --------------------------------------------------------------------------- #
# MCP server (Model Context Protocol over stdio, JSON-RPC 2.0, one message per line)
# --------------------------------------------------------------------------- #

MCP_TOOLS = [
    {
        "name": "transcribe_file",
        "description": "Transcribe an audio or video file on this computer with Parlova and return the text. "
                       "Waits for the result (up to max_wait_seconds); if it isn't ready by then, returns the job id "
                       "to check later with get_transcript.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the audio or video file."},
                "language": {"type": "string", "description": "Spoken language code such as 'en', or 'auto' (default)."},
                "speakers": {"type": "boolean", "description": "Label who said what (Plus/Pro)."},
                "max_wait_seconds": {"type": "integer", "description": "How long to wait for the result (default 240)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "transcribe_youtube",
        "description": "Transcribe a YouTube video by its link and return the text. Waits like transcribe_file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "YouTube link."},
                "language": {"type": "string", "description": "Spoken language code, or 'auto' (default)."},
                "speakers": {"type": "boolean", "description": "Label who said what (Plus/Pro)."},
                "max_wait_seconds": {"type": "integer", "description": "How long to wait for the result (default 240)."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_transcript",
        "description": "Get a transcript by job id: readable text with timestamps and speakers, or a file format "
                       "(srt, vtt, csv, md, json, txt).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "format": {"type": "string", "enum": ["text", *TEXT_FORMATS], "description": "Default: text."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "list_transcripts",
        "description": "List recent Parlova transcriptions with their ids, status, dates and titles.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Default 20."}}},
    },
    {
        "name": "get_usage",
        "description": "Minutes of transcription used this month, the plan's limit, and recent monthly totals.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def mcp_transcribe(source: str, args: dict) -> str:
    job = submit(source, args.get("language") or "auto", bool(args.get("speakers")))
    job = wait(job["id"], timeout=float(args.get("max_wait_seconds") or 240))
    if job["status"] == "completed":
        title = job.get("title") or job.get("filename") or source
        return f"Transcript of {title} (job {job['id']}):\n\n{readable(job)}"
    if job["status"] in FINISHED:
        raise ApiError(job.get("error") or f"Transcription {job['status']}.")
    return (f"Still transcribing (job {job['id']}, {int(job.get('progress') or 0)}% done). "
            f"Call get_transcript with job_id '{job['id']}' in a minute.")


def mcp_call(name: str, args: dict) -> str:
    if name == "transcribe_file":
        return mcp_transcribe(args["path"], args)
    if name == "transcribe_youtube":
        return mcp_transcribe(args["url"], args)
    if name == "get_transcript":
        job = request("GET", f"/api/jobs/{args['job_id']}")
        if job["status"] != "completed":
            return f"Job {job['id']} is {job['status']}" + (f": {job['error']}" if job.get("error") else f" ({int(job.get('progress') or 0)}% done).")
        fmt = args.get("format") or "text"
        if fmt == "text":
            return readable(job)
        return request("GET", f"/api/jobs/{job['id']}/file/{fmt}", raw=True).decode("utf-8", "replace")
    if name == "list_transcripts":
        jobs = request("GET", "/api/jobs")[: int(args.get("limit") or 20)]
        return "\n".join(
            f"{j['id']} | {j['status']} | {(j.get('createdAt') or '')[:10]} | {j.get('title') or j.get('filename') or j.get('url')}"
            for j in jobs
        ) or "No transcriptions yet."
    if name == "get_usage":
        u = request("GET", "/api/usage")
        now = u["thisMonth"]
        lines = [f"{u['planName']} plan: {now['usedMinutes']} of {now['limitMinutes']} minutes used this month, "
                 f"{now['remainingMinutes']} left, resets {now['resetsOn']}."]
        lines += [f"{m['month']}: {m['minutes']} min ({m['recordings']} recordings)" for m in u["months"] if m["recordings"]]
        return "\n".join(lines)
    raise ApiError(f"Unknown tool: {name}")


def mcp_handle(message: dict):
    """Answer one JSON-RPC message. Returns the response, or None for notifications."""
    method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if msg_id is None:  # notifications (e.g. notifications/initialized) get no reply
        return None
    reply = {"jsonrpc": "2.0", "id": msg_id}
    if method == "initialize":
        reply["result"] = {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "parlova", "version": VERSION},
            "instructions": "Transcribe audio/video files and YouTube links with Parlova.",
        }
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {"tools": MCP_TOOLS}
    elif method == "tools/call":
        try:
            text = mcp_call(params.get("name", ""), params.get("arguments") or {})
            reply["result"] = {"content": [{"type": "text", "text": text}], "isError": False}
        except (ApiError, KeyError, ValueError) as exc:
            reply["result"] = {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
    else:
        reply["error"] = {"code": -32601, "message": f"Method not found: {method}"}
    return reply


def cmd_mcp_config(args) -> None:
    """Print a ready-to-paste MCP config for this computer (full paths, so GUI apps can find it).
    It contains no token: the MCP server uses the login saved by `parlova login`."""
    script = str(Path(__file__).resolve())
    python = sys.executable or "python3"
    if args.app == "claude-code":
        print(f'claude mcp add parlova --scope user -- "{python}" "{script}" mcp')
        return
    server = {"command": python, "args": [script, "mcp"]}
    if args.app == "vscode":
        config = {"servers": {"parlova": {"type": "stdio", **server}}}
    else:  # claude-desktop, cursor and most other apps use this shape
        config = {"mcpServers": {"parlova": server}}
    print(json.dumps(config, indent=2))
    if not os.environ.get("PARLOVA_TOKEN") and not load_config().get("token"):
        print("\nNote: you're not logged in yet. Run: parlova login", file=sys.stderr)


def cmd_mcp(args) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = mcp_handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="parlova", description="Transcribe recordings and YouTube links with Parlova.")
    parser.add_argument("--version", action="version", version=f"parlova {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="sign in through your browser")
    p.add_argument("--token", nargs="?", const="", default=None,
                   help="use an access token instead (for servers/CI); leave the value empty to be asked for it")
    p.add_argument("--no-browser", action="store_true", help="print the sign-in link instead of opening a browser")
    p.add_argument("--url", help="your Parlova address, if different")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="forget the saved token").set_defaults(func=cmd_logout)

    p = sub.add_parser("transcribe", help="transcribe files or YouTube links")
    p.add_argument("sources", nargs="+", metavar="FILE_OR_URL")
    p.add_argument("--speakers", action="store_true", help="label who said what (Plus/Pro)")
    p.add_argument("--language", default="auto", help="spoken language code, e.g. en (default: auto)")
    p.add_argument("--format", choices=["text", *FORMATS], default="text", help="output format (default: text)")
    p.add_argument("-o", "--output", help="save to this file instead of printing")
    p.add_argument("--no-wait", action="store_true", help="just start the job and print its id")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("list", help="your recent transcriptions")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", help="print or download a finished transcript")
    p.add_argument("job_id")
    p.add_argument("--format", choices=["text", *FORMATS], default="text")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_get)

    sub.add_parser("usage", help="minutes used this month").set_defaults(func=cmd_usage)
    p = sub.add_parser("mcp-config", help="print the MCP config to paste into your AI app")
    p.add_argument("--app", choices=["claude-desktop", "claude-code", "cursor", "vscode"], default="claude-desktop")
    p.set_defaults(func=cmd_mcp_config)
    sub.add_parser("mcp", help="run as an MCP server over stdio (for AI apps)").set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ApiError as exc:
        print(f"parlova: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
