// SPDX-License-Identifier: MPL-2.0
//! The Hub, as this helper sees it: the identity routes under
//! `/api/hub/sync/` and the delivery service under `/api/messages/v1/`
//! (docs/research/luma-messages-delivery-api.md), both with the Hub device
//! token of this device. Nothing here logs a token, a body or an id pair.

use serde_json::Value;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

pub const MAX_BLOB: u64 = 25 * 1024 * 1024 + 1024;

#[derive(Debug, Clone)]
pub struct HubError {
    pub status: u16,
    pub code: String,
    pub message: String,
    pub retry_after: u64,
}

impl HubError {
    fn transport(message: String) -> Self {
        HubError { status: 0, code: "network".into(), message, retry_after: 0 }
    }

    /// The bridge error class Messages shows for this failure.
    pub fn bridge_code(&self) -> &'static str {
        match (self.status, self.code.as_str()) {
            (0, _) => "network",
            (401, _) => "unauthorized",
            (429, _) => "rate_limited",
            (_, "blocked_by_you") => "blocked_by_you",
            (_, "accepted_only") => "accepted_only",
            (_, "handle_unknown") => "handle_unknown",
            (_, "account_unknown") => "account_unknown",
            (507, _) | (500..=599, _) => "server",
            (413, _) => "too_large",
            _ => "server",
        }
    }
}

impl std::fmt::Display for HubError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} {}", self.status, self.code)
    }
}

pub type HubResult<T> = Result<T, HubError>;

/// `device.json` from Luma Connect enrolment: the Hub and this device's token.
#[derive(Clone)]
pub struct Enrolment {
    pub hub: String,
    pub token: String,
    pub device: String,
}

pub fn device_file() -> PathBuf {
    let data = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_else(|| "/nonexistent".into())).join(".local/share")
    });
    data.join("luma/connect/device.json")
}

fn local_host(host: &str) -> bool {
    matches!(host, "127.0.0.1" | "localhost" | "[::1]" | "::1")
}

/// The Hub origin, refusing plain http to anywhere but this machine.
pub fn hub_origin(value: &str) -> Result<String, String> {
    let text = value.trim().trim_end_matches('/');
    let (scheme, rest) = text.split_once("://").ok_or("not a URL")?;
    let host_port = rest.split('/').next().unwrap_or("");
    if host_port.is_empty() || host_port.contains('@') {
        return Err("not a usable hub URL".into());
    }
    let host = if host_port.starts_with('[') {
        host_port.split(']').next().map(|h| format!("{h}]")).unwrap_or_default()
    } else {
        host_port.rsplit_once(':').map(|(h, _)| h.to_owned()).unwrap_or_else(|| host_port.to_owned())
    };
    match scheme {
        "https" => Ok(format!("https://{host_port}")),
        "http" if local_host(&host) => Ok(format!("http://{host_port}")),
        _ => Err("the Hub must be https".into()),
    }
}

pub fn load_enrolment(path: &Path) -> Result<Option<Enrolment>, String> {
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    // Validate and read the same descriptor: no path-following or replacement
    // race, and no FIFO/device open that could block the helper.
    let file = match std::fs::OpenOptions::new().read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("device file: {}", e.kind())),
    };
    let meta = file.metadata().map_err(|e| format!("device file: {}", e.kind()))?;
    validate_device_metadata(&meta, unsafe { libc::geteuid() })?;
    const MAX_DEVICE_FILE: u64 = 64 * 1024;
    if meta.len() > MAX_DEVICE_FILE {
        return Err("the Luma Connect device file is too large".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_DEVICE_FILE + 1).read_to_end(&mut bytes)
        .map_err(|e| format!("device file: {}", e.kind()))?;
    if bytes.len() as u64 > MAX_DEVICE_FILE {
        return Err("the Luma Connect device file is too large".into());
    }
    let data: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "the Luma Connect device file is not JSON".to_string())?;
    let field = |name: &str| data.get(name).and_then(Value::as_str).unwrap_or("").to_owned();
    let (hub, token, device) = (field("hub"), field("token"), field("device_id"));
    if hub.is_empty() || token.is_empty() || device.is_empty() {
        return Ok(None);
    }
    Ok(Some(Enrolment { hub: hub_origin(&hub)?, token, device }))
}

fn validate_device_metadata(meta: &std::fs::Metadata, uid: u32) -> Result<(), String> {
    use std::os::unix::fs::MetadataExt;
    if !meta.is_file() || meta.uid() != uid || meta.mode() & 0o077 != 0 {
        return Err("the Luma Connect device file must be a private regular file owned by this user".into());
    }
    Ok(())
}

#[derive(Clone)]
pub struct Hub {
    agent: ureq::Agent,
    long: ureq::Agent,
    origin: String,
    token: String,
}

fn agent(read_timeout: Duration) -> ureq::Agent {
    let mut builder = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(15))
        .timeout_read(read_timeout)
        .timeout_write(Duration::from_secs(60))
        .user_agent(concat!("luma-messages/", env!("CARGO_PKG_VERSION")));
    if let Ok(connector) = native_tls::TlsConnector::new() {
        builder = builder.tls_connector(Arc::new(connector));
    }
    builder.build()
}

