import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { clamp, readStoredSize, usePanelSize } from "@/lib/panel-size";

/* Panel sizes the user drags, remembered in this browser (localStorage), never trusted blindly. */

const KEY = "test:panel";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("clamp", () => {
  it("keeps a size within its limits", () => {
    expect(clamp(50, 100, 300)).toBe(100);
    expect(clamp(200, 100, 300)).toBe(200);
    expect(clamp(900, 100, 300)).toBe(300);
  });
});

describe("the stored size", () => {
  it("is read back, clamped", () => {
    window.localStorage.setItem(KEY, "250");
    expect(readStoredSize(KEY, 320, 100, 300)).toBe(250);
    window.localStorage.setItem(KEY, "5000");
    expect(readStoredSize(KEY, 320, 100, 300)).toBe(300);
  });

  it("falls back to the default when missing or not a number", () => {
    expect(readStoredSize(KEY, 320, 100, 400)).toBe(320);
    window.localStorage.setItem(KEY, "wide");
    expect(readStoredSize(KEY, 320, 100, 400)).toBe(320);
  });

  it("falls back to the default when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readStoredSize(KEY, 320, 100, 400)).toBe(320);
  });
});

describe("usePanelSize", () => {
  it("starts from the stored size", async () => {
    window.localStorage.setItem(KEY, "410");
    const { result } = renderHook(() => usePanelSize(KEY, 320, 260, 900));
    await waitFor(() => expect(result.current[0]).toBe(410));
  });

  it("clamps, stores and resets", () => {
    const { result } = renderHook(() => usePanelSize(KEY, 320, 260, 900));

    act(() => result.current[1](2000));
    expect(result.current[0]).toBe(900);
    expect(window.localStorage.getItem(KEY)).toBe("900");

    act(() => result.current[2]());
    expect(result.current[0]).toBe(320);
  });

  it("still resizes when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => usePanelSize(KEY, 320, 260, 900));
    act(() => result.current[1](500));
    expect(result.current[0]).toBe(500);
  });
});
