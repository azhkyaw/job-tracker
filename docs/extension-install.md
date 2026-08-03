# Installing the extension on a new device

`README.md` step 5 only covers the *first* install — Developer mode → Load
unpacked → paste the token that signup just showed you. This is the missing
half: adding the extension on a **second** machine for an account that
already exists. Read the token section before you click anything in
Settings; it's the one step here that can break a device you're not sitting
at.

## Prerequisite: a server this device can reach

The extension talks to a server, not to a database directly. Before
installing the extension, this machine needs `python -m pipeline.cli serve`
reachable from it — normally that means running the server locally on this
same machine, pointed at the same database the other device uses.

If the dev DB is still local Docker Postgres on one machine only, get it onto
managed Postgres first (`docs/windows-dev.md` → **Managed Postgres**) — that
section covers copying `.env` and getting a second machine's server talking
to the shared database. Come back here once `cli status` succeeds on the new
machine.

If instead you want the new device's *browser* to point at the *first*
device's server over LAN (rather than running its own local server), see
[Non-localhost API base](#non-localhost-api-base) below — it needs an extra
permission grant `chrome://extensions` alone won't give you.

## Get the extension loaded

1. `git clone` (or pull) this repo onto the new machine — the extension isn't
   a separate distributable, it's the `extension/` folder here.
2. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select the `extension/` folder.
3. Open the extension's **Options** page (right-click the toolbar icon →
   Options, or the "Details" link on `chrome://extensions`).

## The token — read this before Settings

The extension authenticates with a single **API token**, and the server
stores exactly **one token per account** (`users.api_token_hash`, one
column — see `migrations/003_multi_tenant.sql`). There is no per-device
token. That means one of two paths, and the second one has a real side
effect:

- **You still have the original token** (password manager, notes, wherever
  it was saved when Settings showed it). Paste it into this device's Options
  page, API base `http://127.0.0.1:8000` (or wherever this device's server
  listens). Nothing else to do — the first device keeps working, since the
  server checks the token against the DB by hash, not by device.

- **You don't have it.** The only way to get a usable token is
  `/settings` → **New API token**. That mints a fresh token *and overwrites
  the stored hash*, which **immediately invalidates the old token** — the
  extension on every other device silently stops authenticating
  (`POST /captures` starts 401ing) until you also update *their* Options
  pages with the new value. There's no warning in the UI for this today.
  If you're about to regenerate, plan to update every other device's Options
  page in the same sitting, not "later."

## Verify it

Open a supported job listing (LinkedIn/JobStreet/Indeed) and either apply or
use the popup's manual capture. Check the extension popup's "Recent form
sweeps" / event ring buffer for a success entry, then confirm the row landed
in the tracker's `/` list from this device.

## Non-localhost API base

`manifest.json`'s `host_permissions` only ships `127.0.0.1`, `localhost`, and
`*.linkedin.com` — enough for a device running its own local server. Pointing
the API base at another device's LAN IP or hostname isn't covered by that
grant, and nothing in the extension's UI currently requests the
`optional_host_permissions` (`https://*/*` / `http://*/*`) that would be
needed to make that origin work. Until that's wired up, the supported setup
is: **run a server on every device, all pointed at the same shared database**
(managed Postgres, per `docs/windows-dev.md`), not one shared server reached
over LAN.
