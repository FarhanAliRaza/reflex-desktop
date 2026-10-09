//! Reflex Cloud deploy, as the hosting CLI does it.
//!
//! Three calls: `POST /deployments/reserve` mints a deployment id and signs
//! one PUT per archive; the archives go straight to storage; then
//! `POST /deployments` with `stored_build_id` and `deploy_protocol=1` submits
//! them. `GET /deployments/{id}/status` is a plain string the caller polls
//! until it carries a terminal marker.
//!
//! The archives are what `reflex export` writes: `backend.zip` and
//! `frontend.zip` in the project directory. Producing them is the sandbox's
//! job (see the cli crate's `deploy` command); this module only moves bytes.

use std::path::Path;
use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::multipart::Form;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{Error, Result};

pub const DEFAULT_BASE_URL: &str = "https://build.reflex.dev/api/v1";
const API_TOKEN_HEADER: &str = "x-api-token";

/// The client gate accepts a protocol number from anything that is not the
/// hosting CLI. 1 is the reserve-then-submit flow.
pub const DEPLOY_PROTOCOL: u32 = 1;

/// Status phrases the CLI watcher stops polling on, from
/// `cloud/services/deployment_failures.py`.
pub const TERMINAL_MARKERS: &[&str] = &[
    LANDED_MARKER,
    "AwaitingApproval",
    "build error",
    "unable to find status for given id",
    "error",
    "bad response",
];
pub const LANDED_MARKER: &str = "completed successfully";

pub fn is_terminal(status: &str) -> bool {
    TERMINAL_MARKERS.iter().any(|m| status.contains(m))
}

pub fn landed(status: &str) -> bool {
    status.contains(LANDED_MARKER)
}

#[derive(Debug, Clone)]
pub struct ReflexCloud {
    base_url: String,
    token: String,
    http: reqwest::Client,
}

