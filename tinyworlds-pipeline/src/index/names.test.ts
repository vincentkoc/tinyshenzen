import { describe, expect, it } from "vitest";
import { englishAlias } from "./names";

describe("englishAlias", () => {
  it("lists the English name beside a Chinese one", () => {
    expect(englishAlias("莲花山公园", "Lianhuashan Park")).toBe("Lianhuashan Park");
    expect(englishAlias("高新园", " Hi-Tech Park ")).toBe("Hi-Tech Park");
  });
  it("leaves a name already carrying its English alone", () => {
    expect(englishAlias("深圳灣公路大橋 Shenzhen Bay Bridge", "Shenzhen Bay Bridge")).toBeUndefined();
  });
  it("adds nothing to names outside Chinese, or without English", () => {
    expect(englishAlias("Avenida Paulista", "Paulista Avenue")).toBeUndefined();
    expect(englishAlias("福田", null)).toBeUndefined();
    expect(englishAlias("福田", "")).toBeUndefined();
    expect(englishAlias("福田", "福田区")).toBeUndefined();
  });
});
