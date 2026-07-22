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
```

Unsetting a gateway key disables it in H1KARI. No reachability probe or network
request occurs during import or router construction.

## Privacy boundary

The connection from H1KARI to the gateway stays on loopback, but the selected
gateway normally forwards message text to an upstream provider. That is cloud
egress unless the gateway selects a truly local model. H1KARI therefore treats
each gateway as a provider destination for approval and audit purposes.

This integration carries the router's existing text message shape only. It is
not connected to Phase 4 camera frames, visual-transfer bytes, OCR buffers, or
image-description inputs. Image bytes never enter this JSON request path.
