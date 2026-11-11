# Cuadrilla

A crew of computer-use agents. Each session owns one or more mice. Several sessions work at once. They do not share windows, and they do not take the desktop pointer. Site: https://matthewrball.github.io/cuadrilla/

The name is the Spanish word for a work crew. CUA is the start of the word. The mark is three pointers: Violet, Amber, Mint. A session's mice keep those inks, in order, through Slate.

`cuadrilla demo --keep` opens Calculator and a scratch TextEdit file in the background and leaves the cursors up. Then `cuadrilla press 1` and `cuadrilla type hello`. `cuadrilla stop` closes every crew.

## Engine

Input still goes through [Cua Driver](https://github.com/trycua/cua), forked at <https://github.com/matthewrball/cua>. The local sparse checkout is `../cuadrilla-upstream`. Cuadrilla does not reimplement SkyLight, accessibility, or screenshots. That is the heavy part, and it is already built.

What Cua does not do, and Cuadrilla does:

| Problem | Cuadrilla |
| --- | --- |
| One pointer for every agent | One overlay cursor per mouse. A session can open up to 8. |
| Agents walk into each other's apps | App lease. A conflict returns `lease_conflict` and a cmux notice. |
| Background typing can return ok after sending nothing | `type_text` / `set_value` must show up in the next window read. |
| Foreground fallback grabs the machine | No foreground delivery. `when_idle` waits, then refuses. |
| Desktop screenshot loop | Window tree only, screenshots stripped. |
| Agents do not know the tool exists | `cuadrilla install` registers the skill and MCP. |

Overlapping HID (two apps receiving clicks in the same instant) still needs a cua-driver build that contains `CUA_DRIVER_PARALLEL_BACKGROUND` ([trycua/cua#4231](https://github.com/trycua/cua/pull/4231), unmerged). Cuadrilla already overlaps scheduling across apps and keeps same-app actions in order. `cuadrilla doctor` reports whether the installed driver can overlap HID.

## Install

On a machine that already has [cua-driver](https://cua.ai/docs/cua-driver/guides/install):

```bash
git clone --depth 1 https://github.com/matthewrball/cuadrilla.git
./cuadrilla/install.sh
```

From a checkout, `./install.sh` is the same command. It copies the package to `~/.cuadrilla`, links `~/.local/bin/cuadrilla`, writes the skill where Claude, Codex, Hermes, and omp look, adds an MCP server named `cuadrilla`, and starts a LaunchAgent. Restart an already-open agent so it reconnects MCP. The skill is on disk immediately.

```bash
cuadrilla doctor
cuadrilla session open --mice 2
cuadrilla status
cuadrilla session close cw-xxxxxx
```

Inside cmux the sidebar pill is `cuadrilla`. The CLI and MCP process publish it. The background service cannot: cmux only accepts processes started inside the app. `session_current` reuses the pane's crew.

## Act

```bash
cuadrilla windows
cuadrilla observe cw-abc m1 --pid 123 --window 45
cuadrilla act cw-abc m1 click '{"pid":123,"window_id":45,"element_token":"..."}'
```

`lease_scope` defaults to `app`. Two mice in one session still cannot enter the same app unless the call passes `"lease_scope":"window"` and the windows differ.
