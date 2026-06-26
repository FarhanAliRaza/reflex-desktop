import reflex as rx

from reflex_desktop import DesktopPlugin

config = rx.Config(
    app_name="counter",
    # Allow the Tauri webview origin (tauri://localhost / http://tauri.localhost) to reach
    # the backend. "*" is simplest for a local desktop app.
    cors_allowed_origins=["*"],
    plugins=[
        # embedded (default): the Python backend runs INSIDE the desktop binary via PyO3.
        # Self-contained — no separate server, no `reflex run --backend-only`. Just launch
        # the built binary.
        DesktopPlugin(
            backend="embedded",
            product_name="Reflex Counter",
            identifier="dev.reflex.counter",
            window_title="Reflex Counter",
            # window customization (applied to tauri.conf.json on every build)
            min_width=900,
            min_height=650,
            resizable=True,
            center=True,
            # with_global_tauri=True (default) exposes window.__TAURI__ for the desktop bridge.
            # Add the notification plugin so desktop.notify() works (injected into Cargo/main.rs).
            tauri_plugins=("notification",),
        ),
        # remote: alternative — frontend-only app that talks to a backend you run
        # separately (then you DO need `uv run reflex run --backend-only`):
        # DesktopPlugin(
        #     backend="remote",
        #     backend_url="http://127.0.0.1:8000",
        #     product_name="Reflex Counter",
        #     identifier="dev.reflex.counter",
        #     window_title="Reflex Counter",
        # ),
    ],
)
