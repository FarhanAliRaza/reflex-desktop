# reflex-desktop

A native desktop builder for [Reflex](https://reflex.dev) apps. The user builds
on their own machine, in a sandbox, with their own Claude subscription, and
deploys to Reflex Cloud.

| Piece | Supplied by | Crate |
|---|---|---|
| Sandbox | [MXC](https://github.com/microsoft/mxc), in-process via `mxc-sdk` | `crates/core` (`sandbox`) |
| Agent | Claude Code, driven over the [Agent Client Protocol](https://agentclientprotocol.com) | `crates/core` (`agent`) |
| Deploy | Reflex Cloud's reserve → upload → submit API | `crates/core` (`deploy`) |
| Headless driver | `rxd`, one subcommand per seam | `crates/cli` |
| Window | gpui, with a preview pane rendered by a platform webview (`wry`) | `crates/app` (not yet) |

The app never holds a model credential. Claude Code authenticates with the
user's own login; this app is a client of it over stdio, which is the
arrangement Zed ships.

## How the pieces fit

```
gpui window ──► core::agent ──ACP over stdio──► rxd exec ──► [MXC container: claude-code-acp]
     │                                                                │ writes
     │          core::sandbox ──► [MXC container: uv run reflex run] ◄┘ project dir
     │                                     │ serves 127.0.0.1:3000
     └── webview preview ◄─────────────────┘
     └── core::deploy ──► Reflex Cloud (reserve, PUT archives, submit, status)
```

On macOS and Linux MXC containers are transient, so a `Sandbox` is a policy
and every command is its own container over the same project directory.

## Try it headless

```bash
cargo build -p reflex-desktop-cli
alias rxd=target/debug/rxd

rxd doctor                                   # can this host sandbox?
rxd run --project ./myapp -- uv run python -c 'print(1)'
rxd preview --project ./myapp                # reflex run, logs streamed
rxd prompt --project ./myapp "add a todo list page"   # Claude Code, in the sandbox
rxd deploy --project ./myapp --app-id <uuid> # export in the sandbox, then deploy
rxd status --watch <deployment-id>
```

`rxd prompt` runs `npx -y @zed-industries/claude-code-acp` inside the sandbox
through `rxd exec`, which relays stdio. Claude Code must already be logged in
on the machine. `REFLEX_API_TOKEN` carries the Reflex Cloud token.

## Platform notes

- Linux needs `bubblewrap` (0.5+) on PATH. Any policy that allows egress
  (`--network open`, `--network proxy=...`) also needs `slirp4netns`, since
  Bubblewrap gives the sandbox a private network namespace rather than the
  host's. Verified in CI-like conditions: `--network isolated` and `--network
  open` both run, the relay carries stdin and stdout, and a write outside the
  project directory fails because nothing outside it is mounted.
- **Linux cannot yet show a preview.** Bubblewrap refuses
  `ingress.default=allow`, and its private namespace never shares the host's
  loopback, so `uv run reflex run` inside the sandbox listens on a port the
  host cannot reach. `Sandbox::ingress()` sets ingress to `deny` there so the
  policy is accepted; `rxd preview` therefore starts an app nobody can
  connect to on Linux. The planned fix is a unix-socket bridge through the
  project directory, which both sides can see. macOS and Windows are
  unaffected: Seatbelt accepts inbound, ProcessContainer takes
  `ingress.default=allow` plus `hostLoopback`.
- macOS uses Seatbelt and needs nothing installed. Containers are transient,
  so there is no persistent state beyond the project directory.
- Windows uses ProcessContainer; persistent containers exist there and are
  not used yet.
- The webview: the published `gpui-component` crate carries no webview
  feature, so the preview pane will come from `wry` (or `rgpui-webview`)
  embedded in the gpui window rather than from gpui-component.
