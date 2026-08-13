import { describe, expect, it } from "vitest";

import { isDictionaryAvailable } from "./dictionary";

describe("dictionary availability", () => {
  it("allows lookup only in Practice", () => {
    expect(isDictionaryAvailable("practice")).toBe(true);
    expect(isDictionaryAvailable("exam")).toBe(false);
  });
});
