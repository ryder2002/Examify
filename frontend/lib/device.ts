export function getDeviceKey() {
  const storageKey = "smart-exam-device-key";
  let value = localStorage.getItem(storageKey);
  if (!value) {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    localStorage.setItem(storageKey, value);
  }
  return value;
}

const ACTIVATED_DEVICE_MARKER = "smart-exam-device-activated";

export function markDeviceActivated(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACTIVATED_DEVICE_MARKER, "1");
}

export function isDeviceActivated(): boolean {
  return (
    typeof window !== "undefined" &&
    localStorage.getItem(ACTIVATED_DEVICE_MARKER) === "1"
  );
}

export async function getDeviceIdentity(): Promise<string> {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string>("device_identity");
  }
  return getDeviceKey();
}
