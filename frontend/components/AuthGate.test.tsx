// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AuthGate from "./AuthGate";

const mocks = vi.hoisted(() => ({
  pathname: "/",
  push: vi.fn(),
  replace: vi.fn(),
  resolveAuthState: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}));

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  DESKTOP_APP_VERSION: "0.1.2",
  isDesktop: () => true,
  resolveAuthState: mocks.resolveAuthState,
  roleLanding: () => "/",
}));

vi.mock("@/lib/device", () => ({
  getDeviceIdentity: vi.fn().mockResolvedValue("device-identity"),
  isDeviceActivated: () => true,
  markDeviceActivated: vi.fn(),
}));

describe("AuthGate desktop navigation", () => {
  afterEach(() => {
    cleanup();
    mocks.pathname = "/";
    vi.clearAllMocks();
  });

  it("treats static-export paths with a trailing slash as public", async () => {
    mocks.pathname = "/login/";
    render(<AuthGate><div>Login page</div></AuthGate>);

    expect(await screen.findByText("Login page")).toBeTruthy();
    expect(mocks.resolveAuthState).not.toHaveBeenCalled();
  });

  it("ignores an old auth response after navigating to login", async () => {
    let finishCheck: ((value: object) => void) | undefined;
    mocks.resolveAuthState.mockReturnValueOnce(
      new Promise((resolve) => {
        finishCheck = resolve;
      }),
    );
    const view = render(<AuthGate><div>Login page</div></AuthGate>);
    expect(mocks.resolveAuthState).toHaveBeenCalledOnce();

    mocks.pathname = "/login/";
    view.rerender(<AuthGate><div>Login page</div></AuthGate>);
    expect(await screen.findByText("Login page")).toBeTruthy();

    finishCheck?.({
      state: "activation_required",
      authenticated: false,
      role: null,
      active_class_count: 0,
    });
    await waitFor(() => {
      expect(screen.queryByText("Kích hoạt Examify")).toBeNull();
    });
  });
});
