# miti-hermes-plugin

Hermes Agent plugin that connects to **Miti IM** via an outbound WebSocket
long-connection. No public webhook endpoint required.

- Plugin version: `0.1.0`
- Requires: [`miti-agent-sdk>=0.1.0`](https://pypi.org/project/miti-agent-sdk/)

## Features

| Feature | Status |
|---------|--------|
| Direct messages (single-chat) | ✅ |
| Group @mention routing | ✅ |
| Image receive (`image` / `multimodal`, vision) | ✅ |
| Voice receive (server STT → text) | ✅ |
| Markdown reply (`stream_full` / 125) | ✅ |
| Typing indicator | — (Miti has none) |
| Image / file send (outbound) | Not supported |
| Cron / notification delivery | ✅ (via `MITI_HOME_CHANNEL`) |
| Allowlist by user ID | ✅ (plugin layer; single-chat) |
| Group @ without per-user Hermes pairing | ✅ (auto from pairing file) |

## Installation

```bash
# From a Git repo (production):
hermes plugins install github.com/yourorg/miti-hermes-plugin

# From local source (copies into ~/.hermes/plugins/ — bare paths are not accepted):
hermes plugins install "file:///path/to/miti-hermes-plugin"
```

`miti-agent-sdk` is installed automatically from PyPI on first gateway start.

## Setup

```bash
hermes gateway setup   # select Miti → enter App ID + App Secret
hermes gateway run
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `MITI_APP_ID` | ✅ | Agent App ID (from Miti → 连接智能体) |
| `MITI_APP_SECRET` | ✅ | Agent App Secret |
| `MITI_API_BASE_URL` | — | API base URL (default: `https://www.miti.chat/chat`) |
| `MITI_OWNER_USER_ID` | — | Optional override for group @ Gateway auth. If unset, the plugin uses the **only** user in `~/.hermes/pairing/miti-approved.json`. |
| `MITI_ALLOWED_USERS` | — | Plugin-layer allowlist (comma-separated); empty = allow all. Skipped for group @ when a group auth user is resolved. |
| `MITI_ALLOW_ALL_USERS` | — | `true` skips Hermes pairing for **all** Miti traffic (dev); makes group auth user unnecessary |
| `MITI_HOME_CHANNEL` | — | Default user ID for cron notifications |

### Group @ without `MITI_OWNER_USER_ID`

You do **not** need to set `MITI_OWNER_USER_ID` if either:

1. **Exactly one** Miti user is Hermes-paired (`hermes pairing approve miti …`) — the plugin auto-reads that user for group @ auth, or
2. **`MITI_ALLOW_ALL_USERS=true`** — Hermes pairing is skipped entirely.

Typical new-agent setup:

```bash
# 1. Configure credentials
export MITI_APP_ID="..."
export MITI_APP_SECRET="..."

# 2. Pair once (single DM to bot → approve code)
hermes pairing approve miti <code>

# 3. Restart — group @ works for all members, no MITI_OWNER_USER_ID needed
hermes gateway restart
```

Override with `MITI_OWNER_USER_ID` only when **multiple** users are paired and you need to pick one for group @ auth.

## How It Works

```
Miti user sends text or image(s)
    ↓ WebSocket push (miti-agent-sdk, msg_type text | image | multimodal)
MitiAdapter._dispatch_inbound
    ↓ (images) download images[].url → local cache
    ↓ handle_message(PHOTO + media_urls | TEXT)
Hermes LLM / vision inference
    ↓ adapter.send(chat_id, reply)  — stream_full Markdown (125)
POST /agent/v1/messages/send (miti-agent-sdk)
    ↓
Miti user receives reply
```

Pure image messages (no user text) use default prompts: single image
`请描述这张图片的内容。`; multiple images `请描述这些图片的内容。`

**Image download:** Miti object URLs (`*.miti.chat`) are fetched via a trusted
download path (bypasses Hermes SSRF when DNS resolves to Clash fake-ip
`198.18.x`). Other CDN URLs still use Hermes `cache_image_from_url`.

### chat_id convention

| Context | chat_id format | Example |
|---------|---------------|---------|
| Direct message | `{user_id}` | `u_abc123` |
| Group @mention | `group:{group_id}` | `group:sg_xyz789` |

## Local Development

Use an **editable install** of the SDK so changes take effect immediately
without reinstalling anything:

```bash
# 1. Install SDK from local source into Hermes' Python environment
~/.hermes/hermes-agent/venv/bin/python -m pip install -e /path/to/pai/miti-agent-sdk

# 2. Symlink plugin (recommended — edits in pai/ take effect after gateway restart)
ln -sf /path/to/pai/miti-hermes-plugin ~/.hermes/plugins/miti-platform
hermes plugins enable miti-platform

# Alternative: copy install via file:// (edits in pai/ do NOT sync automatically)
# hermes plugins install "file:///path/to/pai/miti-hermes-plugin"

# 3. Point at local appserver
export MITI_APP_ID="your_app_id"
export MITI_APP_SECRET="your_app_secret"
export MITI_API_BASE_URL="http://localhost:10006/chat"
export MITI_ALLOW_ALL_USERS="true"
hermes gateway run
```

Modifying SDK source under `pai/miti-agent-sdk/src/` takes effect on the
next `hermes gateway restart` — no reinstall needed.

### Tests

```bash
cd miti-hermes-plugin
python -m pytest tests/ --confcutdir=tests -q
```

## Version Management & Upgrades

Both projects follow [Semantic Versioning](https://semver.org/):

| Change type | miti-agent-sdk | miti-hermes-plugin |
|-------------|---------------|-------------------|
| Bug fix, no API change | PATCH (0.1.x) | PATCH (0.1.x) |
| New feature, backward-compatible | MINOR (0.x.0) | MINOR (0.x.0) |
| Breaking API change | MAJOR (x.0.0) | MAJOR (x.0.0) |

The minimum required SDK version is declared in one place in `adapter.py`:

```python
_SDK_REQUIRE = "miti-agent-sdk>=0.1.0"
```

When the plugin depends on a new SDK feature, bump this value and the
`dependencies` entry in `plugin.yaml` together.

### Upgrade scenarios

**SDK only (bug fix or new feature, plugin unchanged):**

```bash
pip install --upgrade miti-agent-sdk
hermes gateway restart
```

**Plugin update (e.g. image send support added):**

```bash
hermes plugins update miti-platform
hermes gateway restart
```

**Breaking SDK change (MAJOR bump):**

```bash
# 1. Upgrade SDK
pip install "miti-agent-sdk>=1.0.0"
# 2. Upgrade plugin (which bumps _SDK_REQUIRE and plugin.yaml dependencies)
hermes plugins update miti-platform
hermes gateway restart
```

**Check currently installed versions:**

```bash
pip show miti-agent-sdk           # SDK version
hermes plugins list               # plugin version (from plugin.yaml)
```
