# Remote MCP Deployment

NotebookLM MCP supports Streamable HTTP, but it is designed primarily for
local, single-user operation. It is not a secure, turnkey public connector.

## Security Boundary

The MCP HTTP endpoint has no built-in authentication, authorization, or TLS.
The server binds to loopback by default and refuses external binds unless
`NOTEBOOKLM_ALLOW_EXTERNAL_BIND=1` is set. That override only removes the bind
guard; it does not secure the endpoint.

Never expose `notebooklm-mcp` directly to the public internet. If remote access
is required:

1. Keep the MCP process bound to `127.0.0.1`.
2. Put an authenticated HTTPS gateway in front of it.
3. Restrict access to the owner of the active NotebookLM account.
4. Protect the host because it stores Google session data.

Safe local HTTP startup:

```bash
notebooklm-mcp \
  --transport http \
  --host 127.0.0.1 \
  --port 8000 \
  --path /mcp
```

This exposes `http://127.0.0.1:8000/mcp` and a local health endpoint at
`http://127.0.0.1:8000/health`.

## Authentication and Account Isolation

Remote operation involves two separate credentials:

- The gateway must authenticate the MCP client.
- `nlm login` must maintain the Google browser session used for NotebookLM.

The MCP server uses one process-wide NotebookLM profile. Every caller operates
the same Google account, and `nlm login switch <profile>` changes the account
for the whole server. An OAuth gateway controls access to the endpoint but does
not create per-user NotebookLM isolation.

Google can require interactive sign-in again at any time. Persistent local
browser profiles give the best recovery behavior. VPS and container
deployments may require manual authentication maintenance, especially when
storage is ephemeral or cookies are supplied only through environment
variables.

## Remote File Limitation

MCP file paths always refer to the filesystem of the machine running the
server:

```python
source_add(
    notebook_id="...",
    source_type="file",
    file_path="/path/on/server/document.pdf",
)
```

A browser or phone path is not uploaded automatically. Likewise,
`download_artifact(output_path=...)` writes to the server host; it does not
return a secure browser download URL. Remote file upload and download require a
separate, authenticated file-transfer layer.

Remote deployments are therefore best suited to URL, text, and Drive sources;
research; queries; Studio creation/status; and metadata operations.

## Deployment Guidance

- For personal access from another device, prefer a remote-control workflow
  that leaves the MCP server, browser profile, and local files on the same
  trusted computer.
- An advanced single-user connector can place an OAuth-capable HTTPS gateway
  in front of a loopback-only MCP process.
- Public multi-user, organization-wide, VPS, container, reverse-proxy, and
  tunnel deployments are not supported turnkey configurations.

The full repository guide is `docs/REMOTE_MCP.md`.
