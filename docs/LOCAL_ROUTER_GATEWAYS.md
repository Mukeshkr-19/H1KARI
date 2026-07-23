# Local Router Gateways

H1KARI can use [OmniRoute](https://github.com/diegosouzapw/OmniRoute) and
[9Router](https://github.com/decolua/9router) through their OpenAI-compatible
local APIs. Both projects run separately from H1KARI; their source and
dependencies are not vendored into this repository.

These gateways aggregate access to upstream model providers. Advertised free
capacity is not a token grant from H1KARI, may overlap between the two routers,
and remains subject to each upstream provider's quota, eligibility, retention,
training, availability, and terms.

## Safe defaults

H1KARI accepts gateway endpoints only when all of the following are true:

- the URL uses plain HTTP on the numeric loopback address `127.0.0.1` or `::1`;
- an explicit TCP port is present;
- the path is exactly `/v1`;
- the URL has no embedded credentials, query, or fragment;
- a local gateway bearer key is configured.

The two projects default to the same port upstream, so H1KARI assigns distinct
defaults when both are used:

| Gateway | H1KARI default | Priority |
|---|---|---|
| OmniRoute | `http://127.0.0.1:20128/v1` | first |
| 9Router | `http://127.0.0.1:20129/v1` | second |

Configure the second application itself to listen on port `20129`, or override
either H1KARI URL with its matching environment variable. H1KARI does not start,
install, update, or administer either gateway.

## H1KARI configuration

Copy the relevant placeholders from the repository's environment-variable
template into your ignored local environment configuration. Never commit real
keys.

OmniRoute uses its built-in aliases by default:

```text
OMNIROUTE_API_KEY=your-local-gateway-key
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_FAST_MODEL=auto/fast
OMNIROUTE_BALANCED_MODEL=auto
OMNIROUTE_SMART_MODEL=auto/smart
OMNIROUTE_VISION_MODEL=provider/exact-image-capable-model-or-combo
```

9Router routes through a user-created combo. The defaults assume a combo named
`free-forever`; change the model variables to the exact combo configured in the
local 9Router application.

```text
NINEROUTER_API_KEY=your-local-gateway-key
NINEROUTER_BASE_URL=http://127.0.0.1:20129/v1
NINEROUTER_FAST_MODEL=free-forever
NINEROUTER_BALANCED_MODEL=free-forever
NINEROUTER_SMART_MODEL=free-forever
NINEROUTER_VISION_MODEL=exact-image-capable-combo
```

Unsetting a gateway key disables it in H1KARI. No reachability probe or network
request occurs during import or router construction.

## Privacy boundary

The connection from H1KARI to the gateway stays on loopback, but the selected
gateway normally forwards message text to an upstream provider. That is cloud
egress unless the gateway selects a truly local model. H1KARI therefore treats
each gateway as a provider destination for approval and audit purposes.

Text routing never carries Phase 4 images. Cloud Vision uses a separate bounded
adapter only after the user selects **Cloud Vision**, acknowledges the visible
egress disclosure, starts an accepted-handoff analysis, and explicitly captures
or selects one validated image. Older clients that omit the processing mode
remain private-local and cannot trigger cloud egress.

The vision adapter requires the separate `*_VISION_MODEL` setting. It never
reuses `auto`, `auto/smart`, or `free-forever`, because those routes may resolve
to a text-only model. OmniRoute is tried first when both exact routes are
configured. 9Router is used only when its operator has created and named an
image-capable combo. No reachability or capability probe runs during import,
construction, CLI, doctor, or server startup.

The validated image is encoded only inside the bounded loopback gateway request;
it never enters the HIKARI WebSocket JSON protocol, logs, audit metadata, or
persistent state. The gateway normally forwards it upstream, so the visible
disclosure applies to every configured route. Upstream retention, training,
quota, and privacy policies remain the operator's responsibility. Unsetting the
vision model variables disables cloud vision without affecting text routing.
