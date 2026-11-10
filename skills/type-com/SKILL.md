---
name: type-com
description: Work in a Type.com workspace from any MCP-capable agent through the hosted Type.com MCP server. Use when the user wants to search or reply to Type.com threads, manage Spaces and Channels, read or write Type.com documents and skills, build or publish Type.com Apps, create or debug scheduled automations, or call integrations connected to their Type.com workspace.
---

# Type.com

Type.com is a shared workspace where people and AI agents work together in Spaces, Channels and threads, with connected integrations, reusable skills, apps and scheduled automations. This skill teaches an agent to use a Type.com workspace through the hosted Type.com MCP server.

## Connect

Type.com runs a hosted, OAuth-protected MCP server. No API key, CLI or local process is needed.

- Server URL: `https://api.type.com/mcp` (Streamable HTTP, `POST` only)
- Auth: OAuth 2.1 with dynamic client registration. Metadata: `https://api.type.com/.well-known/oauth-protected-resource/mcp`
- Claude one-click install: https://type.com/mcp/install
- ChatGPT: https://type.com/mcp/install?client=chatgpt
- Reference: https://docs.type.com/reference/type-mcp/

Example client config:

```json
{
  "mcpServers": {
    "type-com": { "type": "http", "url": "https://api.type.com/mcp" }
  }
}
```

Every call runs as the signed-in user, in the organization chosen at sign-in, with that user's existing permissions.

## First call

Call `type_whoami` to confirm the user and organization before doing anything else. If the server reports `authentication_required`, ask the user to finish the OAuth sign-in in their client.

## Pick the right tool

| Goal | Start with |
| --- | --- |
| Find something by topic | `type_search_workspace` (narrow `types` to `threads`, `docs`, `apps`, ...) |
| Latest activity | `type_threads_recent`, then `type_threads_batch` or `type_thread_get` |
| Reply to a thread | `type_thread_reply` (`replyMode: "chat"` for teammates, `"prompt"` to ask the thread's AI) |
| Spaces and Channels | `type_spaces_list`, then `type_channels_list` with a Space where `isMember` is true |
| Documents | `type_documents_list`, `type_documents_pull`, `type_documents_push` |
| Skills | `type_skills_list`, `type_skills_inspect`, `type_skills_push` |
| Apps | `type_apps_list`, `type_apps_scaffold`, `type_apps_prepare`, `type_apps_publish` |
| Scheduled work | `type_scheduled_actions_*`, `type_synced_events_*`, `type_automations_runs` |
| Connected tools (Slack, Gmail, GitHub, Stripe, ...) | `type_integrations_list`, `type_integrations_tools`, then `type_integrations_call` |

## Rules

1. Use stable IDs from discovery results. Never derive a Space or Channel ID from a name, slug or URL.
2. Input schemas are strict and reject unknown fields. Use the schemas the client discovers; do not guess field names.
3. Read tools are annotated read-only. Every other tool is a write: confirm with the user before creating, publishing, archiving, merging or deleting anything.
4. On `unexpected_error`, a write may or may not have committed. Read the current state before retrying.
5. Type.com MCP replies in existing threads but does not create new threads or read file attachments.

## Errors

Failed calls return `isError: true` with `{ "ok": false, "error": { "code", "message" } }`. Handle `integration_auth_required` by asking the user to connect the integration in Type.com (or use `type_integrations_connect`), and back off on `rate_limited` and `integration_rate_limited`.

## More skills

Browse the full Type.com skills library at https://type.com/library/skills.
