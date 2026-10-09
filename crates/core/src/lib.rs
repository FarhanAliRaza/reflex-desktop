//! Core of the Reflex desktop builder, with no window attached.
//!
//! Three seams, each usable on its own:
//!
//! - [`sandbox`]: runs commands inside an MXC container with the project
//!   directory bound read-write and the runtime bound read-only.
//! - [`agent`]: drives Claude Code over the Agent Client Protocol. The agent
//!   authenticates itself; this crate never holds an Anthropic credential.
//! - [`deploy`]: the three-call Reflex Cloud deploy (reserve, upload, submit)
//!   plus status polling.
//!
//! The `rxd` binary in `crates/cli` exercises all three without a window,
//! which is also how they are tested in CI.

pub mod agent;
pub mod deploy;
pub mod error;
pub mod sandbox;

pub use error::Error;
pub type Result<T> = std::result::Result<T, Error>;
