---
title: remember.dev API
description: The remember.dev account API for sign-in, organisations, projects, tokens, device login, billing, usage, settings, support, offboarding and hosted MCP sign-in, with authentication and error shapes.
applies_to: [remember.dev]
---

# remember.dev API

The remember.dev API manages everything around your memory: accounts,
organisations, projects, tokens, billing and closing projects. It does not
read or write memory. Memory requests go to your deployment's own hostname;
see the [HTTP API reference](http-api/index.md) and
[What remember.dev serves](../cloud/compatibility.md).

## Base URL

```text
https://remember.dev/app/api
```

Every path on this page is relative to it, for example
`https://remember.dev/app/api/v1/orgs`. This is also the default base URL
of `remember.CloudClient` and the token host to pass to `remember login`.

## Authentication

| Method | How | Used for |
|---|---|---|
| Browser session | The `__session` cookie set by `POST /v1/auth/login` or Google sign-in | Every route marked **session** |
| Control-plane token | `Authorization: Bearer umc_cp_…` | Only the routes marked **cp**; see [Tokens and sign-in](../cloud/tokens-and-sign-in.md#control-plane-tokens) |
| Deployment API token | `Authorization: Bearer umc_dp_…` | Only `DELETE /v1/api-tokens/self` |
| Hosted MCP access token | `Authorization: Bearer …` from the OAuth flow | Only `POST /mcp` |
| None | | Routes marked **public** |

Routes marked **session** also need a verified email address, except where
noted. Roles:

- **member**: any active member of the organisation;
- **owner**: an owner of the organisation.

## Errors

Error bodies are not uniform yet. Branch on the HTTP status first, then on
the body.

| Shape | Where |
|---|---|
| `{"detail": "<message>"}` | Most routes |
| `{"detail": {"code": "…", "message": "…", "retryable": true/false}}` | Control-plane tokens, browser credentials |
| `{"detail": {"code": "…"}}` | `GET /v1/organisations/{org_id}/deployments/{deployment_id}` |
| `{"detail": [{"loc": […], "msg": "…", "type": "…"}]}` | `422` request validation, on every route |
| `{"error": "…", "error_description": "…"}` | `POST /v1/device/token` (device-flow errors) and the OAuth routes |
| `{"detail": {"code": "dp.bridge_removed", "message": "…", "retryable": false, "request_id": "…"}}` | The retired `/dp/v1/…` path, with `X-Request-Id` |

Common statuses: `401` not signed in or bad token; `403` signed in but not
allowed (not a member, not an owner, email not verified, or the route is
outside a control-plane token's profile); `404` not found or not visible to
you; `409` conflict with the current state; `429` rate limited, sometimes
with `Retry-After`; `503` a dependency is unavailable.

## Health

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health/live` | public | Liveness: `{"status": "alive"}` |

## Accounts and sign-in

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/auth/register` | public | Create an account. Body `email`, `password` (8–256), optional `name`. Sends a verification email |
| `POST` | `/v1/auth/verify-email` | public | Body `token` from the email |
| `POST` | `/v1/auth/login` | public | Body `email`, `password`. Sets the session cookie |
| `POST` | `/v1/auth/google/login` | public | Returns `login_url` to start Google sign-in |
| `POST` | `/v1/auth/google/callback` | public | Body `code`, `state` from Google. Sets the session cookie |
| `POST` | `/v1/auth/token/refresh` | session cookie | Renews the session; returns `expires_in` |
| `POST` | `/v1/auth/logout` | session (verification not needed) | Signs out and ends the user's sessions everywhere |
| `GET` | `/v1/auth/me` | session (verification not needed) | The signed-in user: `user_id`, `email`, `name`, `email_verified` |
| `POST` | `/v1/auth/password-reset/request` | public | Body `email`. Sends a reset link |
| `POST` | `/v1/auth/password-reset/confirm` | public | Body `token`, `new_password` |

## Organisations

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs` | session | Organisations you belong to, with your role |
| `POST` | `/v1/orgs` | session | Create a team organisation you own. Body `name` (1–100), optional `slug` (≤63) |
| `GET` | `/v1/orgs/{org_id}` | session member, cp | `id`, `slug`, `name`, `kind` |
| `PATCH` | `/v1/orgs/{org_id}` | session owner | Rename. Body `name` |

## Members and invitations

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/members` | session member | Members with `role`, `state`, `email`, `name` |
| `POST` | `/v1/orgs/{org_id}/members` | session owner | Add an existing account. Body `email`, `role` (`MEMBER` or `OWNER`) |
| `GET` | `/v1/orgs/{org_id}/invites` | session owner | Invitations with `state` and `expires_at` |
| `POST` | `/v1/orgs/{org_id}/invites` | session owner | Invite. Body `email`, `role` (default `MEMBER`), optional `idempotency_key` |
| `POST` | `/v1/orgs/{org_id}/invites/{invite_id}/resend` | session owner | Resend; at most once a minute (`429` otherwise) |
| `POST` | `/v1/orgs/{org_id}/invites/{invite_id}/revoke` | session owner | Revoke |
| `POST` | `/v1/invites/accept` | session | Body `token` from the invitation email. The signed-in, verified email must match |

There is no route to remove a member or change a role.

## Projects

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/projects` | session member | Projects visible to you, plus `project_limit`, `slots_used`, `is_owner` |
| `POST` | `/v1/orgs/{org_id}/projects` | session owner | Create a project and its activation Checkout. Header `Idempotency-Key` (required, ≤160). Body `name` (≤100). Returns `project` and `checkout` with the Checkout `url` |
| `GET` | `/v1/orgs/{org_id}/projects/{project_id}` | session member | One project, with `activation_state`, `deployment`, `member_count`, `monthly_subcap_credits` |
| `PATCH` | `/v1/orgs/{org_id}/projects/{project_id}` | session owner | Rename. Body `name` (1–100). The slug does not change |
| `POST` | `/v1/orgs/{org_id}/projects/{project_id}/archive` | session owner | Archive a project with no deployment and no activation in progress |
| `POST` | `/v1/orgs/{org_id}/projects/{project_id}/members` | session owner | Assign a member. Body `user_id` |
| `DELETE` | `/v1/orgs/{org_id}/projects/{project_id}/members/{membership_id}` | session owner | Revoke an assignment |
| `PUT` | `/v1/orgs/{org_id}/projects/{project_id}/spend-cap` | session owner | Set or clear the monthly sub-cap. Body `monthly_subcap_credits` (≥0 or `null`) |
| `GET` | `/v1/orgs/{org_id}/projects/{project_id}/intake` | session member | Ingest counts for the project: totals, per-day buckets and failure reasons, with no document names. Query `days` (default 14) |

`POST /v1/orgs/{org_id}/projects` answers `409` when billing is not active,
when the Default project has not been activated, or when
`Project limit reached (n of m)`.

## Deployments

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/deployments` | session member, cp | Deployments of the projects visible to you |
| `GET` | `/v1/organisations/{org_id}/deployments/{deployment_id}` | session member, cp | One deployment. Header `X-Correlation-ID` (required) |

A deployment has `id`, `org_id`, `project_id`, `state` (`requested`,
`provisioning`, `active`, `blocked`, `recovering`, `disabled`),
`created_at`, `data_plane_hostname` and `data_plane_hostname_live`.

## Deployment API tokens

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/deployments/{deployment_id}/api-tokens` | session owner | Token metadata: `token_id`, `label`, `state`, `created_at`, `last_used_at`, `revoked_at`, `expires_at` |
| `POST` | `/v1/orgs/{org_id}/deployments/{deployment_id}/api-tokens` | session owner | Mint. Body `label` (1–100), optional `replaces_token_id`. Returns `secret` once, `data_plane_hostname`, `data_plane_hostname_live`, `expires_at`, `replaced_predecessor` |
| `DELETE` | `/v1/orgs/{org_id}/deployments/{deployment_id}/api-tokens/{token_id}` | session owner | Revoke. Repeating it is harmless |
| `DELETE` | `/v1/api-tokens/self` | deployment API token | Revoke the presenting token |

Minting answers `409` at 20 live tokens and `429` above 60 mints an hour.
An `expires_at` of `null` in a listing means the token predates expiry, not
that it never expires.

## Control-plane tokens

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/orgs/{org_id}/control-tokens` | session member | Mint. Body `label` (1–100), `profile` (`status:read`), optional `expires_in_days` (1–180). Returns `secret` once |
| `GET` | `/v1/orgs/{org_id}/control-tokens` | session member | Your tokens' metadata |
| `DELETE` | `/v1/orgs/{org_id}/control-tokens/{token_id}` | session member | Revoke |
| `DELETE` | `/v1/control-tokens/self` | cp | Revoke the presenting token |

## Device login

These routes implement `remember login` (OAuth 2.0 device authorization).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/device/authorize` | public | Start. Body optional `client_name` (≤64), `audience` (`deployment` default, or `control`). Returns `device_code`, `user_code`, `verification_uri`, `verification_uri_complete`, `expires_in` (900), `interval` (5) |
| `POST` | `/v1/device/token` | public | Poll. Body `grant_type` = `urn:ietf:params:oauth:grant-type:device_code`, `device_code`. `400` with `authorization_pending`, `slow_down`, `access_denied`, `expired_token` or `temporarily_unavailable` until approved; then the token |
| `POST` | `/v1/device/grants/preview` | session | Body `user_code`. What the grant asks for, and the organisations and deployments you may choose |
| `POST` | `/v1/device/grants/approve` | session owner (`deployment`), member (`control`) | Body `user_code`, `org_id`, and for `deployment` the `deployment_id`; optional `label` (≤100) |
| `POST` | `/v1/device/grants/deny` | session, same roles | Body `user_code`, `org_id` |

A `deployment` token response carries `access_token`, `token_id`,
`org_id`, `project_id`, `deployment_id`, `label`, `data_plane_hostname`,
`data_plane_hostname_live` and `expires_at`. A `control` response carries
`access_token`, `token_id`, `org_id`, `label`, `profile`,
`profile_version` and `expires_at`, and no deployment fields.

## Browser credentials

Used by the console to call a deployment directly.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/orgs/{org_id}/deployments/{deployment_id}/browser-credential` | session member | A read-only credential for the deployment, valid 10 minutes |
| `POST` | `/v1/orgs/{org_id}/deployments/{deployment_id}/browser-credential/ingest` | session owner or project member | An upload-only credential, valid 3 minutes |

A control-plane token cannot obtain either.

## Billing

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/pricing/rates` | public | The published rate card: `rate_version_id`, `currency`, `effective_from`, `charges`, `ordinary_retrieval_included`. `404` `pricing_not_published` when none is published |
| `GET` | `/v1/orgs/{org_id}/billing/purchase-options` | session member, cp | `currency`, `minimum_credits`, `maximum_credits`, `recommended_credits`, `suggested_credits`, `credits_per_currency_unit` |
| `POST` | `/v1/orgs/{org_id}/billing/checkout-session` | session owner | Start a Stripe Checkout. Body optional `amount_credits` (whole number), `save_payment_method`. Returns `checkout_session_id` and `url`. `400` `amount_BELOW_MINIMUM`, `amount_NOT_INTEGER` and similar |
| `GET` | `/v1/orgs/{org_id}/billing/status` | session member, cp | `billing_state` (`AWAITING_PAYMENT`, `ACTIVE`, `SUSPENDED`), `balance_credits`, `monthly_cap_credits` |
| `GET` | `/v1/orgs/{org_id}/billing/ledger` | session member, cp | Ledger entries, newest first. Query `limit` (1–200, default 50) |
| `GET` | `/v1/orgs/{org_id}/billing/grants` | session member, cp | Credit grants from purchases, with `status` |
| `GET` | `/v1/orgs/{org_id}/billing/statements` | session member, cp | Published statements |
| `GET` | `/v1/orgs/{org_id}/billing/statements/{statement_id}` | session member, cp | One statement with `line_items` and `closing_balance` |
| `PUT` | `/v1/orgs/{org_id}/billing/cap` | session owner | Body `monthly_cap_credits`. Raising or clearing an existing cap answers `403` |
| `GET` | `/v1/orgs/{org_id}/billing/auto-top-up` | session member, cp | Settings, `can_enable` and `disable_reason` |
| `PUT` | `/v1/orgs/{org_id}/billing/auto-top-up` | session owner | Body `is_enabled`, `threshold_credits`, `top_up_amount_credits`, `monthly_ceiling_cash`, optional `stripe_payment_method_id` |
| `POST` | `/v1/orgs/{org_id}/billing/auto-top-up/evaluate` | session owner | Run the auto top-up check now. Returns `status` (`skipped`, `pending`, `fulfilled`, `failed`, `needs_reconciliation`) and `detail` |

Auto top-up `PUT` errors: `threshold_invalid`, `top_up_amount_out_of_range`,
`ceiling_below_top_up`, `payment_method_required_to_enable`,
`live_tax_unavailable`, all `400`.

## Usage

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/usage` | session member | Usage for a period. Query optional `project_id`, `period_start`, `period_end` |

Owners get `scope: "organisation"` with every project and an `owner_money`
block (balance, reserved credits, cap, billing state). Members get
`scope: "visible_projects"` with only their projects. Each meter total
carries a `completeness` of `complete`, `partial`, `unavailable` or
`stale`.

## Spend safety

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/deployments/{deployment_id}/spend-safety/gate` | session member, cp | The provider-cost safeguard's current `decision`, `reason_code`, `is_parked` and ceiling |
| `GET` | `/v1/orgs/{org_id}/deployments/{deployment_id}/spend-safety/usage` | session member, cp | Estimated and settled provider cost in USD, with adjustments |

These describe remember.dev's provider-cost safeguard, not your credits.

## Settings and onboarding

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/settings` | session member | `diagnostics_opt_out`, `onboarding_state`, `alpha_terms_version` |
| `PUT` | `/v1/orgs/{org_id}/settings` | session owner | Body any of `diagnostics_opt_out`, `onboarding_state`, `alpha_terms_version` (≤64) |
| `GET` | `/v1/orgs/{org_id}/onboarding` | session member | Same as settings |
| `PUT` | `/v1/orgs/{org_id}/onboarding` | session owner | Same as settings |

## Support

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/orgs/{org_id}/support/records` | session member | The organisation's support records |
| `POST` | `/v1/orgs/{org_id}/support/records` | session member | Open one. Body `category` (1–64), `subject` (1–255), `body` (1–8000), optional `correlation_ref` (≤128) |

## Offboarding

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/orgs/{org_id}/projects/{project_id}/offboarding/request` | session owner | Start closing a project. Body optional `reason` (`customer_choice` default, `cancellation`), `read_export_window_days` (0–180, default 30) |
| `GET` | `/v1/orgs/{org_id}/projects/{project_id}/offboarding/status` | session owner | The offboarding record: `state`, `read_export_window_ends_at`, step timestamps, `deletion_evidence_ref`, `holds_summary`, `project_archived` |
| `POST` | `/v1/orgs/{org_id}/projects/{project_id}/offboarding/early-closure` | session owner | End the cancel window. Body `confirm_permanent_loss_of_unexported_data: true`, otherwise `400` |

Requesting again while a closure is open returns the existing record. The
`read_export_…` names are historical: the window is for cancelling, and
there is no export. See
[Closing a project](../cloud/leaving.md).

## Hosted MCP and OAuth

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/.well-known/oauth-protected-resource` | public | Protected-resource metadata for `/mcp` |
| `GET` | `/.well-known/oauth-authorization-server` | public | Authorization-server metadata: endpoints, `S256`, scope `remember.mcp` |
| `POST` | `/oauth/register` | public | Dynamic client registration. Body `client_name`, `redirect_uris` (`https`, or `http` on localhost) |
| `GET` | `/oauth/authorize` | public, then browser session | Start authorization. Query `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method=S256`, `state`, optional `resource` |
| `GET` | `/oauth/continue/{ticket}` | browser session | The project-choice page |
| `POST` | `/oauth/approve` | browser session | Form fields `ticket`, `project` |
| `POST` | `/oauth/token` | public | Form. `grant_type` `authorization_code` (with `code`, `redirect_uri`, `code_verifier`, `client_id`) or `refresh_token` (with `refresh_token`, `client_id`). Returns `access_token` (1 hour), `refresh_token`, `scope` |
| `POST` | `/mcp` | hosted MCP access token | MCP over HTTP (JSON-RPC): `initialize`, `tools/list`, `tools/call` |

The issuer is `https://remember.dev/app/api`, so the metadata documents are
at `https://remember.dev/app/api/.well-known/…`. See
[Hosted MCP](../cloud/hosted-mcp.md).

## Not covered here

The API also has routes that customers do not call: the Stripe webhook,
remember.dev's operator routes (credit grants, statement publication,
spend-safety policy and provider keys, the provider dispatch halt), the
spend lease (`/v1/spend/…`) and billing meter routes that deployments use,
administration routes, and the deprecated organisation API keys
(`/v1/orgs/{org_id}/api-keys`). They are omitted on purpose.
