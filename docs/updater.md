# Auto-updates

`reflex-desktop` wires Tauri's official updater plugin for you. With it enabled, your
installed app checks an endpoint you host for a newer version, downloads the signed
artifact, verifies the signature, and installs it.

## 1. Generate a signing keypair

Updates must be signed (this is separate from OS code signing — see
[signing.md](signing.md)). Generate a minisign keypair once:

```sh
cargo tauri signer generate -w ~/.tauri/myapp.key
```

Keep the private key out of the repo. The command prints the **public key** — that goes
in your `rxconfig.py`.

## 2. Configure the plugin

```python
DesktopPlugin(
    backend="embedded",
    product_name="My App",
    identifier="com.example.myapp",
    tauri_plugins=("updater",),
    updater_endpoints=(
        "https://releases.example.com/myapp/{{target}}/{{arch}}/{{current_version}}",
    ),
    updater_pubkey="dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6...",
)
```

This injects `tauri-plugin-updater` into the generated shell (`Cargo.toml` + `main.rs`),
grants the `updater:default` capability, writes the endpoints/pubkey into
`tauri.conf.json`, and turns on `createUpdaterArtifacts` so `reflex-desktop build
--bundle` emits the update artifacts and `.sig` files next to the installers.

The `{{target}}`, `{{arch}}` and `{{current_version}}` placeholders are filled in by the
updater at request time; your endpoint can be a static file server or the
[Tauri updater JSON](https://tauri.app/plugin/updater/) format — see the upstream docs
for the response schema.

## 3. Sign at build time

Set the private key in the environment when producing a release bundle:

```sh
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/myapp.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="..."   # if the key has one
reflex-desktop build --bundle
```

## 4. Trigger the check from your app

The updater's default capability lets the frontend drive the check. From a Reflex event
handler:

```python
from reflex_desktop import desktop

# Check + install using the plugin's JS API via the generic invoke bridge:
rx.call_script(
    """
    (async () => {
      const update = await window.__TAURI__.updater.check();
      if (update) { await update.downloadAndInstall(); }
    })()
    """
)
```

For fully automatic background updates, add the check to your app's startup event.

## Notes

- Linux: updates apply to the AppImage distribution (deb/rpm installs update through the
  package manager instead).
- macOS: the updated app must also be code-signed/notarized or Gatekeeper will reject it
  — see [signing.md](signing.md).
- The updater compares versions against `version` in `tauri.conf.json`; bump it (or set
  it from your Python package version) for each release.
