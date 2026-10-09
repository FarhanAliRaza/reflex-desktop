//! `rxd`: the headless driver for reflex-desktop-core.
//!
//! Every subcommand is one seam of the core, so each can be exercised and
//! tested without a window. The gpui app composes the same calls.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Context};
use clap::{Args, Parser, Subcommand};
use reflex_desktop_core::agent::{
    describe_update, run_prompt, AgentCommand, PermissionPolicy, DEFAULT_AGENT_COMMAND,
};
use reflex_desktop_core::deploy::{landed, ReflexCloud, Submission, DEFAULT_BASE_URL};
use reflex_desktop_core::sandbox::{self, NetworkMode, Sandbox, Stream};
use uuid::Uuid;

#[derive(Parser)]
#[command(name = "rxd", about = "Reflex desktop builder, headless", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Args, Clone)]
struct SandboxArgs {
    /// The project directory, bound read-write into the sandbox.
    #[arg(long, default_value = ".")]
    project: PathBuf,
    /// Directories bound read-only (a runtime bundle, a uv cache).
    #[arg(long = "readonly")]
    readonly: Vec<PathBuf>,
    /// Network posture: open, isolated, or proxy=HOST:PORT.
    #[arg(long, default_value = "open")]
    network: String,
}

impl SandboxArgs {
    fn build(&self) -> anyhow::Result<Sandbox> {
        let project = self
            .project
            .canonicalize()
            .with_context(|| format!("project directory {}", self.project.display()))?;
        let network = match self.network.as_str() {
            "open" => NetworkMode::Open,
            "isolated" => NetworkMode::Isolated,
            other => match other.strip_prefix("proxy=") {
                Some(addr) => NetworkMode::ProxyOnly {
                    addr: addr.to_string(),
                },
                None => bail!("--network takes open, isolated or proxy=HOST:PORT"),
            },
        };
        let mut sandbox = Sandbox::for_project(project).with_network(network);
        for dir in &self.readonly {
            sandbox = sandbox.with_readonly(dir.canonicalize()?);
        }
        Ok(sandbox)
    }
}

#[derive(Subcommand)]
enum Command {
    /// Report whether this host can run sandboxes and which backends it has.
    Doctor,
    /// Run one command inside the project sandbox and print its output.
    Run {
        #[command(flatten)]
        sandbox: SandboxArgs,
        /// Seconds before the command is killed.
        #[arg(long)]
        timeout: Option<u32>,
        /// The shell line to run.
        #[arg(last = true, required = true)]
        command: Vec<String>,
    },
    /// Relay this process's stdio through a command running in the sandbox.
    /// This is what an ACP client is pointed at to run the agent contained.
    Exec {
        #[command(flatten)]
        sandbox: SandboxArgs,
        #[arg(last = true, required = true)]
        command: Vec<String>,
    },
    /// Start `reflex run` in the sandbox and stream its logs.
    Preview {
        #[command(flatten)]
        sandbox: SandboxArgs,
        /// The command that starts the app.
        #[arg(long, default_value = "uv run reflex run")]
        command: String,
    },
    /// Send one prompt to Claude Code and stream what it does.
    Prompt {
        #[command(flatten)]
        sandbox: SandboxArgs,
        /// The ACP agent command, run inside the sandbox.
        #[arg(long, default_value = DEFAULT_AGENT_COMMAND)]
        agent: String,
        /// Run the agent on the host instead of in the sandbox. For debugging.
        #[arg(long)]
        unsandboxed: bool,
        /// Decline every tool permission the agent asks for.
        #[arg(long)]
        deny: bool,
        prompt: String,
    },
    /// Export the app in the sandbox and deploy the archives to Reflex Cloud.
    Deploy {
        #[command(flatten)]
        sandbox: SandboxArgs,
        #[command(flatten)]
        cloud: CloudArgs,
        /// The app to deploy.
        #[arg(long)]
        app_id: Uuid,
        /// Skip `reflex export` and deploy the archives already in the project.
        #[arg(long)]
        no_export: bool,
        #[arg(long)]
        regions: Option<String>,
        #[arg(long)]
        hostname: Option<String>,
        #[arg(long)]
        description: Option<String>,
    },
    /// Print a deployment's status line, or watch it to a terminal state.
    Status {
        #[command(flatten)]
        cloud: CloudArgs,
        deployment_id: Uuid,
        #[arg(long)]
        watch: bool,
    },
}

#[derive(Args, Clone)]
struct CloudArgs {
    /// A Reflex Cloud API token (from the account's token settings).
    #[arg(long, env = "REFLEX_API_TOKEN")]
    token: String,
    #[arg(long, env = "REFLEX_CLOUD_URL", default_value = DEFAULT_BASE_URL)]
    base_url: String,
}

impl CloudArgs {
    fn client(&self) -> ReflexCloud {
        ReflexCloud::with_base_url(&self.base_url, &self.token)
    }
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("error: {error:#}");
        std::process::exit(1);
    }
}

