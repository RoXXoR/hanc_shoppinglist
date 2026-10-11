# HANC Shopping List

**H**ome **A**ssistant **N**ext**c**loud — a Home Assistant custom integration for the
[Nextcloud Shopping List app](https://github.com/otherworld-dev/Shopping-List).

Adds every selected shopping list as a native **`todo` entity** (add, check, uncheck,
rename, reorder, delete) plus a **counts sensor** with full item details, and keeps both
always in sync with Nextcloud.

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Validate](https://github.com/roxxor/hanc_shoppinglist/actions/workflows/validate.yml/badge.svg)](https://github.com/roxxor/hanc_shoppinglist/actions/workflows/validate.yml)
[![Lint & Test](https://github.com/roxxor/hanc_shoppinglist/actions/workflows/tests.yml/badge.svg)](https://github.com/roxxor/hanc_shoppinglist/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- 🛒 **Native `todo` entity per list** — add items, check/uncheck, rename, drag to
  reorder, delete. Works with the built-in To-do card, `todo.add_item` etc.
- 🔢 **Counts sensor per list** — state is the number of open items; attributes carry
  `total`, `checked` and every item with quantity, unit, shop area, who added/checked it.
- 🔄 **Always in sync** — polls Nextcloud (default every 30 s, 10–600 s configurable)
  and refreshes immediately after every change made in Home Assistant.
- 🔐 **Two auth methods** — Nextcloud account with an app password (full API), or a
  **public share link** (including password-protected and read-only links).
- ✅ **Services** — `hanc_shopping_list.add_item` (name, quantity, unit, area, checked),
  `hanc_shopping_list.clear_checked`, `hanc_shopping_list.uncheck_all`.
- 🔁 **Reauth flow** when the app password is revoked, plus a proper options flow for
  list selection and polling interval.
- 🧾 **Diagnostics** download with credentials redacted.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Integrations* → *Custom repositories*
2. Repository URL: `https://github.com/roxxor/hanc_shoppinglist`, category **Integration**
3. Install **HANC Shopping List** and restart Home Assistant.

### Manual

Copy `custom_components/hanc_shopping_list` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

1. *Settings → Devices & services → Add integration → HANC Shopping List*
2. Enter your Nextcloud URL and pick an auth method:
   - **Nextcloud account** — username + an
     [app password](https://docs.nextcloud.com/server/latest/manual_manual.html#app-passwords)
     (Nextcloud → *Personal settings → App passwords*). Recommended.
   - **Public share link** — paste the share link of a list from the Shopping List app,
     plus the link password if the share is protected.
3. Choose which lists to import (all are preselected). Change the selection any time in
   the integration's **Options**.

Entities are grouped under a device per Nextcloud account/share; each list provides:

| Entity | Description |
| --- | --- |
| `todo.<list>` | The list itself (state = number of open items) |
| `sensor.<list>_counts` | Open item count, `total` / `checked` / `items` attributes |

Item summaries are rendered as `2 × Milk (kg)` — a leading `N × ` is parsed back as
quantity and a trailing `(unit)` as unit when you edit an item in Home Assistant.

## Services

```yaml
service: hanc_shopping_list.add_item
target:
  entity_id: todo.groceries
data:
  name: Milk
  quantity: "2"
  unit: l
  area: Supermarket   # optional, must match an existing shop area of that list
  checked: false
```

```yaml
service: hanc_shopping_list.clear_checked
target:
  entity_id: todo.groceries
```

```yaml
service: hanc_shopping_list.uncheck_all
target:
  entity_id: todo.groceries
```

> `clear_checked` and `uncheck_all` need an account connection — the public share API
> only supports per-item operations.

## Automation example

```yaml
automation:
  - alias: Add milk when the shopping list runs low
    trigger:
      - platform: state
        entity_id: sensor.groceries_counts
        to: "0"
    action:
      - service: hanc_shopping_list.add_item
        target:
          entity_id: todo.groceries
        data:
          name: Milk
```

## How sync works

The integration uses **polling** (default 30 s). Every write in Home Assistant is sent
to Nextcloud immediately and followed by a refresh, so the UI reflects the server's
answer (the Nextcloud app merges duplicate item names server-side). Changes made in the
Nextcloud app or on other devices appear at the next poll. Notify push is not usable
here: Nextcloud's push channel is bound to a browser/app session.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install ruff pytest pytest-asyncio pytest-homeassistant-custom-component
ruff check custom_components tests && ruff format custom_components tests
pytest
```

CI runs [hassfest](https://github.com/home-assistant/actions), the
[HACS validator](https://github.com/hacs/action), Ruff and pytest.

## License

[MIT](LICENSE)
