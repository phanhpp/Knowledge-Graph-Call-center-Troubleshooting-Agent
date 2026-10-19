import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { AGENT_API_PATH, ASSISTANT_ID, resolveAgentUrl } from "@/lib/agent";

/* The URL helper, plus one cross-package check: the graph the page asks for must exist in the
   repo's langgraph.json. Nothing else checks that; a rename on the Python side would otherwise
   show up as a 404 at runtime. (Adapted from an earlier human-in-the-loop project of mine.) */

function setSearch(search: string) {
  window.history.replaceState({}, "", `${window.location.pathname}${search}`);
}

afterEach(() => setSearch(""));

describe("resolveAgentUrl", () => {
  it("returns the same-origin proxy path by default", () => {
    expect(resolveAgentUrl()).toBe(`${window.location.origin}${AGENT_API_PATH}`);
  });

  it("honours an ?agentServer= override", () => {
    setSearch("?agentServer=http://127.0.0.1:2024");
    expect(resolveAgentUrl()).toBe("http://127.0.0.1:2024");
  });

  it("ignores the override when it is 'local'", () => {
    setSearch("?agentServer=local");
    expect(resolveAgentUrl()).toBe(`${window.location.origin}${AGENT_API_PATH}`);
  });
});

describe("assistant id", () => {
  it("names the graph langgraph.json registers", () => {
    // From cwd (web/): under jsdom import.meta.url is an http: URL, which readFileSync rejects.
    const config = JSON.parse(readFileSync(resolve(process.cwd(), "../langgraph.json"), "utf8"));
    expect(Object.keys(config.graphs)).toContain(ASSISTANT_ID);
  });
});
