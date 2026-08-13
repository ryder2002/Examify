use rand::{distributions::Alphanumeric, Rng};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    net::TcpListener,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopRuntime {
    local_api: String,
    remote_api: String,
    secret: String,
}

struct SidecarState {
    runtime: DesktopRuntime,
    child: Mutex<Option<CommandChild>>,
}

struct RollingLog {
    path: PathBuf,
    file: Option<File>,
}

impl RollingLog {
    const MAX_BYTES: u64 = 4 * 1024 * 1024;

    fn open(path: PathBuf) -> Option<Self> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .ok()?;
        Some(Self {
            path,
            file: Some(file),
        })
    }

    fn write_line(&mut self, line: &str) {
        if self
            .file
            .as_ref()
            .and_then(|file| file.metadata().ok())
            .map(|metadata| metadata.len() >= Self::MAX_BYTES)
            .unwrap_or(false)
        {
            if let Some(mut file) = self.file.take() {
                let _ = file.flush();
            }
            let rotated = self.path.with_extension("log.1");
            let _ = fs::remove_file(&rotated);
            let _ = fs::rename(&self.path, &rotated);
            self.file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.path)
                .ok();
        }
        if let Some(file) = self.file.as_mut() {
            let _ = writeln!(file, "{line}");
        } else if let Ok(file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            self.file = Some(file);
            if let Some(file) = self.file.as_mut() {
                let _ = writeln!(file, "{line}");
            }
        }
    }
}

#[tauri::command]
fn desktop_runtime(state: tauri::State<Arc<SidecarState>>) -> DesktopRuntime {
    state.runtime.clone()
}

const DEVICE_ID_NAMESPACE: &str = "toeicdoc.desktop.device.v1";
const KEYRING_SERVICE: &str = "TOEICDOC";
const LEGACY_KEYRING_SERVICE: &str = "SmartExamConverter";
const LEGACY_BUNDLE_IDENTIFIER: &str = "online.congnhat.exam";
static DEVICE_IDENTITY: OnceLock<Result<String, String>> = OnceLock::new();

fn legacy_data_dir(current_data_dir: &Path) -> Option<PathBuf> {
    let parent = current_data_dir.parent()?;
    let legacy = parent.join(LEGACY_BUNDLE_IDENTIFIER);
    (legacy != current_data_dir).then_some(legacy)
}

fn purge_legacy_app_data(current_data_dir: &Path) -> std::io::Result<bool> {
    let Some(legacy) = legacy_data_dir(current_data_dir) else {
        return Ok(false);
    };
    let metadata = match std::fs::symlink_metadata(&legacy) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    // Never follow a link or remove anything except the exact legacy directory.
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Ok(false);
    }
    std::fs::remove_dir_all(legacy)?;
    Ok(true)
}

fn clear_legacy_credential() {
    if let Ok(entry) = keyring::Entry::new(LEGACY_KEYRING_SERVICE, "refresh-token") {
        let _ = entry.delete_credential();
    }
}

fn normalize_hardware_parts(
    machine_guid: Option<String>,
    macs: Vec<String>,
) -> Result<String, String> {
    let guid = machine_guid
        .unwrap_or_default()
        .trim()
        .trim_matches('{')
        .trim_matches('}')
        .to_ascii_lowercase();
    let mut normalized_macs: Vec<String> = macs
        .into_iter()
        .map(|value| {
            value
                .chars()
                .filter(|ch| ch.is_ascii_hexdigit())
                .collect::<String>()
                .to_ascii_lowercase()
        })
        .filter(|value| value.len() == 12 && value != "000000000000")
        .collect();
    normalized_macs.sort();
    normalized_macs.dedup();
    if guid.is_empty() && normalized_macs.is_empty() {
        return Err("Không lấy được định danh phần cứng của máy".into());
    }
    let material = format!(
        "{DEVICE_ID_NAMESPACE}|guid={guid}|macs={}",
        normalized_macs.join(",")
    );
    Ok(hex::encode(Sha256::digest(material.as_bytes())))
}

#[cfg(windows)]
fn windows_machine_guid() -> Option<String> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;

    RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey("SOFTWARE\\Microsoft\\Cryptography")
        .ok()?
        .get_value("MachineGuid")
        .ok()
}

