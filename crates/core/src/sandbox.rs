//! MXC-backed sandbox for one project.
//!
//! On macOS (Seatbelt) and Linux (Bubblewrap) MXC offers only transient
//! containers, so a [`Sandbox`] is a *policy* rather than a running thing:
//! every command is its own container built from that policy, and what
//! persists between commands is the project directory on disk. Windows adds
//! persistent containers (IsolationSession, WSLC) and is left for later.
//!
//! Networking: egress is left open by default because the agent reaches the
//! Anthropic API and Reflex installs packages. [`NetworkMode::ProxyOnly`] is
//! the stricter posture for later, once a loopback proxy exists to point at.
//!
//! Inbound differs per backend, and the policy is built per backend for it.
//! Seatbelt (macOS) and ProcessContainer (Windows) accept inbound on the
//! host's own network, so a preview server inside the sandbox is reachable
//! from a webview. Bubblewrap (Linux) refuses `ingress.default = allow`
//! outright: every non-isolated policy runs in a private slirp namespace that
//! never shares the host network and drops host-loopback traffic, so a TCP
//! listener inside is reachable from inside only. The Linux preview therefore
//! needs a bridge over something both sides share, which is the project
//! directory (a unix socket there, relayed to TCP on the host). Not built yet.

use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::thread;

use mxc_sdk::v1::policy::{
    FilesystemPolicy, NetworkAction, NetworkEgressPolicy, NetworkIngressPolicy, NetworkPolicy,
    NetworkRuntimeConfig,
};
use mxc_sdk::v1::{self, ContainerRequest, Containment, MxcProcess, WaitResult};

use crate::{Error, Result};

/// What this host can run, as MXC reports it.
#[derive(Debug, Clone)]
pub struct HostSupport {
    pub supported: bool,
    pub reason: Option<String>,
    pub backends: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn host_support() -> HostSupport {
    let support = v1::platform_support();
    let mut backends = Vec::new();
    let mut warnings = Vec::new();
    for backend in v1::available_backends() {
        backends.push(backend.backend.clone());
        warnings.extend(backend.warnings.iter().cloned());
    }
    HostSupport {
        supported: support.is_supported,
        reason: support.reason,
        backends,
        warnings,
    }
}

/// The backend MXC will pick on this host, by its own name (`bubblewrap`,
/// `seatbelt`, `processcontainer`, ...). Probed once per process.
pub fn backend_name() -> &'static str {
    static BACKEND: OnceLock<String> = OnceLock::new();
    BACKEND
        .get_or_init(|| {
            v1::available_backends()
                .into_iter()
                .next()
                .map(|b| b.backend)
                .unwrap_or_default()
        })
        .as_str()
}

/// Whether a listener inside the sandbox can be reached from the host over
/// TCP on this backend. False on Bubblewrap, see the module docs.
pub fn host_can_reach_inbound() -> bool {
    backend_name() != "bubblewrap"
}

/// Refuse early with MXC's own reason rather than failing at the first spawn.
pub fn require_host_support() -> Result<HostSupport> {
    let support = host_support();
    if !support.supported {
        return Err(Error::SandboxUnsupported(
            support
                .reason
                .clone()
                .unwrap_or_else(|| "no backend reported".to_string()),
        ));
    }
    Ok(support)
}

#[derive(Debug, Clone, Default)]
pub enum NetworkMode {
    /// Outbound open, inbound loopback allowed. The default while nothing
    /// proxies for the container.
    #[default]
    Open,
    /// Outbound only through a loopback proxy at `addr` (`host:port`).
    ProxyOnly { addr: String },
    /// No network at all. Useful for builds and tests, never for the agent.
    Isolated,
}

/// The policy every command in a project runs under.
#[derive(Debug, Clone)]
pub struct Sandbox {
    project_dir: PathBuf,
    readonly_dirs: Vec<PathBuf>,
    network: NetworkMode,
    environment: Vec<(String, String)>,
}