fn error_from(response: ureq::Response) -> HubError {
    let status = response.status();
    let header_retry = response.header("retry-after").and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
    let body: Value = response.into_json().unwrap_or(Value::Null);
    let code = body.get("code").and_then(Value::as_str).unwrap_or("").to_owned();
    let message = body
        .get("message")
        .or_else(|| body.get("error"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .chars()
        .take(300)
        .collect();
    let retry_after = body.get("retry_after").and_then(Value::as_u64).unwrap_or(header_retry);
    HubError { status, code, message, retry_after }
}

impl Hub {
    pub fn new(enrolment: &Enrolment) -> Self {
        Hub {
            agent: agent(Duration::from_secs(60)),
            long: agent(Duration::from_secs(80)),
            origin: enrolment.hub.clone(),
            token: enrolment.token.clone(),
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.origin, path)
    }

    fn call(&self, agent: &ureq::Agent, method: &str, path: &str, body: Option<&Value>) -> HubResult<Value> {
        let request = agent.request(method, &self.url(path)).set("Authorization", &format!("Bearer {}", self.token)).set("Accept", "application/json");
        let result = match body {
            Some(body) => request.send_json(body.clone()),
            None => request.call(),
        };
        match result {
            Ok(response) => {
                let text = response.into_string().map_err(|e| HubError::transport(e.kind().to_string()))?;
                if text.trim().is_empty() {
                    Ok(Value::Null)
                } else {
                    serde_json::from_str(&text).map_err(|_| HubError::transport("the Hub answered something other than JSON".into()))
                }
            }
            Err(ureq::Error::Status(_, response)) => Err(error_from(response)),
            Err(ureq::Error::Transport(t)) => Err(HubError::transport(t.kind().to_string())),
        }
    }

    pub fn get(&self, path: &str) -> HubResult<Value> {
        self.call(&self.agent, "GET", path, None)
    }

    pub fn long_get(&self, path: &str) -> HubResult<Value> {
        self.call(&self.long, "GET", path, None)
    }

    pub fn post(&self, path: &str, body: &Value) -> HubResult<Value> {
        self.call(&self.agent, "POST", path, Some(body))
    }

    pub fn put(&self, path: &str, body: &Value) -> HubResult<Value> {
        self.call(&self.agent, "PUT", path, Some(body))
    }

    pub fn delete(&self, path: &str) -> HubResult<Value> {
        self.call(&self.agent, "DELETE", path, None)
    }

    pub fn upload_blob(&self, bytes: &[u8]) -> HubResult<Value> {
        let result = self
            .agent
            .post(&self.url("/api/messages/v1/blobs"))
            .set("Authorization", &format!("Bearer {}", self.token))
            .set("Content-Type", "application/octet-stream")
            .set("Accept", "application/json")
            .send_bytes(bytes);
        match result {
            Ok(response) => response.into_json().map_err(|_| HubError::transport("blob answer".into())),
            Err(ureq::Error::Status(_, response)) => Err(error_from(response)),
            Err(ureq::Error::Transport(t)) => Err(HubError::transport(t.kind().to_string())),
        }
    }

    pub fn download_blob(&self, blob: &str) -> HubResult<Vec<u8>> {
        if blob.is_empty() || !blob.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
            return Err(HubError { status: 404, code: "blob_unknown".into(), message: String::new(), retry_after: 0 });
        }
        let result = self
            .agent
            .get(&self.url(&format!("/api/messages/v1/blobs/{blob}")))
            .set("Authorization", &format!("Bearer {}", self.token))
            .call();
        match result {
            Ok(response) => {
                let mut bytes = Vec::new();
                response
                    .into_reader()
                    .take(MAX_BLOB + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|e| HubError::transport(e.kind().to_string()))?;
                if bytes.len() as u64 > MAX_BLOB {
                    return Err(HubError { status: 413, code: "too_large".into(), message: String::new(), retry_after: 0 });
                }
                Ok(bytes)
            }
            Err(ureq::Error::Status(_, response)) => Err(error_from(response)),
            Err(ureq::Error::Transport(t)) => Err(HubError::transport(t.kind().to_string())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_http_only_to_this_machine() {
        assert_eq!(hub_origin("https://hub.simplyluma.com/").unwrap(), "https://hub.simplyluma.com");
        assert_eq!(hub_origin("http://127.0.0.1:8123").unwrap(), "http://127.0.0.1:8123");
        assert!(hub_origin("http://hub.simplyluma.com").is_err());
        assert!(hub_origin("http://127.0.0.1.evil.example").is_err());
        assert!(hub_origin("https://user:pw@hub.example").is_err());
        assert!(hub_origin("ftp://hub.example").is_err());
    }
    #[test]
    fn enrolment_refuses_unsafe_files_and_reads_private_regular_file() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let dir = std::env::temp_dir().join(format!("luma-enrolment-{}-{}", std::process::id(), rand::random::<u64>()));
        std::fs::create_dir(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        let path = dir.join("device.json");
        let body = br#"{"hub":"https://hub.example","token":"test-token","device_id":"0b7e3a52-8c1d-4f6e-9a20-3d5c7b1e9f04"}"#;
        std::fs::write(&path, body).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert_eq!(load_enrolment(&path).unwrap().unwrap().hub, "https://hub.example");
        let meta = std::fs::metadata(&path).unwrap();
        assert!(validate_device_metadata(&meta, unsafe { libc::geteuid() } + 1).is_err());
        let link = dir.join("link");
        symlink(&path, &link).unwrap();
        assert!(load_enrolment(&link).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(load_enrolment(&path).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(load_enrolment(&dir).is_err());
        let fifo = dir.join("fifo");
        let cpath = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
        assert!(load_enrolment(&fifo).is_err());
        std::fs::write(&path, vec![b' '; 65537]).unwrap();
        assert!(load_enrolment(&path).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

}
