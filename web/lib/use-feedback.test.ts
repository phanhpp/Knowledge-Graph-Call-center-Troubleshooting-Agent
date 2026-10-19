import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFeedback } from "@/lib/use-feedback";

/*
 * The thumbs lookup and the vote. (Adapted from an earlier human-in-the-loop project of mine: here each reply carries its
 * own run id in additional_kwargs.run_id.)
 *
 * Pinned: seeding recorded votes must not overwrite a vote already in flight, because LangSmith
 * ingestion is backgrounded and a fetch right after voting can legitimately not see it yet.
 */

const REPLY = { id: "m1", additional_kwargs: { run_id: "run-1" } };
const OTHER = { id: "m2", additional_kwargs: { run_id: "run-2" } };
const QUESTION = { id: "h1", additional_kwargs: { customer_id: "CUST-1001" } };

/* Built once per test, never inside the render callback, so the hook sees stable inputs. */
const conversation = [QUESTION, REPLY, OTHER];

function stubFetch(votes: Record<string, { score: 0 | 1 }> = {}) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ votes }) });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => stubFetch());
afterEach(() => vi.unstubAllGlobals());

describe("which messages can be rated", () => {
  it("finds the run a reply carries", () => {
    const { result } = renderHook(() => useFeedback(conversation, "t1"));
    expect(result.current.runFor("m1")).toBe("run-1");
    expect(result.current.runFor("m2")).toBe("run-2");
  });

  it("can't rate a consultant message, an unknown id or no id", () => {
    const { result } = renderHook(() => useFeedback(conversation, "t1"));
    expect(result.current.runFor("h1")).toBeUndefined();
    expect(result.current.runFor("unknown")).toBeUndefined();
    expect(result.current.runFor(undefined)).toBeUndefined();
  });
});

describe("voting", () => {
  it("posts the run id and score to the thread's feedback route", async () => {
    const fetchMock = stubFetch();
    const { result } = renderHook(() => useFeedback(conversation, "t1"));

    await act(async () => {
      result.current.vote("m1", 1);
    });

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post![0]).toMatch(/\/api\/langgraph\/feedback\/t1$/);
    expect(JSON.parse(post![1].body)).toMatchObject({ message_id: "m1", run_id: "run-1", score: 1 });
  });

  it("marks the vote sent on a 200", async () => {
    const { result } = renderHook(() => useFeedback(conversation, "t1"));
    await act(async () => {
      result.current.vote("m1", 1);
    });
    await waitFor(() => expect(result.current.voteFor("m1")).toEqual({ score: 1, status: "sent" }));
  });

  it("marks the vote failed on a 400, which still resolves the promise", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    const { result } = renderHook(() => useFeedback(conversation, "t1"));
    await act(async () => {
      result.current.vote("m1", 1);
    });
    await waitFor(() => expect(result.current.voteFor("m1")?.status).toBe("failed"));
  });

  it("marks the vote failed when the request throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { result } = renderHook(() => useFeedback(conversation, "t1"));
    await act(async () => {
      result.current.vote("m1", 0);
    });
    await waitFor(() => expect(result.current.voteFor("m1")?.status).toBe("failed"));
  });

  it("does nothing for a message with no run, or without a thread", async () => {
    const fetchMock = stubFetch();
    const { result: noRun } = renderHook(() => useFeedback(conversation, "t1"));
    await act(async () => {
      noRun.current.vote("h1", 1);
    });
    const { result: noThread } = renderHook(() => useFeedback(conversation, null));
    await act(async () => {
      noThread.current.vote("m1", 1);
    });
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
});

describe("votes recorded earlier", () => {
  it("come back as already voted after a reload", async () => {
    const fetchMock = stubFetch({ "run-1": { score: 1 } });
    const { result } = renderHook(() => useFeedback(conversation, "t1"));

    await waitFor(() => expect(result.current.voteFor("m1")).toEqual({ score: 1, status: "sent" }));
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/feedback\/t1\/votes\?run_ids=run-1,run-2$/);
    expect(result.current.voteFor("m2")).toBeUndefined();
  });

  it("don't overwrite a vote already in flight", async () => {
    stubFetch({});
    const { result } = renderHook(() => useFeedback(conversation, "t1"));
    await act(async () => {
      result.current.vote("m1", 0);
    });
    await waitFor(() => expect(result.current.voteFor("m1")).toBeDefined());
    expect(result.current.voteFor("m1")?.score).toBe(0);
  });

  it("asks for nothing when no reply has a run", () => {
    const fetchMock = stubFetch();
    const noRuns = [QUESTION];
    renderHook(() => useFeedback(noRuns, "t1"));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
