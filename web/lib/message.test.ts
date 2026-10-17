import { AIMessage } from "langchain";
import { describe, expect, it } from "vitest";

import {
  citationsOf,
  customerOf,
  humanMessage,
  normalizeCustomerId,
  runIdOf,
  savedKwargs,
  sourcesOf,
  withSavedKwargs,
} from "@/lib/message";

/* The message formats the agent reads and writes (agent/utils.py on the Python side). */

const REPLY_KWARGS = {
  citations: ["ACT-015", "R012"],
  sources: [{ id: "R012", kind: "rule", title: "Tier 2 escalations require a documented troubleshooting summary", text: "…" }],
  run_id: "01a14739-700c-7cc3-9248-985e7d3482b0",
};

describe("the consultant's message", () => {
  it("names its customer in additional_kwargs", () => {
    const message = humanMessage("customer cannot activate roaming", "CUST-1001");
    expect(message.additional_kwargs).toEqual({ customer_id: "CUST-1001" });
    expect(message.text).toBe("customer cannot activate roaming");
  });

  it("names no customer for a general question", () => {
    expect(humanMessage("What is the SIM replacement fee?", null).additional_kwargs).toEqual({});
  });

  it("reads a typed customer ID leniently and rejects anything else", () => {
    expect(normalizeCustomerId(" cust-1001 ")).toBe("CUST-1001");
    expect(normalizeCustomerId("CUST-10")).toBeNull();
    expect(normalizeCustomerId("1001")).toBeNull();
    expect(normalizeCustomerId("")).toBeNull();
  });
});

describe("a reply", () => {
  it("gives its citations, sources and run id", () => {
    const reply = { additional_kwargs: REPLY_KWARGS };
    expect(citationsOf(reply)).toEqual(["ACT-015", "R012"]);
    expect(sourcesOf(reply).map((s) => s.id)).toEqual(["R012"]);
    expect(runIdOf(reply)).toBe(REPLY_KWARGS.run_id);
  });

  it("has none of them when it cites nothing or was saved before they existed", () => {
    for (const reply of [{ additional_kwargs: { citations: [] } }, { additional_kwargs: {} }, {}, null]) {
      expect(citationsOf(reply)).toEqual([]);
      expect(sourcesOf(reply)).toEqual([]);
      expect(runIdOf(reply)).toBeUndefined();
    }
  });

  it("reads the customer a consultant message named", () => {
    expect(customerOf({ additional_kwargs: { customer_id: "CUST-1005" } })).toBe("CUST-1005");
    expect(customerOf({ additional_kwargs: {} })).toBeUndefined();
  });
});

describe("the saved copy wins over the streamed one", () => {
  // REGRESSION: a reply streamed live arrived with an empty additional_kwargs (no chips, no sources,
  // no 👍 until a reload). The thread's saved state has them, under the same id.
  const streamed = new AIMessage({ id: "m1", content: "Escalate to Tier 2", additional_kwargs: {} });
  const saved = savedKwargs([{ id: "m1", type: "ai", content: "Escalate to Tier 2", additional_kwargs: REPLY_KWARGS }]);

  it("takes citations, sources and run id from the saved state", () => {
    const view = withSavedKwargs(streamed, saved);
    expect(citationsOf(view)).toEqual(["ACT-015", "R012"]);
    expect(runIdOf(view)).toBe(REPLY_KWARGS.run_id);
  });

  it("falls back to the message itself when the state doesn't have it yet", () => {
    const own = new AIMessage({ id: "m2", content: "x", additional_kwargs: { citations: ["KB-006"] } });
    expect(citationsOf(withSavedKwargs(own, saved))).toEqual(["KB-006"]);
  });

  it("ignores state entries without an id", () => {
    expect(savedKwargs([{ additional_kwargs: REPLY_KWARGS }, null, "junk"]).size).toBe(0);
    expect(savedKwargs(undefined).size).toBe(0);
  });
});
