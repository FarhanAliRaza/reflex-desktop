// Embedded-backend Reflex desktop shell.
//
// The Python ASGI backend runs in-process via PyO3 on a background thread bound to
// 127.0.0.1:PORT (matching the URL baked into env.json). The Tauri webview owns the
// main thread (required on macOS), loads the prebuilt static frontend from ../dist, and
// talks to the local backend. Bundled resources: a relocatable python-build-standalone
// interpreter (`python/`), the app's `site-packages/`, and the app payload (`app/`).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::path::Path;
use std::thread;

use pyo3::prelude::*;
use tauri::Manager;

// Loopback port the embedded backend binds to (baked into env.json at build time).
const BACKEND_PORT: u16 = __PORT__;

#[cfg(all(unix, not(target_os = "macos")))]
fn reflex_desktop_send_notification(
    app_name: &str,
    title: &str,
    body: Option<&str>,
) -> Result<(), String> {
    let mut notification = notify_rust::Notification::new();
    notification.appname(app_name).summary(title);
    if let Some(body) = body {
        notification.body(body);
    }

    notification
        .show()
        .map(|_| ())
        .map_err(|err| err.to_string())
}

#[cfg(all(unix, not(target_os = "macos")))]
fn reflex_desktop_run_notification_helper() -> bool {
    if !std::env::args().any(|arg| arg == "--reflex-desktop-notify-helper") {
        return false;
    }

    let app_name = std::env::var("REFLEX_DESKTOP_NOTIFY_APP_NAME")
        .unwrap_or_else(|_| "Reflex Notifications".to_string());
    let title = std::env::var("REFLEX_DESKTOP_NOTIFY_TITLE").unwrap_or_default();
    let body = std::env::var("REFLEX_DESKTOP_NOTIFY_BODY").ok();

    if reflex_desktop_send_notification(&app_name, &title, body.as_deref()).is_err() {
        std::process::exit(1);
    }
    true
}

#[cfg(not(all(unix, not(target_os = "macos"))))]
fn reflex_desktop_run_notification_helper() -> bool {
    false
}

#[tauri::command]
fn reflex_desktop_notify(
    app: tauri::AppHandle,
    title: String,
    body: Option<String>,
) -> Result<(), String> {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let app_name = app
            .config()
            .product_name
            .clone()
            .unwrap_or_else(|| "Reflex".to_string());
        let notification_app_name = format!("{app_name} Notifications");
        let mut command =
            std::process::Command::new(std::env::current_exe().map_err(|err| err.to_string())?);
        command
            .arg("--reflex-desktop-notify-helper")
            .env("REFLEX_DESKTOP_NOTIFY_APP_NAME", &notification_app_name)
            .env("REFLEX_DESKTOP_NOTIFY_TITLE", &title);
        if let Some(body) = body {
            command.env("REFLEX_DESKTOP_NOTIFY_BODY", body);
        }

        return match command.status() {
            Ok(status) if status.success() => Ok(()),
            Ok(status) => {
                let err = format!("notification helper exited with {status}");
                Err(err)
            }
            Err(err) => Err(err.to_string()),
        };
    }

    #[cfg(not(all(unix, not(target_os = "macos"))))]
    {
        let _ = app;
        let err = "reflex-desktop notification bridge is Linux-only";
        let _ = (title, body);
        Err(err.to_string())
    }
}

fn port_available(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn copy_dir_all(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let path = entry.path();
        let target = dst.join(entry.file_name());
        if path.is_dir() {
            copy_dir_all(&path, &target)?;
        } else {
            std::fs::copy(&path, &target)?;
        }
    }
    Ok(())
}

// Bundle resources are read-only / signed on macOS and Windows, but the backend writes
// under its working dir (.web/backend, uploads). Copy the app payload to a per-user
// writable dir on first launch and run from there.
fn ensure_writable_app_root(
    bundled_app: &Path,
    app_data: &Path,
) -> std::io::Result<std::path::PathBuf> {
    let dest = app_data.join("app");
    if !dest.join("rxconfig.py").exists() {
        copy_dir_all(bundled_app, &dest)?;
    }
    Ok(dest)
}

