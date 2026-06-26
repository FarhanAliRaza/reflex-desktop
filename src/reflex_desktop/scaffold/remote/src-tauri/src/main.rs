// Remote-backend Reflex desktop shell: a plain Tauri window that loads the prebuilt
// static frontend (embedded from ../dist). The frontend talks to a hosted backend over
// the URL baked into env.json at build time, so there is no local server to launch.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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

fn main() {
    if reflex_desktop_run_notification_helper() {
        return;
    }

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![reflex_desktop_notify])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