async fn run() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .with_writer(std::io::stderr)
        .init();

    match Cli::parse().command {
        Command::Doctor => doctor(),
        Command::Run {
            sandbox,
            timeout,
            command,
        } => {
            sandbox::require_host_support()?;
            let line = shell_join(&command);
            let output = sandbox
                .build()?
                .run(&line, timeout.map(|s| s * 1000))?;
            print!("{}", output.stdout_text());
            eprint!("{}", output.stderr_text());
            for warning in output.warnings {
                eprintln!("warning: {warning}");
            }
            std::process::exit(output.code);
        }
        Command::Exec { sandbox, command } => {
            sandbox::require_host_support()?;
            let code = sandbox::relay_stdio(&sandbox.build()?, &shell_join(&command))?;
            std::process::exit(code);
        }
        Command::Preview { sandbox, command } => {
            sandbox::require_host_support()?;
            let mut process = sandbox.build()?.spawn(&command)?;
            process.forward_lines(|stream, line| match stream {
                Stream::Stdout => println!("{line}"),
                Stream::Stderr => eprintln!("{line}"),
            })?;
            let code = process.wait()?;
            std::process::exit(code);
        }
        Command::Prompt {
            sandbox,
            agent,
            unsandboxed,
            deny,
            prompt,
        } => {
            let project = sandbox.project.canonicalize()?;
            let command = if unsandboxed {
                AgentCommand::parse(&agent)?
            } else {
                sandbox::require_host_support()?;
                relay_command(&sandbox, &agent)?
            };
            let permissions = if deny {
                PermissionPolicy::DenyAll
            } else {
                PermissionPolicy::AutoApprove
            };
            let outcome = run_prompt(
                &command,
                project,
                prompt,
                permissions,
                Arc::new(|update| {
                    if let Some(text) = describe_update(&update) {
                        println!("{text}");
                    }
                }),
            )
            .await?;
            eprintln!("agent stopped: {:?}", outcome.stop_reason);
            Ok(())
        }
        Command::Deploy {
            sandbox,
            cloud,
            app_id,
            no_export,
            regions,
            hostname,
            description,
        } => {
            let project = sandbox.project.canonicalize()?;
            if !no_export {
                sandbox::require_host_support()?;
                eprintln!("exporting in the sandbox...");
                sandbox
                    .build()?
                    .run_checked("uv run reflex export", Some(15 * 60 * 1000))?;
            }
            let backend = project.join("backend.zip");
            let frontend = project.join("frontend.zip");
            for archive in [&backend, &frontend] {
                if !archive.is_file() {
                    bail!("{} is missing; run `reflex export` first", archive.display());
                }
            }
            let client = cloud.client();
            let deployment_id = client
                .deploy_archives(
                    &backend,
                    &frontend,
                    Submission {
                        app_id,
                        regions,
                        hostname,
                        description,
                        ..Default::default()
                    },
                )
                .await?;
            println!("{deployment_id}");
            let status = client
                .watch(
                    deployment_id,
                    Duration::from_secs(5),
                    Duration::from_secs(15 * 60),
                    |status| eprintln!("{status}"),
                )
                .await?;
            if landed(&status) {
                Ok(())
            } else {
                bail!("deployment ended: {status}")
            }
        }
        Command::Status {
            cloud,
            deployment_id,
            watch,
        } => {
            let client = cloud.client();
            if watch {
                client
                    .watch(
                        deployment_id,
                        Duration::from_secs(5),
                        Duration::from_secs(15 * 60),
                        |status| println!("{status}"),
                    )
                    .await?;
            } else {
                println!("{}", client.status(deployment_id).await?);
            }
            Ok(())
        }
    }
}

fn doctor() -> anyhow::Result<()> {
    let support = sandbox::host_support();
    println!(
        "sandbox: {}",
        if support.supported { "supported" } else { "unsupported" }
    );
    if let Some(reason) = &support.reason {
        println!("reason: {reason}");
    }
    for backend in &support.backends {
        println!("backend: {backend}");
    }
    for warning in &support.warnings {
        println!("warning: {warning}");
    }
    Ok(())
}

/// The agent command, wrapped so ACP spawns *this* binary and the agent runs
/// inside the sandbox with the same policy the caller named.
fn relay_command(sandbox: &SandboxArgs, agent: &str) -> anyhow::Result<AgentCommand> {
    let exe = std::env::current_exe()?;
    let mut args = vec![
        "exec".to_string(),
        "--project".to_string(),
        sandbox.project.canonicalize()?.to_string_lossy().into_owned(),
        "--network".to_string(),
        sandbox.network.clone(),
    ];
    for dir in &sandbox.readonly {
        args.push("--readonly".to_string());
        args.push(dir.to_string_lossy().into_owned());
    }
    args.push("--".to_string());
    args.extend(shell_words::split(agent).context("agent command")?);
    Ok(AgentCommand::new(exe.to_string_lossy().into_owned(), args))
}

fn shell_join(words: &[String]) -> String {
    shell_words::join(words)
}