impl Sandbox {
    /// A sandbox over `project_dir`, which is bound read-write.
    pub fn for_project(project_dir: impl Into<PathBuf>) -> Self {
        Self {
            project_dir: project_dir.into(),
            readonly_dirs: Vec::new(),
            network: NetworkMode::default(),
            environment: Vec::new(),
        }
    }

    /// Add a directory the container may read but not write: the bundled
    /// runtime, a uv cache, a tools directory.
    pub fn with_readonly(mut self, dir: impl Into<PathBuf>) -> Self {
        self.readonly_dirs.push(dir.into());
        self
    }

    pub fn with_network(mut self, network: NetworkMode) -> Self {
        self.network = network;
        self
    }

    pub fn with_env(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.environment.push((name.into(), value.into()));
        self
    }

    pub fn project_dir(&self) -> &Path {
        &self.project_dir
    }

    fn request(&self, command: &str, timeout_ms: Option<u32>) -> ContainerRequest {
        let project = self.project_dir.to_string_lossy().into_owned();
        let mut request = ContainerRequest::new(command);
        request.containment = Containment::Process;
        request.working_directory = Some(project.clone());
        request.filesystem = Some(FilesystemPolicy {
            readwrite_paths: vec![project],
            readonly_paths: self
                .readonly_dirs
                .iter()
                .map(|p| p.to_string_lossy().into_owned())
                .collect(),
            ..Default::default()
        });
        request.network = Some(self.network_policy());
        request.inherit_default_environment = Some(true);
        if !self.environment.is_empty() {
            request.environment = Some(self.environment.clone());
        }
        request.timeout_ms = timeout_ms;
        request
    }

    /// Inbound as open as this backend allows: Bubblewrap refuses anything
    /// but deny, the others take allow.
    fn ingress() -> NetworkIngressPolicy {
        let action = if host_can_reach_inbound() {
            NetworkAction::Allow
        } else {
            NetworkAction::Deny
        };
        NetworkIngressPolicy {
            default: Some(action),
            host_loopback: Some(action),
        }
    }

    fn network_policy(&self) -> NetworkPolicy {
        match &self.network {
            NetworkMode::Open => NetworkPolicy {
                egress: Some(NetworkEgressPolicy {
                    default: Some(NetworkAction::Allow),
                    ..Default::default()
                }),
                ingress: Some(Self::ingress()),
                ..Default::default()
            },
            NetworkMode::ProxyOnly { addr } => NetworkPolicy {
                egress: Some(NetworkEgressPolicy {
                    default: Some(NetworkAction::Deny),
                    ..Default::default()
                }),
                ingress: Some(Self::ingress()),
                runtime_config: Some(NetworkRuntimeConfig {
                    network_proxy: Some(addr.clone()),
                }),
            },
            NetworkMode::Isolated => NetworkPolicy {
                egress: Some(NetworkEgressPolicy {
                    default: Some(NetworkAction::Deny),
                    ..Default::default()
                }),
                ingress: Some(NetworkIngressPolicy {
                    default: Some(NetworkAction::Deny),
                    host_loopback: Some(NetworkAction::Deny),
                }),
                ..Default::default()
            },
        }
    }

    /// Run `command` to completion and capture its output.
    pub fn run(&self, command: &str, timeout_ms: Option<u32>) -> Result<Output> {
        let result = v1::run(self.request(command, timeout_ms), Default::default())?;
        let code = match result.outcome {
            WaitResult::Exited(code) => code,
            WaitResult::TimedOut => return Err(Error::TimedOut),
        };
        Ok(Output {
            code,
            stdout: result.stdout,
            stderr: result.stderr,
            warnings: result.warnings,
        })
    }

    /// Like [`run`](Self::run) but an error on a non-zero exit.
    pub fn run_checked(&self, command: &str, timeout_ms: Option<u32>) -> Result<Output> {
        let output = self.run(command, timeout_ms)?;
        if output.code != 0 {
            return Err(Error::CommandFailed {
                code: output.code,
                stderr: output.stderr_text(),
            });
        }
        Ok(output)
    }

