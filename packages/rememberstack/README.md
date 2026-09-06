# rememberstack (DEPRECATED)

> **Notice:** The `rememberstack` PyPI distribution is deprecated as of version 0.17.0 per Architecture Decisions D108 and D109.

## Migration Guide

### 1. Python SDK & Platform CLI
For client application development, scripts, and local MCP agent usage, please migrate to the canonical **`remember`** distribution on PyPI:

```bash
pip install remember
# or using uv:
uv add remember
```

Import interfaces remain fully compatible:
```python
from remember import RememberClient, MemoryClient, Client, CloudClient
```

### 2. Self-Hosted Server Deployments
Engine server deployments are distributed container-first via GitHub Packages and Docker Compose:

```bash
docker pull ghcr.io/writeitai/remember-stack:v0.17.0
```

For more information, see the official documentation at [remember.dev/docs](https://remember.dev/docs).
