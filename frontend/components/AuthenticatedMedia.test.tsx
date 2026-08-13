// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthenticatedImage } from "./AuthenticatedMedia";

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock("@/lib/api", () => ({
  apiFetch,
  assetUrl: (url: string) => url,
}));

describe("AuthenticatedImage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:authenticated-image"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("loads private extraction media through the authenticated API client", async () => {
    apiFetch.mockResolvedValue(
      new Response(new Blob(["image"]), {
        status: 200,
        headers: { "Content-Type": "image/webp" },
      }),
    );

    render(
      <AuthenticatedImage
        source="/api/extractions/job-1/assets/crop.webp"
        alt="Bản crop"
        className="h-40"
      />,
    );

    expect(screen.getByText("Đang tải ảnh...")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByAltText("Bản crop").getAttribute("src")).toBe(
        "blob:authenticated-image",
      ),
    );
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/extractions/job-1/assets/crop.webp",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});
