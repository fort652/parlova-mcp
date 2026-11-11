# Parlova MCP: transcribe audio, video and YouTube from Claude, Cursor and VS Code

Parlova lets your AI assistant turn local recordings and YouTube videos into accurate, timestamped transcripts, with
speakers labelled, so you can summarise, search and quote them without leaving the chat.

It runs as a local MCP server (`parlova mcp`) that talks to the [Parlova](https://parlova.co.za) transcription API, powered
by ElevenLabs Scribe v2: 90+ languages with automatic detection, including Afrikaans and isiZulu, even when languages are
mixed in one recording. It's also a handy command-line tool.

## Prerequisites

- Python 3.8 or newer. There are no dependencies; it's a single file using only the standard library.
- A Parlova **Plus** ($5/month) or **Pro** ($10/month) account. Developer access (API, CLI, MCP) isn't on the Free plan.
  [Try the quality free on the web first](https://parlova.co.za).

## Quickstart (about 1 minute)

**1. Install** (pick one)

```sh
uv tool install parlova          # or: pipx install parlova
pip install --user parlova

# no Python packaging tools? (macOS / Linux)
curl -fsSL https://parlova.co.za/cli/install.sh | sh
# Windows PowerShell
irm https://parlova.co.za/cli/install.ps1 | iex
```

**2. Sign in.** Your browser opens; check the code and click Approve.

```sh
parlova login
```

**3. Connect your AI app**

```sh
parlova mcp-config                    # Claude Desktop → paste into claude_desktop_config.json
parlova mcp-config --app cursor       # Cursor → ~/.cursor/mcp.json
parlova mcp-config --app vscode       # VS Code → .vscode/mcp.json
parlova mcp-config --app claude-code  # Claude Code → prints a `claude mcp add …` command to run
```

`mcp-config` prints the config with full paths for your machine. Desktop apps don't see your shell's PATH, so a bare
`"command": "parlova"` usually fails. Restart the app after editing its config.

If you have [uv](https://docs.astral.sh/uv/), this config works anywhere without installing first:

```json
{
  "mcpServers": {
    "parlova": { "command": "uvx", "args": ["parlova", "mcp"] }
  }
}
```

(Run `uvx parlova login` once first, or add `"env": {"PARLOVA_TOKEN": "…"}` with a token from Settings → Developers.)

## Tools

| Tool | What it does | Arguments |
|---|---|---|
| `transcribe_file` | Transcribes an audio or video file on your computer and returns the text. Waits for the result; if it takes longer, returns a job id. | `path` (absolute), `language` (`auto` default, or e.g. `af`, `en`), `speakers` (bool), `max_wait_seconds` (default 240) |
| `transcribe_youtube` | Same, for a YouTube link | `url`, `language`, `speakers`, `max_wait_seconds` |
| `get_transcript` | Fetches a finished transcript by job id | `job_id`, `format`: `text` (readable, timestamps + speakers), `txt`, `srt`, `vtt`, `csv`, `md`, `json` |
| `list_transcripts` | Recent transcriptions with ids, status, dates and titles | `limit` (default 20) |
| `get_usage` | Minutes used this month, your plan's limit, recent months | — |

Speaker labels need Plus or Pro. `md` and `json` downloads are Pro. Plan limits: Plus is 10 hours a month with recordings up
to 1 hour; Pro is 30 hours a month with recordings up to 3 hours.

## Example prompts

1. "Transcribe ~/Downloads/team-meeting.m4a with speakers and list the action items with who owns each one."
2. "Transcribe this YouTube talk and give me the five key ideas with timestamps: https://www.youtube.com/watch?v=…"
3. "Transcribe ~/Desktop/interview.mp3. It's mostly Afrikaans with some English. Then translate it to English, keeping the
   speaker labels."
4. "Get the SRT subtitles for my last transcript and save them next to the video as talk.srt."
5. "How many transcription minutes have I used this month, and what were my last five transcripts about?"

## Command line

```sh
parlova transcribe meeting.m4a --speakers
parlova transcribe "https://youtu.be/…" --format srt -o talk.srt
parlova list
parlova get <job-id> --format vtt
parlova usage
```

For servers and CI: `parlova login --token` or set `PARLOVA_TOKEN`.

## Privacy

Audio is only used to make your transcript. Uploaded audio is deleted once it's transcribed, and our speech providers don't
store it or train on it. You can delete any transcript at any time. [Privacy policy](https://parlova.co.za/privacy)

## License

MIT
