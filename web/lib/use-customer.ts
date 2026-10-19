"use client";

/*
 * The current customer's CRM record, for the customer card: GET /customers/{id}
 * (agent/routes/customers.py, through the proxy route).
 *
 * Read fresh from the server rather than from graph state: the graph's crm_context is set only on
 * troubleshooting turns and never cleared, so it can describe a previous customer.
 */

import { useEffect, useState } from "react";

import { resolveAgentUrl } from "@/lib/agent";

export type CustomerRecord = Record<string, unknown> & { customer_id: string };

export type CustomerLookup =
  | { status: "none" }
  | { status: "loading"; id: string }
  | { status: "found"; id: string; record: CustomerRecord }
  | { status: "missing"; id: string }
  | { status: "error"; id: string };

export function useCustomer(customerId: string | null | undefined): CustomerLookup {
  const [base] = useState(resolveAgentUrl);
  const [lookup, setLookup] = useState<CustomerLookup>({ status: "none" });

  useEffect(() => {
    if (!customerId) {
      setLookup({ status: "none" });
      return;
    }
    let cancelled = false;
    setLookup({ status: "loading", id: customerId });

    fetch(`${base}/customers/${encodeURIComponent(customerId)}`, { cache: "no-store" })
      .then(async (res) => {
        if (cancelled) return;
        if (res.status === 404) setLookup({ status: "missing", id: customerId });
        else if (!res.ok) setLookup({ status: "error", id: customerId });
        else setLookup({ status: "found", id: customerId, record: (await res.json()) as CustomerRecord });
      })
      .catch(() => {
        if (!cancelled) setLookup({ status: "error", id: customerId });
      });

    return () => {
      cancelled = true;
    };
  }, [base, customerId]);

  return lookup;
}
