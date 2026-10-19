# Bridge

WFH setup pro max. Multiple Claude accounts in multiple containers inside multiple tmux windows, and one chat UI on your phone for all of them. Termius and the phone drive the same sessions, so you can pick up a session from the couch and keep going at the desk.

It finds sessions by scanning `/proc` for `claude` processes (container ones too), reading `CLAUDE_CONFIG_DIR` from each one's environment and matching it to a tmux pane. The chat comes from the session's `.jsonl` transcript, the permission buttons from `tmux capture-pane`, and your messages go in with `tmux paste-buffer` plus Enter. Container sessions are matched by tmux window name, so give those accounts a SessionStart hook that renames the window to the session name.

Python stdlib only, no build step. Linux only, since it reads `/proc`.

## Run it

1. Copy `accounts.example.json` to `accounts.json` and list your accounts: config dir, inbox for uploaded files, and optionally the tmux session, container and folder used by the New button.
2. Copy `.env.example` to `.env` and fill it in. `BRIDGE_NTFY_TOPIC` is optional and only the session name ever goes to ntfy.
3. `DEPLOY_HOST=myserver ./deploy.sh` ships git HEAD (plus your local `accounts.json`) to `~/bridge` on the server and starts the `bridge` systemd user service. It writes `.env` on first run if there isn't one.
4. Once, as root on the server: `tailscale serve --bg --set-path /bridge unix:/home/<you>/bridge/run/bridge.sock`
5. Open `https://<machine>.<tailnet>.ts.net/bridge/` on your phone and add it to the home screen.

To run it by hand instead: `set -a; . ./.env; set +a; python3 server.py`

Remove: `systemctl --user disable --now bridge`, `sudo tailscale serve --set-path /bridge off`, delete `~/bridge`.

## Security

It can type into any Claude window on the server, so treat it like a shell.

- Tailnet only. `tailscale serve` publishes it over HTTPS and overwrites `Tailscale-User-Login` with your real tailnet identity. Logins not in `BRIDGE_ALLOWED_LOGINS` get a 403, and so do tagged devices. Keep it off Funnel and any public tunnel.
- It listens on a Unix socket (dir 700, socket 600), not a TCP port. A container on host networking could reach a 127.0.0.1 port and fake the login header, but it can't reach the socket as long as `~/bridge` isn't mounted into it.
- POSTs also need an `X-Bridge: 1` header, so other sites can't fire requests from your browser.

`tools/make_icons.py` redraws the icons.
