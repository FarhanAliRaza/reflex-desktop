use std::io;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("this host cannot run MXC sandboxes: {0}")]
    SandboxUnsupported(String),

    #[error("sandbox: {0}")]
    Sandbox(#[from] mxc_sdk::v1::Error),

    #[error("sandbox process exposed no {0} stream")]
    MissingStream(&'static str),

    #[error("sandbox command timed out")]
    TimedOut,

    #[error("sandbox command exited with status {code}: {stderr}")]
    CommandFailed { code: i32, stderr: String },

    #[error("agent: {0}")]
    Agent(#[from] agent_client_protocol::Error),

    #[error("agent command is not a valid shell line: {0}")]
    AgentCommand(String),

    #[error("reflex cloud: {0}")]
    Http(#[from] reqwest::Error),

    #[error("reflex cloud answered {status}: {body}")]
    CloudRefused { status: u16, body: String },

    #[error("deploy: {0}")]
    Deploy(String),

    #[error(transparent)]
    Io(#[from] io::Error),

    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
