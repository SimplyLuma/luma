// SPDX-License-Identifier: MPL-2.0
//! `luma`: the Luma Messages helper (ADR-051 §2). One process per Messages
//! account, speaking luma-messages-bridge/1 (docs/research/
//! messages-bridge-protocol.md) on stdin and stdout, with JSON log lines on
//! stderr. Messages starts it as `luma --data-dir <account directory>`.
//!
//! Commands are answered on their own threads so a long send never holds up a
//! read; the service serialises what must be serial.

mod hub;
mod service;
mod state;

use serde_json::{json, Value};
use std::io::{BufRead, Read, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub const PROTOCOL: &str = "luma-messages-bridge/1";
const MAX_LINE: usize = 16 * 1024 * 1024;

/// stdout, one JSON object per line, shared by every thread.
#[derive(Clone)]
pub struct Output(Arc<Mutex<std::io::Stdout>>);

impl Output {
    fn line(&self, value: &Value) {
        let mut text = value.to_string();
        text.push('\n');
        let mut out = self.0.lock().unwrap_or_else(|p| p.into_inner());
        let _ = out.write_all(text.as_bytes());
        let _ = out.flush();
    }

    pub fn event(&self, name: &str, data: Value) {
        self.line(&json!({"event": name, "data": data}));
    }

    fn answer(&self, id: &str, result: Result<Value, BridgeError>) {
        match result {
            Ok(value) => self.line(&json!({"id": id, "ok": true, "result": if value.is_object() { value } else { json!({}) }})),
            Err(e) => self.line(&json!({"id": id, "ok": false, "error": {"code": e.code, "message": e.message, "retryable": e.retryable}})),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BridgeError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}

impl BridgeError {
    pub fn new(code: &str, message: &str) -> Self {
        BridgeError { code: code.into(), message: message.into(), retryable: false }
    }

    pub fn retry(code: &str, message: &str) -> Self {
        BridgeError { code: code.into(), message: message.into(), retryable: true }
    }
}

/// A structured log line on stderr. Never ids pairs, tokens, keys or text.
pub fn log(level: &str, message: &str) {
    let line = json!({"level": level, "msg": message});
    eprintln!("{line}");
}

fn main() {
    let mut args = std::env::args().skip(1);
    let mut data_dir: Option<PathBuf> = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--data-dir" => data_dir = args.next().map(PathBuf::from),
            "--version" => {
                println!("luma {}", env!("CARGO_PKG_VERSION"));
                return;
            }
            _ => {
                eprintln!("usage: luma --data-dir <directory>");
                std::process::exit(2);
            }
        }
    }
    let Some(data_dir) = data_dir else {
        eprintln!("usage: luma --data-dir <directory>");
        std::process::exit(2);
    };
    if let Err(e) = std::fs::create_dir_all(data_dir.join("media")) {
        eprintln!("luma: cannot use the data directory: {}", e.kind());
        std::process::exit(1);
    }
    let output = Output(Arc::new(Mutex::new(std::io::stdout())));
    let service = service::Service::new(data_dir, output.clone());

    let stdin = std::io::stdin();
    let mut reader = stdin.lock();
    let mut line = String::new();
    loop {
        line.clear();
        match reader.by_ref().take(MAX_LINE as u64 + 1).read_line(&mut line) {
            Ok(0) | Err(_) => break,
            Ok(n) if n > MAX_LINE => {
                log("error", "a command line was over 16 MiB; stopping");
                break;
            }
            Ok(_) => {}
        }
        let Ok(command) = serde_json::from_str::<Value>(line.trim()) else { continue };
        let id = match command.get("id") {
            Some(Value::String(s)) => s.clone(),
            Some(other) if !other.is_null() => other.to_string(),
            _ => continue,
        };
        let name = command.get("cmd").and_then(Value::as_str).unwrap_or("").to_owned();
        let args = command.get("args").cloned().unwrap_or_else(|| json!({}));
        if name == "shutdown" {
            service.shutdown();
            output.answer(&id, Ok(json!({})));
            break;
        }
        let (service, output) = (service.clone(), output.clone());
        std::thread::spawn(move || {
            let result = service.command(&name, &args);
            output.answer(&id, result);
        });
    }
    service.shutdown();
}
