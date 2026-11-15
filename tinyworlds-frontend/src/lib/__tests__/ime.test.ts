import { describe, expect, it } from "vitest";
import { isComposing } from "../ime";

describe("isComposing", () => {
  it("is true while the IME composes", () => {
    expect(isComposing({ nativeEvent: { isComposing: true }, keyCode: 13 })).toBe(true);
  });
  it("is true for Safari's composing keyCode 229", () => {
    expect(isComposing({ nativeEvent: { isComposing: false }, keyCode: 229 })).toBe(true);
  });
  it("is false for a plain Enter", () => {
    expect(isComposing({ nativeEvent: { isComposing: false }, keyCode: 13 })).toBe(false);
    expect(isComposing({ nativeEvent: {} })).toBe(false);
  });
});