    /// Start `command` and hand back its live pipes. Long-running workloads
    /// (`reflex run`, the agent) go through here.
    pub fn spawn(&self, command: &str) -> Result<Process> {
        let process = v1::spawn(self.request(command, None), Default::default())?;
        Ok(Process { inner: process })
    }
}

#[derive(Debug)]
pub struct Output {
    pub code: i32,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub warnings: Vec<String>,
}

impl Output {
    pub fn stdout_text(&self) -> String {
        String::from_utf8_lossy(&self.stdout).into_owned()
    }
    pub fn stderr_text(&self) -> String {
        String::from_utf8_lossy(&self.stderr).into_owned()
    }
}

/// A live sandboxed process.
pub struct Process {
    inner: MxcProcess,
}

impl Process {
    pub fn id(&self) -> u32 {
        self.inner.id()
    }

    pub fn take_stdin(&mut self) -> Result<Box<dyn Write + Send>> {
        self.inner.take_stdin().ok_or(Error::MissingStream("stdin"))
    }

    pub fn take_stdout(&mut self) -> Result<Box<dyn Read + Send>> {
        self.inner.take_stdout().ok_or(Error::MissingStream("stdout"))
    }

    pub fn take_stderr(&mut self) -> Result<Box<dyn Read + Send>> {
        self.inner.take_stderr().ok_or(Error::MissingStream("stderr"))
    }

    /// Forward stdout and stderr line by line to `on_line` from background
    /// threads. Returns once both streams are taken; the threads end when the
    /// process closes its pipes.
    pub fn forward_lines<F>(&mut self, on_line: F) -> Result<()>
    where
        F: Fn(Stream, String) + Send + Sync + Clone + 'static,
    {
        let stdout = self.take_stdout()?;
        let stderr = self.take_stderr()?;
        let out = on_line.clone();
        thread::spawn(move || read_lines(stdout, |line| out(Stream::Stdout, line)));
        thread::spawn(move || read_lines(stderr, |line| on_line(Stream::Stderr, line)));
        Ok(())
    }

    pub fn try_wait(&mut self) -> io::Result<Option<i32>> {
        self.inner.try_wait()
    }

    pub fn wait(&mut self) -> Result<i32> {
        match self.inner.wait()? {
            WaitResult::Exited(code) => Ok(code),
            WaitResult::TimedOut => Err(Error::TimedOut),
        }
    }

    pub fn kill(&mut self) -> io::Result<()> {
        self.inner.kill()
    }

    pub fn warnings(&self) -> Vec<String> {
        self.inner.warnings()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Stdout,
    Stderr,
}

fn read_lines(stream: Box<dyn Read + Send>, mut sink: impl FnMut(String)) {
    for line in BufReader::new(stream).lines() {
        match line {
            Ok(line) => sink(line),
            Err(_) => break,
        }
    }
}

/// Relay this process's own stdin, stdout and stderr through a sandboxed
/// child, so a caller that spawned *us* as a stdio subprocess (an ACP client,
/// say) is really talking to the child inside the container.
///
/// Returns the child's exit code.
pub fn relay_stdio(sandbox: &Sandbox, command: &str) -> Result<i32> {
    let mut process = sandbox.spawn(command)?;
    let mut child_in = process.take_stdin()?;
    let mut child_out = process.take_stdout()?;
    let mut child_err = process.take_stderr()?;

    thread::spawn(move || {
        let mut stdin = io::stdin().lock();
        let _ = io::copy(&mut stdin, &mut child_in);
    });
    let out = thread::spawn(move || {
        let mut stdout = io::stdout().lock();
        let _ = io::copy(&mut child_out, &mut stdout);
    });
    let err = thread::spawn(move || {
        let mut stderr = io::stderr().lock();
        let _ = io::copy(&mut child_err, &mut stderr);
    });

    let code = process.wait()?;
    let _ = out.join();
    let _ = err.join();
    Ok(code)
}