#[derive(Debug, Serialize)]
struct ReserveParams {
    app_id: Uuid,
    backend_size: u64,
    frontend_size: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UploadTarget {
    pub url: String,
    #[serde(default)]
    pub headers: std::collections::HashMap<String, String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Reservation {
    pub deployment_id: Uuid,
    pub backend: UploadTarget,
    pub frontend: UploadTarget,
    pub expires_in: u64,
}

/// Everything `POST /deployments` takes that a desktop deploy sends.
#[derive(Debug, Clone, Default)]
pub struct Submission {
    pub app_id: Uuid,
    pub stored_build_id: Uuid,
    /// JSON: a list of region codes or a map of region to machine count.
    pub regions: Option<String>,
    pub hostname: Option<String>,
    /// The URL the frontend was compiled against (`reflex export` bakes it in).
    pub exported_from: Option<String>,
    /// JSON object of environment variables for the app.
    pub secrets: Option<String>,
    pub reflex_version: Option<String>,
    pub python_version: Option<String>,
    pub description: Option<String>,
}

impl ReflexCloud {
    pub fn new(token: impl Into<String>) -> Self {
        Self::with_base_url(DEFAULT_BASE_URL, token)
    }

    pub fn with_base_url(base_url: impl Into<String>, token: impl Into<String>) -> Self {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(60))
            .build()
            .expect("reqwest client");
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            token: token.into(),
            http,
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
    }

    fn auth(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        if let Ok(value) = HeaderValue::from_str(&self.token) {
            headers.insert(HeaderName::from_static(API_TOKEN_HEADER), value);
        }
        headers
    }

    async fn refused(response: reqwest::Response) -> Error {
        let status = response.status().as_u16();
        let body = response.text().await.unwrap_or_default();
        Error::CloudRefused { status, body }
    }

    /// Mint a deployment id and signed upload URLs for the two archives.
    pub async fn reserve(
        &self,
        app_id: Uuid,
        backend_size: u64,
        frontend_size: u64,
    ) -> Result<Reservation> {
        let response = self
            .http
            .post(self.url("/deployments/reserve"))
            .headers(self.auth())
            .json(&ReserveParams {
                app_id,
                backend_size,
                frontend_size,
            })
            .send()
            .await?;
        if !response.status().is_success() {
            return Err(Self::refused(response).await);
        }
        Ok(response.json().await?)
    }

    /// PUT an archive to its signed URL. The signature pins the exact byte
    /// count and content type, so the body must be the file the size was
    /// reserved for.
    pub async fn upload(&self, target: &UploadTarget, body: Vec<u8>) -> Result<()> {
        let mut request = self.http.put(&target.url).body(body);
        for (name, value) in &target.headers {
            request = request.header(name, value);
        }
        let response = request.send().await?;
        if !response.status().is_success() {
            return Err(Self::refused(response).await);
        }
        Ok(())
    }

    /// Submit the reserved archives as a deployment. Returns the deployment id.
    pub async fn submit(&self, submission: Submission) -> Result<Uuid> {
        let mut form = Form::new()
            .text("app_id", submission.app_id.to_string())
            .text("stored_build_id", submission.stored_build_id.to_string())
            .text("deploy_protocol", DEPLOY_PROTOCOL.to_string());
        for (name, value) in [
            ("regions", submission.regions),
            ("hostname", submission.hostname),
            ("exported_from", submission.exported_from),
            ("secrets", submission.secrets),
            ("reflex_version", submission.reflex_version),
            ("python_version", submission.python_version),
            ("description", submission.description),
        ] {
            if let Some(value) = value {
                form = form.text(name, value);
            }
        }
        let response = self
            .http
            .post(self.url("/deployments"))
            .headers(self.auth())
            .multipart(form)
            .send()
            .await?;
        if !response.status().is_success() {
            return Err(Self::refused(response).await);
        }
        Ok(response.json().await?)
    }

    /// The published status line for a deployment.
    pub async fn status(&self, deployment_id: Uuid) -> Result<String> {
        let response = self
            .http
            .get(self.url(&format!("/deployments/{deployment_id}/status")))
            .headers(self.auth())
            .send()
            .await?;
        if !response.status().is_success() {
            return Err(Self::refused(response).await);
        }
        Ok(response.json().await?)
    }

    /// Poll the status until it is terminal, reporting each change to
    /// `on_status`. Gives up after `deadline`.
    pub async fn watch(
        &self,
        deployment_id: Uuid,
        interval: Duration,
        deadline: Duration,
        on_status: impl Fn(&str),
    ) -> Result<String> {
        let started = std::time::Instant::now();
        let mut last = String::new();
        loop {
            let status = self.status(deployment_id).await?;
            if status != last {
                on_status(&status);
                last = status.clone();
            }
            if is_terminal(&status) {
                return Ok(status);
            }
            if started.elapsed() > deadline {
                return Err(Error::Deploy(format!(
                    "deployment {deployment_id} did not settle within {deadline:?}; last status: {status}"
                )));
            }
            tokio::time::sleep(interval).await;
        }
    }

    /// The whole flow over archives already on disk: reserve, upload both,
    /// submit. Returns the deployment id; call [`watch`](Self::watch) after.
    pub async fn deploy_archives(
        &self,
        backend_zip: &Path,
        frontend_zip: &Path,
        mut submission: Submission,
    ) -> Result<Uuid> {
        let backend = tokio::fs::read(backend_zip).await?;
        let frontend = tokio::fs::read(frontend_zip).await?;
        let reservation = self
            .reserve(submission.app_id, backend.len() as u64, frontend.len() as u64)
            .await?;
        self.upload(&reservation.backend, backend).await?;
        self.upload(&reservation.frontend, frontend).await?;
        submission.stored_build_id = reservation.deployment_id;
        self.submit(submission).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_markers_stop_a_watch() {
        assert!(is_terminal("Deployment completed successfully"));
        assert!(is_terminal("build error: pip failed"));
        assert!(!is_terminal("Building backend image"));
        assert!(landed("Deployment completed successfully"));
        assert!(!landed("AwaitingApproval"));
    }
}