#[cfg(windows)]
fn physical_mac_addresses() -> Vec<String> {
    use std::os::windows::process::CommandExt;
    use std::process::Command;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let script = "Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object {$_.MacAddress} | ForEach-Object {$_.MacAddress}";
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .creation_flags(CREATE_NO_WINDOW)
        .output();
    output
        .ok()
        .filter(|value| value.status.success())
        .map(|value| {
            String::from_utf8_lossy(&value.stdout)
                .lines()
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(not(windows))]
fn physical_mac_addresses() -> Vec<String> {
    let mut values = Vec::new();
    if let Ok(entries) = std::fs::read_dir("/sys/class/net") {
        for entry in entries.flatten() {
            if entry.file_name() == "lo" {
                continue;
            }
            if let Ok(value) = std::fs::read_to_string(entry.path().join("address")) {
                values.push(value);
            }
        }
    }
    values
}

#[cfg(not(windows))]
fn windows_machine_guid() -> Option<String> {
    std::fs::read_to_string("/etc/machine-id").ok()
}

#[tauri::command]
fn device_identity() -> Result<String, String> {
    DEVICE_IDENTITY
        .get_or_init(|| {
            // Only this namespaced digest crosses the Tauri boundary.
            // MachineGuid and raw MAC addresses are never persisted, returned,
            // or written to logs. Cache it for the process lifetime so browser
            // refreshes do not repeatedly enumerate Windows adapters.
            normalize_hardware_parts(windows_machine_guid(), physical_mac_addresses())
        })
        .clone()
}

static REFRESH_TOKEN_CACHE: Mutex<Option<Option<String>>> = Mutex::new(None);

fn get_fallback_token_path() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .or_else(|| std::env::var_os("APPDATA"))?;
    let mut dir = std::path::PathBuf::from(base);
    dir.push(".toeic_doc");
    std::fs::create_dir_all(&dir).ok()?;
    dir.push("session.dat");
    Some(dir)
}

fn clear_legacy_fallback_token() {
    let Some(base) = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .or_else(|| std::env::var_os("APPDATA"))
    else {
        return;
    };
    let legacy_dir = PathBuf::from(base).join(".smart_exam_converter");
    let _ = std::fs::remove_file(legacy_dir.join("session.dat"));
    // remove_dir only succeeds when the exact legacy directory is empty.
    let _ = std::fs::remove_dir(legacy_dir);
}

fn write_fallback_token(token: &str) {
    if let Some(path) = get_fallback_token_path() {
        let _ = std::fs::write(path, token.as_bytes());
    }
}

fn read_fallback_token() -> Option<String> {
    let path = get_fallback_token_path()?;
    let content = std::fs::read_to_string(path).ok()?;
    let trimmed = content.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn remove_fallback_token() {
    if let Some(path) = get_fallback_token_path() {
        let _ = std::fs::remove_file(path);
    }
}

#[tauri::command]
fn store_refresh_token(token: String) -> Result<(), String> {
    let keyring_result = keyring::Entry::new(KEYRING_SERVICE, "refresh-token")
        .map_err(|error| error.to_string())
        .and_then(|entry| {
            entry
                .set_password(&token)
                .map_err(|error| error.to_string())
        });

    if let Err(_error) = keyring_result {
        #[cfg(windows)]
        return Err(format!(
            "Không lưu được phiên vào kho bảo mật của hệ điều hành: {_error}"
        ));

        #[cfg(not(windows))]
        write_fallback_token(&token);
    } else {
        // Remove plaintext state left by releases that wrote the fallback file
        // even when Windows Credential Manager / Apple Keychain succeeded.
        remove_fallback_token();
    }

    if let Ok(mut cache) = REFRESH_TOKEN_CACHE.lock() {
        *cache = Some(Some(token));
    }
    Ok(())
}

#[tauri::command]
fn load_refresh_token() -> Result<Option<String>, String> {
    if let Ok(cache) = REFRESH_TOKEN_CACHE.lock() {
        if let Some(ref val) = *cache {
            return Ok(val.clone());
        }
    }
    let keyring_token = keyring::Entry::new(KEYRING_SERVICE, "refresh-token")
        .map_err(|e| e.to_string())
        .and_then(|entry| match entry.get_password() {
            Ok(token) => Ok(Some(token)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(error.to_string()),
        });

    let token = match keyring_token {
        Ok(Some(token)) => {
            remove_fallback_token();
            Some(token)
        }
        _ => {
            let legacy = read_fallback_token();
            if let Some(ref token) = legacy {
                // One-time migration for installations created by older builds.
                if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, "refresh-token") {
                    if entry.set_password(token).is_ok() {
                        remove_fallback_token();
                    }
                }
            }
            legacy
        }
    };

    if let Ok(mut cache) = REFRESH_TOKEN_CACHE.lock() {
        *cache = Some(token.clone());
    }
    Ok(token)
}

#[tauri::command]
fn clear_refresh_token() -> Result<(), String> {
    if let Ok(mut cache) = REFRESH_TOKEN_CACHE.lock() {
        *cache = Some(None);
    }
    remove_fallback_token();
    if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, "refresh-token") {
        let _ = entry.delete_credential();
    }
    Ok(())
}

#[tauri::command]
#[allow(deprecated)]
fn open_support_group(app: tauri::AppHandle) -> Result<(), String> {
    app.shell()
        .open("https://zalo.me/g/3ekaczmgbnytxav4jj8s", None)
        .map_err(|error| error.to_string())
}

#[tauri::command]
#[allow(deprecated)]
fn open_external_url(app: tauri::AppHandle, url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://") || url.starts_with("mailto:")) {
        return Err("Only http(s) and mailto links are allowed".into());
    }
    app.shell()
        .open(url, None)
        .map_err(|error| error.to_string())
}

