# Miti Platform Plugin — Installation Complete

Source: [github.com/stocki-ai/miti-hermes-plugin](https://github.com/stocki-ai/miti-hermes-plugin)

## Dependencies

This plugin requires **miti-agent-sdk**. It is installed automatically from
PyPI the first time you run the gateway.

If the Hermes Python venv has no `pip` (seen on some **Windows** installs),
the plugin bootstraps pip via `ensurepip` before installing the SDK. You can
also install manually:

```bash
# macOS / Linux
~/.hermes/hermes-agent/venv/bin/python -m ensurepip --upgrade
~/.hermes/hermes-agent/venv/bin/python -m pip install miti-agent-sdk

# Windows PowerShell
$py = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"
& $py -m ensurepip --upgrade
& $py -m pip install miti-agent-sdk
```

## Enable the plugin

Hermes plugins are **opt-in** — install does not load the plugin until it is enabled.

```bash
# Skip this if you installed with: hermes plugins install stocki-ai/miti-hermes-plugin --enable
hermes plugins enable miti-platform
```

## Next Step: Configure Your Agent

```bash
hermes gateway setup
```

Select **Miti** and enter:
- **App ID** — shown after creating an agent in the Miti app
- **App Secret** — visible anytime on the agent detail page

### How to get App ID and App Secret

1. Open the **Miti** app
2. Go to the **Discovery** page (发现)
3. Tap the **top-right menu** → **连接智能体** (Connect Agent)
4. Fill in the agent name and tap **Create**
5. Copy the **App ID** and **App Secret** from the detail page

### Start the gateway

```bash
hermes gateway run
```

**Environment file (if not using `gateway setup`):** edit Hermes `.env` — macOS/Linux `~/.hermes/.env`, Windows `%LOCALAPPDATA%\hermes\.env` (`hermes config env-path`).

Send a direct message to your bot in Miti — Hermes will respond.

### Upgrade plugin

```bash
hermes plugins update miti-platform
hermes gateway restart
```

---

## Local Development

Install the SDK from source in editable mode (same Python env as Hermes):

```bash
pip install -e /path/to/miti-agent-sdk
```

Point at a local appserver:

```bash
export MITI_API_BASE_URL="http://localhost:10006/chat"
hermes gateway run
```

---

## Check version

```bash
hermes plugins list    # Version column = plugin.yaml version
```

Plugin internal name is **`miti-platform`**. SDK version: `pip show miti-agent-sdk` (in Hermes venv).

## Uninstall

```bash
hermes gateway stop
hermes plugins remove miti-platform
hermes gateway restart
```

On Windows, if `remove` fails with `WinError 5`, stop the gateway and delete
`%LOCALAPPDATA%\hermes\plugins\miti-platform\` manually (see README).
