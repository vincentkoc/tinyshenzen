import { describe, expect, it } from "vitest";
import { DICTIONARIES } from "@/i18n";
import { viewReadout } from "../readout";

const EN = DICTIONARIES["en-US"].credit;
const PT = DICTIONARIES["pt-BR"].credit;

describe("viewReadout", () => {
  it("says where the middle of the view is and how far in", () => {
    expect(viewReadout(40.7484, -73.9857, 1.5, EN)).toBe("40.7484° N  73.9857° W  ·  1.5×");
    expect(viewReadout(-21.1775, -47.8103, 0.25, EN)).toBe("21.1775° S  47.8103° W  ·  25%");
  });

  it("writes the numbers the way the language does", () => {
    expect(viewReadout(-21.1775, -47.8103, 2, PT)).toBe("21,1775° S  47,8103° O  ·  2,0×");
  });

  it("leaves the coordinates out for a place that does not exist: only the zoom is left", () => {
    expect(viewReadout(0.0123, -0.0456, 1.5, EN, true)).toBe("1.5×");
    expect(viewReadout(0.0123, -0.0456, 0.5, PT, true)).toBe("50%");
    expect(viewReadout(0.0123, -0.0456, 3, PT, true)).toBe("3,0×");
  });

  it("rounds the degrees to the world's digits", () => {
    expect(viewReadout(22.54551, 114.06834, 1.5, EN, false, 3)).toBe(
      "22.546° N  114.068° E  ·  1.5×",
    );
    expect(viewReadout(22.54551, 114.06834, 1.5, PT, false, 3)).toBe(
      "22,546° N  114,068° L  ·  1,5×",
    );
  });
});
