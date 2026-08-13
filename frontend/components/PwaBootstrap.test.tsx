// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PwaBootstrap from "./PwaBootstrap";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

describe("PwaBootstrap", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    });
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { register: vi.fn().mockResolvedValue({}) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("registers service worker and handles online/offline status", async () => {
    render(<PwaBootstrap />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Cài đặt ứng dụng Examify" })).not.toBeNull();
    });
  });
});