pub fn run() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("reserve loopback port");
    let port = listener.local_addr().expect("local address").port();
    drop(listener);
    let secret: String = rand::thread_rng()
        .sample_iter(&Alphanumeric)
        .take(48)
        .map(char::from)
        .collect();
    let runtime = DesktopRuntime {
        local_api: format!("http://127.0.0.1:{port}"),
        remote_api: "https://exam.congnhat.online".into(),
        secret: secret.clone(),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Arc::new(SidecarState {
            runtime: runtime.clone(),
            child: Mutex::new(None),
        }))
        .invoke_handler(tauri::generate_handler![
            desktop_runtime,
            device_identity,
            store_refresh_token,
            load_refresh_token,
            clear_refresh_token,
            open_support_group,
            open_external_url
        ])
        .setup(move |app| {
            let data_dir = app.path().app_local_data_dir()?;
            let resource_dir = app.path().resource_dir()?;
            match purge_legacy_app_data(&data_dir) {
                Ok(true) => eprintln!(
                    "Removed local data from legacy bundle identifier {LEGACY_BUNDLE_IDENTIFIER}"
                ),
                Ok(false) => {}
                Err(error) => eprintln!("Could not remove legacy app data: {error}"),
            }
            clear_legacy_credential();
            clear_legacy_fallback_token();
            std::fs::create_dir_all(&data_dir)?;
            let log_path = data_dir.join("sidecar-output.log");
            let log_file = RollingLog::open(log_path.clone());

            // Show the webview before launching the optional OCR sidecar so a
            // slow or broken native dependency cannot make the whole app look
            // frozen in Activity Monitor.
            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
                let _ = window.set_focus();
            }

            let sidecar = app.shell().sidecar("smart-exam-sidecar")?.args([
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "--secret",
                &secret,
                "--data-dir",
                &data_dir.to_string_lossy(),
                "--resource-dir",
                &resource_dir.to_string_lossy(),
            ]);
            let (mut events, child) = sidecar.spawn()?;
            let state = app.state::<Arc<SidecarState>>();
            *state.child.lock().expect("sidecar lock") = Some(child);
            let log_file = std::sync::Arc::new(std::sync::Mutex::new(log_file));
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Error(ref error) => {
                            eprintln!("Sidecar error: {error}");
                            if let Ok(mut guard) = log_file.lock() {
                                if let Some(ref mut file) = *guard {
                                    file.write_line(&format!("[ERROR] {error}"));
                                }
                            }
                        }
                        CommandEvent::Stdout(ref line) => {
                            if let Ok(mut guard) = log_file.lock() {
                                if let Some(ref mut file) = *guard {
                                    file.write_line(&format!(
                                        "[STDOUT] {}",
                                        String::from_utf8_lossy(line.as_ref())
                                    ));
                                }
                            }
                        }
                        CommandEvent::Stderr(ref line) => {
                            let text = String::from_utf8_lossy(line.as_ref());
                            eprintln!("Sidecar: {text}");
                            if let Ok(mut guard) = log_file.lock() {
                                if let Some(ref mut file) = *guard {
                                    file.write_line(&format!("[STDERR] {text}"));
                                }
                            }
                        }
                        _ => {}
                    }
                }
            });

            // Do not keep the whole Tauri setup hidden while OCR dependencies
            // warm up. A failed sidecar health check must be visible to the
            // user as a recoverable desktop state, not look like a hung app in
            // Activity Monitor.
            let health = format!("http://127.0.0.1:{port}/health");
            let health_data_dir = data_dir.clone();
            let health_log_path = log_path.clone();
            std::thread::spawn(move || {
                let started = Instant::now();
                while started.elapsed() < Duration::from_secs(45) {
                    if ureq::get(&health).call().is_ok() {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(200));
                }
                eprintln!(
                    "Sidecar health check failed after 45s. Port: {port}, \
                     Data dir: {}, Log: {}",
                    health_data_dir.display(),
                    health_log_path.display()
                );
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Smart Exam Converter")
        .run(|app, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                let state = app.state::<Arc<SidecarState>>();
                if let Some(child) = state.child.lock().expect("sidecar lock").take() {
                    let _ = child.kill();
                };
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{legacy_data_dir, normalize_hardware_parts};
    use std::path::Path;

    #[test]
    fn legacy_data_path_is_an_exact_sibling_of_the_new_bundle_directory() {
        let current = Path::new("/local/app-data/com.toeicdoc.app");
        assert_eq!(
            legacy_data_dir(current).unwrap(),
            Path::new("/local/app-data/online.congnhat.exam")
        );
    }

    #[test]
    fn hardware_identity_does_not_depend_on_adapter_order() {
        let first = normalize_hardware_parts(
            Some("{ABC-123}".into()),
            vec!["AA-BB-CC-DD-EE-FF".into(), "11:22:33:44:55:66".into()],
        )
        .unwrap();
        let second = normalize_hardware_parts(
            Some("abc-123".into()),
            vec!["11-22-33-44-55-66".into(), "aabbccddeeff".into()],
        )
        .unwrap();
        assert_eq!(first, second);
    }
}
