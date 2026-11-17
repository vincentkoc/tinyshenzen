import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function storage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  });
  return data;
}

/** A fresh copy of the module: the store keeps its snapshot between calls. */
const fresh = async () => {
  vi.resetModules();
  return import("../prefs");
};

describe("prefsStore and the night setting", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("starts off when nothing is stored", async () => {
    storage();
    const { prefsStore } = await fresh();
    expect(prefsStore.getSnapshot().night).toBe("off");
  });

  it("saves a change, tells listeners, and reads it back on the next visit", async () => {
    const data = storage();
    const { prefsStore, PREFS_KEY } = await fresh();
    const heard = vi.fn();
    vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
    const off = prefsStore.subscribe(heard);
    prefsStore.set({ night: "on" });
    expect(heard).toHaveBeenCalledTimes(1);
    expect(prefsStore.getSnapshot().night).toBe("on");
    expect(JSON.parse(data.get(PREFS_KEY) ?? "{}").night).toBe("on");
    off();

    const again = await fresh();
    expect(again.prefsStore.getSnapshot().night).toBe("on");
  });

  it("keeps the other settings when the night one changes", async () => {
    storage();
    const { prefsStore } = await fresh();
    prefsStore.set({ minimap: false, theme: "dark" });
    prefsStore.set({ night: "auto" });
    const prefs = prefsStore.getSnapshot();
    expect(prefs).toMatchObject({ minimap: false, theme: "dark", night: "auto" });
  });

  it("drops a junk value instead of saving it", async () => {
    storage();
    const { prefsStore } = await fresh();
    prefsStore.set({ night: "on" });
    prefsStore.set({ night: "bright" as never });
    expect(prefsStore.getSnapshot().night).toBe("off");
  });

  it("still switches for this visit when storage is blocked", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    });
    const { prefsStore } = await fresh();
    expect(() => prefsStore.set({ night: "on" })).not.toThrow();
    expect(prefsStore.getSnapshot().night).toBe("on");
  });
});
