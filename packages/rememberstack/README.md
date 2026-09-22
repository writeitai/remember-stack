# rememberstack (RETIRED)

> **Notice:** Version 0.17.0 exists only to move existing installations to
> `remember`. This project receives no further releases and is archived on PyPI
> after the transition publication, per Architecture Decision D108.

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
docker pull ghcr.io/writeitai/remember-stack:0.17.0
```

For more information, see the official documentation at [remember.dev/docs](https://remember.dev/docs).
