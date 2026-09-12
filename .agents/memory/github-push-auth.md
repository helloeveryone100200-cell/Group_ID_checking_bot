---
name: GitHub push authentication
description: How to proceed when GitHub OAuth remains unconnected during a repository push.
---

When a user explicitly chooses the secret-based GitHub route, use a protected
Replit secret and validate it against GitHub before attempting a push. Never
put the token in a remote URL, project file, logs, or chat.

**Why:** A GitHub connector proposal can remain `not_setup` when the user does
not approve the OAuth card, but a user-approved secret can still support the
requested repository operation.

**How to apply:** Prefer GitHub OAuth first. If the user explicitly requests a
secret route, request it through the secrets flow, validate authentication
without printing the value, and use a temporary askpass mechanism that is
removed after the push.