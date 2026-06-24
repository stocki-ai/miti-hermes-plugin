# Miti Platform Plugin — Installation Complete

## Dependencies

This plugin requires **miti-agent-sdk**. It will be installed automatically from
PyPI the first time you run the gateway. To install it now:

```bash
pip install miti-agent-sdk
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

Send a direct message to your bot in Miti — Hermes will respond.

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