fn spawn_backend(app_root: std::path::PathBuf) {
    thread::spawn(move || {
        std::env::set_var("REFLEX_DESKTOP_APP_ROOT", &app_root);
        std::env::set_var("REFLEX_DESKTOP_PORT", BACKEND_PORT.to_string());
        // Acquire the GIL once and hand control to uvicorn (it owns its asyncio loop and
        // releases the GIL while awaiting I/O). This call blocks for the app's lifetime.
        let result: PyResult<()> = Python::with_gil(|py| {
            let bootstrap = py.import_bound("reflex_desktop_bootstrap")?;
            bootstrap.call_method0("main")?;
            Ok(())
        });
        if let Err(err) = result {
            eprintln!("reflex-desktop: backend thread exited with error: {err}");
        }
    });
}

fn main() {
    if reflex_desktop_run_notification_helper() {
        return;
    }

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![reflex_desktop_notify])
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // >>> reflex-desktop setup >>>
            // <<< reflex-desktop setup <<<
            // A bundled app finds its resources via Tauri's resource_dir(); an in-place dev
            // build (bare `cargo build`, launched by `reflex-desktop run`) has its resources in
            // the crate dir, which the CLI passes via REFLEX_DESKTOP_RESOURCE_DIR.
            let dev_resource = std::env::var_os("REFLEX_DESKTOP_RESOURCE_DIR");
            let resource_dir = match &dev_resource {
                Some(dir) => std::path::PathBuf::from(dir),
                None => app.path().resource_dir()?,
            };

            // Point the embedded interpreter at the bundled runtime. python-build-standalone
            // extracts to <resource>/python/python (holding bin/ + lib/). Must be set before
            // the first Python::with_gil (which triggers Py_Initialize).
            std::env::set_var("PYTHONHOME", resource_dir.join("python").join("python"));
            std::env::set_var("PYTHONDONTWRITEBYTECODE", "1");
            std::env::set_var("PYTHONNOUSERSITE", "1");

            if !port_available(BACKEND_PORT) {
                // Same-app relaunches are already handled by the single-instance plugin
                // (the second instance focuses the first and exits), so a busy port means
                // some other program owns it. A desktop launch has no visible stderr, so
                // surface the conflict in a native dialog before bailing out.
                use tauri_plugin_dialog::DialogExt;
                let product = app
                    .config()
                    .product_name
                    .clone()
                    .unwrap_or_else(|| "This app".to_string());
                let message = format!(
                    "{product} could not start: port 127.0.0.1:{BACKEND_PORT} is already in \
                     use by another program.\n\nClose the program using that port and launch \
                     {product} again."
                );
                eprintln!("reflex-desktop: {message}");
                app.dialog()
                    .message(message)
                    .kind(tauri_plugin_dialog::MessageDialogKind::Error)
                    .title(format!("{product} failed to start"))
                    .blocking_show();
                std::process::exit(1);
            }

            // Dev runs straight from the (writable) project payload so each build's code is
            // picked up; a bundled app's resources are read-only, so copy them out first.
            let bundled_app = resource_dir.join("app");
            let app_root = if dev_resource.is_some() {
                bundled_app
            } else {
                ensure_writable_app_root(&bundled_app, &app.path().app_data_dir()?)?
            };

            // site-packages holds reflex/uvicorn; the app payload holds rxconfig + the app
            // package + reflex_desktop_bootstrap.py — both must be importable by Py_Initialize.
            let pythonpath = std::env::join_paths([resource_dir.join("site-packages"), app_root.clone()])
                .expect("compose PYTHONPATH");
            std::env::set_var("PYTHONPATH", &pythonpath);

            eprintln!(
                "reflex-desktop: embedded backend starting (resource_dir={}, app_root={})",
                resource_dir.display(),
                app_root.display()
            );
            spawn_backend(app_root);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
