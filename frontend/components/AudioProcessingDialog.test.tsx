// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import AudioProcessingDialog from "./AudioProcessingDialog";

afterEach(cleanup);

describe("AudioProcessingDialog", () => {
  it("shows real audio progress and explains that OCR runs next", () => {
    render(
      <AudioProcessingDialog
        mode="full"
        progress={42}
        stage="Đang cắt audio 23/55"
      />,
    );

    expect(
      screen.getByRole("dialog", { name: "Đang xử lý Audio Full" }),
    ).toBeTruthy();
    expect(screen.getByText("Đang cắt audio 23/55")).toBeTruthy();
    expect(screen.getByText("42%")).toBeTruthy();
    expect(screen.getByText(/OCR tài liệu sẽ tự bắt đầu/)).toBeTruthy();
  });

  it("bounds an invalid progress value before rendering the bar", () => {
    render(
      <AudioProcessingDialog
        mode="question_groups"
        progress={140}
        stage="Đang chuẩn hóa audio"
      />,
    );

    expect(screen.getByText("100%")).toBeTruthy();
    expect(
      (screen.getByLabelText("Tiến độ xử lý audio 100%") as HTMLElement)
        .style.width,
    ).toBe("100%");
  });

  it("shows independent audio and OCR progress when server runs both", () => {
    render(
      <AudioProcessingDialog
        mode="full"
        progress={51}
        stage="Audio 55% · OCR 50%"
        parallel
        audioProgress={55}
        ocrProgress={50}
        audioStage="Đang cắt audio"
        ocrStage="Đang đọc trang 8/20"
      />,
    );

    expect(
      screen.getByRole("dialog", { name: "Đang xử lý Audio và OCR" }),
    ).toBeTruthy();
    expect(screen.getByLabelText("Tiến độ Audio 55%")).toBeTruthy();
    expect(screen.getByLabelText("Tiến độ OCR tài liệu 50%")).toBeTruthy();
    expect(screen.getByText("Đang đọc trang 8/20")).toBeTruthy();
  });
});
