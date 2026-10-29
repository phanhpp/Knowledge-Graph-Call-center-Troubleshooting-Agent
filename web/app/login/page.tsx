"use client";

import { useState } from "react";

import { safeNext } from "@/lib/safe-next";

/** The password form. Signing in sets a cookie (app/api/login) that proxy.ts checks on every request. */
export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password }),
    }).catch(() => null);
    if (res?.ok) {
      // Back to where proxy.ts sent us from, but never to another site (lib/safe-next.ts).
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.assign(safeNext(next, window.location.origin));
      return;
    }
    setBusy(false);
    setError(res?.status === 401 ? "Wrong password." : "Couldn't sign in. Try again.");
  }

  return (
    <main className="flex h-full items-center justify-center bg-surface-secondary px-4">
      <form onSubmit={signIn} className="w-full max-w-sm space-y-4 rounded-xl border border-border bg-surface p-6 shadow-sm">
        <div>
          <h1 className="text-lg font-semibold">Super Agent</h1>
          <p className="mt-1 text-sm text-text-secondary">Call-centre copilot. Enter the team password.</p>
        </div>
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Password"
          aria-label="Password"
          className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
        />
        {error && (
          <p role="alert" className="text-sm text-error">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !password}
          className="w-full rounded-md bg-primary-dark px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
