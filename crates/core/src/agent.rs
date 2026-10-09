//! Claude Code over the Agent Client Protocol.
//!
//! The agent is a subprocess speaking ACP on stdio. This crate never holds a
//! model credential: Claude Code authenticates with the user's own login, and
//! we are a client of it, the arrangement Zed uses.
//!
//! To run the agent *inside* the sandbox, the command handed here is the
//! `rxd sandbox exec` relay (see the cli crate), which spawns the real agent
//! in an MXC container and copies stdio through. ACP sees an ordinary
//! subprocess either way.

use std::path::PathBuf;
use std::str::FromStr;
use std::sync::Arc;

use agent_client_protocol::schema::v1::{
    ContentBlock, InitializeRequest, NewSessionRequest, PermissionOptionId, PromptRequest,
    RequestPermissionOutcome, RequestPermissionRequest, RequestPermissionResponse,
    SelectedPermissionOutcome, SessionNotification, SessionUpdate, StopReason, TextContent,
};
use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::{AcpAgent, AcpAgentConfig, Agent, Client, ConnectionTo};

use crate::{Error, Result};

/// The command Zed's Claude Code adapter is started with when nothing else
/// is configured. The adapter is what speaks ACP on Claude Code's behalf.
pub const DEFAULT_AGENT_COMMAND: &str = "npx -y @zed-industries/claude-code-acp";

/// How to launch the agent process.
#[derive(Debug, Clone)]
pub struct AgentCommand {
    pub program: String,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
}

impl AgentCommand {
    /// Parse a shell-style line such as `npx -y @zed-industries/claude-code-acp`.
    pub fn parse(line: &str) -> Result<Self> {
        let mut words =
            shell_words::split(line).map_err(|e| Error::AgentCommand(e.to_string()))?;
        if words.is_empty() {
            return Err(Error::AgentCommand("empty command".into()));
        }
        let program = words.remove(0);
        Ok(Self {
            program,
            args: words,
            env: Vec::new(),
        })
    }

    pub fn new(program: impl Into<String>, args: impl IntoIterator<Item = String>) -> Self {
        Self {
            program: program.into(),
            args: args.into_iter().collect(),
            env: Vec::new(),
        }
    }

    pub fn env(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.push((name.into(), value.into()));
        self
    }

    fn config(&self) -> AcpAgentConfig {
        AcpAgentConfig::new(&self.program)
            .args(self.args.iter().cloned())
            .envs(self.env.iter().cloned())
    }
}

impl FromStr for AgentCommand {
    type Err = Error;
    fn from_str(s: &str) -> Result<Self> {
        Self::parse(s)
    }
}

/// Answers a permission request with the option to select, or `None` to cancel.
pub type PermissionDecider =
    Arc<dyn Fn(&RequestPermissionRequest) -> Option<PermissionOptionId> + Send + Sync>;

/// What to do when the agent asks permission for a tool call.
#[derive(Clone)]
pub enum PermissionPolicy {
    /// Pick the first option the agent offers, which is its "allow" option.
    /// Fine inside a sandbox; never use it outside one.
    AutoApprove,
    /// Decline every request. The agent carries on without the tool.
    DenyAll,
    /// Ask the caller.
    Ask(PermissionDecider),
}

impl std::fmt::Debug for PermissionPolicy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::AutoApprove => write!(f, "AutoApprove"),
            Self::DenyAll => write!(f, "DenyAll"),
            Self::Ask(_) => write!(f, "Ask(..)"),
        }
    }
}

impl PermissionPolicy {
    fn decide(&self, request: &RequestPermissionRequest) -> RequestPermissionOutcome {
        let chosen = match self {
            Self::AutoApprove => request.options.first().map(|o| o.option_id.clone()),
            Self::DenyAll => None,
            Self::Ask(ask) => ask(request),
        };
        match chosen {
            Some(id) => RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(id)),
            None => RequestPermissionOutcome::Cancelled,
        }
    }
}

/// A callback for every session update the agent streams: text chunks,
/// thinking, tool calls and their progress, plan changes.
pub type OnUpdate = Arc<dyn Fn(SessionUpdate) + Send + Sync>;

/// Outcome of one prompt.
#[derive(Debug)]
pub struct PromptOutcome {
    pub stop_reason: StopReason,
}

/// Start the agent, open one session in `cwd`, send one prompt, and return
/// when the agent stops. Updates stream to `on_update` as they arrive.
///
/// One session per call is the scaffold's shape. A long-lived session that
/// takes several prompts is the next step and sits behind the same
/// `connect_with` block; it needs a channel feeding prompts into the closure.
pub async fn run_prompt(
    command: &AgentCommand,
    cwd: PathBuf,
    prompt: String,
    permissions: PermissionPolicy,
    on_update: OnUpdate,
) -> Result<PromptOutcome> {
    let agent = AcpAgent::new(command.config());
    let notify = on_update.clone();

    let outcome = Client
        .builder()
        .on_receive_notification(
            async move |notification: SessionNotification, _cx| {
                notify(notification.update);
                Ok(())
            },
            agent_client_protocol::on_receive_notification!(),
        )
        .on_receive_request(
            async move |request: RequestPermissionRequest, responder, _connection| {
                let outcome = permissions.decide(&request);
                responder.respond(RequestPermissionResponse::new(outcome))
            },
            agent_client_protocol::on_receive_request!(),
        )
        .connect_with(agent, |connection: ConnectionTo<Agent>| async move {
            connection
                .send_request(InitializeRequest::new(ProtocolVersion::V1))
                .block_task()
                .await?;
            let session = connection
                .send_request(NewSessionRequest::new(cwd))
                .block_task()
                .await?;
            let response = connection
                .send_request(PromptRequest::new(
                    session.session_id.clone(),
                    vec![ContentBlock::Text(TextContent::new(prompt))],
                ))
                .block_task()
                .await?;
            Ok(PromptOutcome {
                stop_reason: response.stop_reason,
            })
        })
        .await?;

    Ok(outcome)
}

/// Render an update as one line of plain text, for logs and the CLI. The
/// gpui app will draw these properly; this is the lowest common denominator.
pub fn describe_update(update: &SessionUpdate) -> Option<String> {
    let text = match update {
        SessionUpdate::AgentMessageChunk(chunk) => content_text(&chunk.content),
        SessionUpdate::AgentThoughtChunk(chunk) => {
            content_text(&chunk.content).map(|t| format!("[thinking] {t}"))
        }
        SessionUpdate::ToolCall(call) => Some(format!("[tool] {}", call.title)),
        SessionUpdate::ToolCallUpdate(update) => update
            .fields
            .status
            .map(|status| format!("[tool {}] {:?}", update.tool_call_id, status)),
        SessionUpdate::Plan(plan) => Some(format!("[plan] {} entries", plan.entries.len())),
        _ => None,
    };
    text
}

fn content_text(block: &ContentBlock) -> Option<String> {
    match block {
        ContentBlock::Text(text) => Some(text.text.clone()),
        _ => None,
    }
}
