import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { AUTHOR_URL } from "@/site";

const source = readFileSync(new URL("../splash.tsx", import.meta.url), "utf8");

describe("Splash", () => {
  it("credits Vincent Koc at the foot, with the donate heart, linking to his GitHub", () => {
    expect(source).toContain("href={AUTHOR_URL}");
    expect(source).toContain("{t.credit.madeBy}");
    expect(source).toContain("<Heart");
    expect(source).toContain("fill-taxi text-taxi");
    expect(source).toMatch(/absolute[\s\S]*bottom-\(--edge-b\)/);
    expect(AUTHOR_URL).toBe("https://github.com/vincentkoc");
  });
});
