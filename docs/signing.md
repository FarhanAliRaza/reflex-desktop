# Code signing and notarization

Unsigned desktop apps trigger OS warnings (macOS Gatekeeper blocks them outright for
downloaded apps; Windows SmartScreen warns). Signing is configured through environment
variables read by the Tauri bundler during `reflex-desktop build --bundle` — nothing in
this repo needs to change.

## macOS: signing + notarization

You need an Apple Developer account and a **Developer ID Application** certificate
installed in the keychain of the build machine (or exported as a `.p12`).

```sh
# Who to sign as — the certificate's identity string, e.g.
# "Developer ID Application: Your Name (TEAMID)"
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"

# Notarization credentials (App Store Connect API key is the least-painful option):
export APPLE_API_ISSUER="..."     # App Store Connect issuer id
export APPLE_API_KEY="..."        # key id
export APPLE_API_KEY_PATH="$HOME/.appstoreconnect/AuthKey_XXXX.p8"

reflex-desktop build --bundle
```

With those set, the bundler signs the `.app`, and submits the `.dmg` for notarization and
staples the ticket. Alternative credential styles (`APPLE_ID` + `APPLE_PASSWORD` +
`APPLE_TEAM_ID`, or a `.p12` via `APPLE_CERTIFICATE`/`APPLE_CERTIFICATE_PASSWORD` for CI)
are documented in the [Tauri macOS signing guide](https://tauri.app/distribute/sign/macos/).

Notes for the embedded backend: the bundled Python runtime (`Resources/python`,
`Resources/site-packages`) is part of the signed bundle, and the bundled
`libpython3.x.dylib` is ad-hoc signed at assemble time; the bundler re-signs everything
with your identity, so no extra steps are needed.

## Windows: Authenticode

Any Authenticode workflow works; the two common setups:

- **OV/EV certificate on the machine** — set the thumbprint and the bundler signs with
  `signtool`:

  ```powershell
  $env:WINDOWS_CERTIFICATE_THUMBPRINT = "A1B2C3..."
  $env:TAURI_WINDOWS_SIGNTOOL_PATH = "C:\...\signtool.exe"   # optional, auto-detected
  reflex-desktop build --bundle
  ```

- **Cloud signing (Azure Trusted Signing / KMS / custom)** — point the bundler at a
  custom sign command in `tauri/src-tauri/tauri.conf.json` (hand edits outside the
  managed values are preserved):

  ```json
  {
    "bundle": {
      "windows": {
        "signCommand": "trusted-signing-cli -e https://... %1"
      }
    }
  }
  ```

Details: [Tauri Windows signing guide](https://tauri.app/distribute/sign/windows/).

## Linux

No platform-enforced signing. For distribution-level trust, publish your `.deb`/`.rpm`
from a signed repository, or ship the AppImage plus the updater's minisign signature
(see [updater.md](updater.md) — update artifacts are always signature-verified).

## CI tips

- Store certificates/keys as CI secrets; never commit them.
- macOS notarization needs network access and can take minutes — keep it in the release
  job only.
- Pair signing with the auto-updater ([updater.md](updater.md)) so users actually receive
  the signed builds.
