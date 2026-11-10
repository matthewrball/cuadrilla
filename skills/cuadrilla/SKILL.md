---
name: cuadrilla
description: Drive native apps as a crew. Each Cuadrilla session can own several mice, and several sessions can work at once without sharing windows or taking the desktop pointer. Use instead of cua-driver or computer-use when more than one agent is on the machine, when the user must keep working, or when a cmux pane should show the crew.
version: 0.1.0
metadata:
  openclaw:
    requires:
      bins:
        - cuadrilla
        - cua-driver
---

# Cuadrilla

Cuadrilla is the crew layer on the Cua engine (`cua-driver`). It does not replace window input. It stops agents from taking the desktop, from reporting a false ok, and from driving the same app.

## When to use it

Use Cuadrilla for GUI work on this machine. Do not call `cua-driver` or a desktop computer-use tool for the same task. Those take one pointer and will fight the crew.

Use an app's API, MCP server, or CLI when one exists. The GUI is the fallback.

## Crew

1. `session_current` (MCP) or `cuadrilla session current`. Inside cmux this reuses the pane's crew.
2. One mouse (`m1`) is enough for a single app. Add mice with `mice_add` when this session should work in several apps at once. A session holds at most 8 mice.
3. Another agent opens its own session. It gets its own mice. It cannot act in an app you have leased.

Each mouse is a cua-driver session, so each has its own overlay cursor.

## Act

1. `list_windows`. Do not screenshot the desktop.
2. `observe` the one window you will touch. `include_screenshot` stays off unless the tree has no control for the target.
3. `act` with `pid`, `window_id`, and either `element_token` or `x`/`y` from that observe. Name the mouse.

```bash
cuadrilla act cw-abc m1 click '{"pid":123,"window_id":45,"element_token":"..."}'
```

`act` leases the app for that mouse. A second mouse, even in this session, gets `lease_conflict` and must pick another app. Pass `"lease_scope":"window"` only when two mice must work in different windows of one app.

Delivery is `background`. `when_idle` waits until the user has stopped typing, then gives up. There is no focus-stealing mode.

## Trust the window, not ok

If `act` returns `unconfirmed`, the click or text did not show up. Observe again. Do not repeat the same action. cua-driver has returned ok for background typing that sent nothing.

## cmux

The daemon sets the sidebar pill `cuadrilla` (`N crews · M mice · apps`). Conflicts notify the pane without a desktop banner. Do not clear that pill unless the crew is done.

## Stop

`session_close` when the GUI work is finished. Do not `cua-driver stop`. That daemon is shared.
