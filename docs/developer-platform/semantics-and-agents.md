# Semantic applications and agent access

## Model

GTK/AT-SPI remains the canonical accessible representation of visible
controls. Luma Semantics adds stable domain objects and operations that are not
reliably derivable from pixels or labels. A Filer selection, calendar event,
conversation, or document is a semantic object; open, move, join, send, and
export are semantic actions.

Actions are presentation-free. UI controls, menus, shortcuts, accessibility,
Live Extensions, automation, and agents reference the same stable action ID.

## Access boundary

The per-user Luma Semantic Broker is the only supported external discovery and
invocation boundary. Applications publish structured surfaces to it over the
session bus; agents never connect directly to an application's provider
endpoint. The broker mediates:

- current-app versus all-app observation;
- public/private/sensitive object visibility;
- read versus invoke access;
- explicit scope and expiration;
- consequential/destructive/security-sensitive confirmation;
- lock-screen redaction;
- revocation, rate limits, and audit history.

Secret fields are never serialized. Password managers and authentication
surfaces expose only the minimum state necessary to complete their own trusted
flow. An AI protocol bridge is a broker client; it is not the system ABI.

Destructive and security-sensitive invocation are separate scopes. Possessing
general low-risk or consequential access never authorizes them, and every such
operation still requires an explicit confirmation. Secret descendants are
omitted recursively even when their parent object is visible.

### Caller identity

The broker derives the caller's Unix UID and PID from the session bus. A string
supplied by the caller is never accepted as identity evidence.

- Flatpak callers are bound to the app ID in the process's `.flatpak-info`.
- Managed native applications and providers are bound to a standardized
  systemd application scope whose app ID resolves to an installed desktop
  entry. Merely owning an application-shaped D-Bus name is not accepted as
  identity evidence.
- Native clients may receive an explicitly approved, expiring grant for the
  current login session, but never a persistent grant. Native clients outside
  a recognized application scope are labeled **Unverified native
  application**.
- Another Unix user, a mismatched provider ID, an unknown sender, or an
  unreadable identity fails closed.

Flatpak packaging must explicitly allow access to
`org.projectluma.SemanticBroker1`. That bus permission permits contact with the
broker; it does not grant access to any application surface.

The broker does not claim to create a kernel security boundary between two
unsandboxed processes running as the same Unix user. Such processes already
share a broad host trust domain. Luma therefore keeps all native grants
session-only and recommends a sandbox for third-party agents that need a
durable identity or remembered permission.

### Grants and confirmation

Observation and invocation use distinct scopes. Public and private observation
are separate, as are low-risk, consequential, destructive, and
security-sensitive invocation. Invoking an action also requires observation
scope for the target object's privacy class, so guessing a private object ID
cannot bypass redaction.

Grants expire after 60 seconds to 30 days. Only a sandbox-derived application
identity may be remembered across broker restarts; native grants are always
session-only. Consequential, destructive, and
security-sensitive operations require a new broker-owned confirmation for each
invocation even when the associated invocation scope is present. Callers can
provide only plain text to the confirmation dialog; they cannot provide markup,
widgets, executable UI, or a command.

The broker returns no surface while the desktop is locked or when lock state is
unknown. Published data and actions are bounded by size, depth, count, and
validated schema. Publication counts are bounded globally and per application.
A provider result is also bounded before it is accepted.

### Audit and retention

The local audit records timestamp, authenticated client key, operation, target
identifier, decision, and reason. It deliberately excludes semantic payloads,
action parameters, provider results, document titles, and document contents.
It is user-private, size-bounded, and rotated. A client may retrieve only its
own records. Revocation applies immediately.

### Wire contract

The installed session service is `org.projectluma.SemanticBroker1`. Access and
action requests use caller-owned request objects and asynchronous responses so
the trusted confirmation UI never blocks D-Bus dispatch. Providers expose only
`org.projectluma.SemanticProvider1` and verify that every invocation comes from
the current unique owner of the broker name.

The initial action parameter vocabulary is deliberately small: boolean,
double, UTF-8 string, signed 64-bit integer, or a variant dictionary. Actions
that declare no parameter accept only the canonical empty dictionary. This is
version 0.1 and may be expanded through an explicit contract revision.

## Compatibility ladder

1. Existing apps: AT-SPI and vision fallback.
2. Structured apps: stable UI semantics and actions.
3. Domain apps: typed application objects.
4. Workflow apps: bounded high-level operations with declared effects.

Notes is the first reference integration. Its application root is public; each
note object is private. Create and save are low-risk, while deletion is
destructive and always receives a fresh broker confirmation. Note bodies are
not published as object fields.

## Developer inspection

The optional SDK package exposes the broker through `luma semantic`. It can
request an expiring session grant through the same broker-owned consent UI as
any other native client, list or read only the surfaces visible to that grant,
invoke an action through the normal confirmation path, revoke access, and read
its own payload-free audit entries. It has no privileged bypass and is not
installed by the base desktop or mobile application package.

```text
luma semantic request org.projectluma.Notes \
  --scope observe-public --scope observe-private --scope invoke-low-risk
luma semantic list
luma semantic invoke PUBLICATION notes notes.create --parameter '{}'
luma semantic audit
luma semantic revoke org.projectluma.Notes
```
