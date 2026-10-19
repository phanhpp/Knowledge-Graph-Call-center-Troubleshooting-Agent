"use client";

import type { CustomerLookup, CustomerRecord } from "@/lib/use-customer";

/** Reads a nested field ("device.model") from the CRM record, or undefined. */
function field(record: CustomerRecord, path: string): unknown {
  return path.split(".").reduce<unknown>((value, key) => (value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined), record);
}

function show(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  if (value === undefined || value === null || value === "") return "—";
  return String(value);
}

/** The fields the walks check most, in the order a consultant reads them. */
const ROWS: [label: string, path: string][] = [
  ["Account", "account_status"],
  ["Plan", "plan_name"],
  ["Plan status", "plan_status"],
  ["Roaming on", "roaming_flag"],
  ["Device", "device.model"],
  ["Roaming-compatible", "device.device_roaming_compatible"],
  ["SIM", "sim_status"],
  ["Balance", "billing.current_balance"],
  ["Last payment", "billing.last_payment_status"],
  ["Contract ends", "contract_end_date"],
  ["Tenure (months)", "tenure_months"],
];

/** The current customer's CRM record (GET /customers/{id}, through the proxy route). */
export function CustomerCard({ lookup }: { lookup: CustomerLookup }) {
  return (
    <section aria-label="Customer card" className="rounded-xl border border-border bg-surface p-3">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-tertiary">Customer</h2>

      {lookup.status === "none" && <p className="text-sm text-text-secondary">No customer yet. Enter a customer ID to see their record.</p>}
      {lookup.status === "loading" && <p className="text-sm text-text-tertiary">Loading {lookup.id}…</p>}
      {lookup.status === "missing" && <p className="text-sm text-error">No customer {lookup.id} in the CRM.</p>}
      {lookup.status === "error" && <p className="text-sm text-error">Couldn&apos;t load {lookup.id}.</p>}

      {lookup.status === "found" && (
        <div>
          <p className="font-medium">{show(field(lookup.record, "name"))}</p>
          <p className="mb-2 font-mono text-xs text-text-tertiary">{lookup.record.customer_id}</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            {ROWS.map(([label, path]) => (
              <div key={path} className="contents">
                <dt className="text-text-tertiary">{label}</dt>
                <dd>{show(field(lookup.record, path))}</dd>
              </div>
            ))}
            <dt className="text-text-tertiary">Open tickets</dt>
            <dd>
              {show(
                Array.isArray(lookup.record.recent_tickets)
                  ? (lookup.record.recent_tickets as { resolved?: boolean }[]).filter((t) => !t.resolved).length
                  : 0,
              )}
            </dd>
          </dl>
        </div>
      )}
    </section>
  );
}
