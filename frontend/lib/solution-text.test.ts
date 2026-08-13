import { describe, expect, it } from "vitest";

import { solutionTextParagraphs } from "./solution-text";

describe("solutionTextParagraphs", () => {
  it("unwraps OCR lines and keeps each speaker in a separate paragraph", () => {
    expect(
      solutionTextParagraphs(
        "W: Did you see the\nfocus group results?\nM: Yes. It was\nvery useful.",
      ),
    ).toEqual([
      "W: Did you see the focus group results?",
      "M: Yes. It was very useful.",
    ]);
  });

  it("keeps translated answer options separate", () => {
    expect(
      solutionTextParagraphs("(A) Phương án\nđầu tiên\n(B) Phương án\nthứ hai"),
    ).toEqual(["(A) Phương án đầu tiên", "(B) Phương án thứ hai"]);
  });

  it("rejoins words split by PDF hyphenation", () => {
    expect(solutionTextParagraphs("factory-\ntrained specialists")).toEqual([
      "factory-trained specialists",
    ]);
  });

  it("restores inline Vietnamese speaker paragraphs with numbered speakers", () => {
    expect(
      solutionTextParagraphs(
        "Nữ: Chào buổi sáng! Chào mừng hai ông đến với ngân hàng Jasper. Nam 1: Cảm ơn cô đã gặp chúng tôi. Nữ: Hãy cho tôi biết thêm nhé? Nam 2: Mười năm trước chúng tôi mở cửa hàng.",
      ),
    ).toEqual([
      "Nữ: Chào buổi sáng! Chào mừng hai ông đến với ngân hàng Jasper.",
      "Nam 1: Cảm ơn cô đã gặp chúng tôi.",
      "Nữ: Hãy cho tôi biết thêm nhé?",
      "Nam 2: Mười năm trước chúng tôi mở cửa hàng.",
    ]);
  });
});
