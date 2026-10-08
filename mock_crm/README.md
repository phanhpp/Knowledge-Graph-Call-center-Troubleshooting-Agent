# mock_crm

This folder answers yes/no questions about a customer, for example "is there an outage where they live?" or "do they still owe money on their phone?".

In the real system these answers come from the company's CRM and other internal systems. This project has no access to those, so each one is replaced here by a JSON file in `data/`. Nothing calls the network, and nothing here changes any data. It only reads.

## Core components

- **Data files (**`data/*.json`**)**: 60 customer records (`crm_records.json`) and three lookup tables behind them: network status (`network_status.json`), device financing (`device_financing.json`) and fraud records (`fraud_gateway.json`). A customer record links to them by `service_area.zip_code`, `device.imei` and `customer_id`.
- **Condition evaluator (**`condition_evaluator.py`**)**: answers a condition for one customer, `True` or `False`, in one of two ways:
  1. **Simple check**: compares one field of the CRM record with a value, e.g. `roaming_flag == false`.
  2. **Chain check**: runs several lookups in order, each using the previous one's result, e.g. read the customer's `zip_code` from the CRM record, then look that ZIP code up in `network_status.json` for an active outage.
  Ticket checks (reading a ticket's free text) are not done here: they need an LLM, so `agent/ticket_interpreter.py` handles them.
- **Stores (**`*_store.py`**)**: one per data file. Each loads its file once, keeps it in memory, and returns one record by key. A missing key raises an error named after it, e.g. `ZipNotFoundError`, `ImeiNotFoundError`, `CustomerNotFoundError`.



## Files


| File                        | Data file it reads      | What it does                                                                              |
| --------------------------- | ----------------------- | ----------------------------------------------------------------------------------------- |
| `crm_store.py`              | `crm_records.json`      | `get_customer(customer_id)` returns the customer's CRM record.                            |
| `network_status_store.py`   | `network_status.json`   | `get_network_status(zip_code)` says whether an outage is active in that ZIP.              |
| `device_financing_store.py` | `device_financing.json` | `get_device_financing(imei)` returns how much the customer still owes on the device.      |
| `fraud_gateway_store.py`    | `fraud_gateway.json`    | `get_fraud_status(customer_id)` says whether the account is flagged as high fraud risk.   |
| `condition_evaluator.py`    | `conditions.json`       | Answers a condition. Runs the simple check or the chain check depending on the condition. |


The four `*_store.py` files all work the same way. They load their JSON file once, keep it in memory, and look records up by key. If the key isn't found they raise an error named after the lookup, for example `ZipNotFoundError`.

## Terms used below

- **CRM record**: everything we know about one customer (ID, plan, device, ZIP code and so on). Stored in `data/crm_records.json`, 60 customers.
- **IMEI**: a 15-digit number that identifies one physical phone (the handset). It is not the SIM card and not the phone number. Each customer's record holds the IMEI of their device (`device.imei`), and it is the key for the device-financing lookup.
- **Condition**: one yes/no question, stored in `data/conditions.json`. Example: `COND-001` is "is roaming disabled?".
- **Procedure**: a list of steps a consultant follows to fix a problem. Each step asks one condition, and the answer is **yes** when the condition is true for this customer (for example, their roaming really is disabled) or **no** when it is false. On yes, the procedure stops and gives the consultant that step's action. On no, it moves on to the next step. The last step has an action for no too, so a procedure always ends with an answer.

This folder's job is to answer a condition for a given customer: yes (`True`) or no (`False`).

## How the mock CRM works

```
 inject_crm(): once per turn, first thing               [agent/supervisor.py]
 |
 +--> get_customer("CUST-1001")                         [mock_crm/crm_store.py]
 |      reads data/crm_records.json (read once)
 |      unknown ID --> CustomerNotFoundError
 |  puts the record in state["crm_context"]
 v
 procedure walk, for each step                          [agent/procedural_executor.py]
 |
 v
 fetch_step(): the condition ID, from Neo4j             [agent/procedural_executor.py]
 +--> get_condition(id): the full condition             [mock_crm/condition_evaluator.py]
 |
 +-- field or chain check:
 |   evaluate_field_condition()                         [agent/procedural_executor.py]
 |   +--> resolve_condition(crm_context, cond)          [mock_crm/condition_evaluator.py]
 |        |
 |        +-- no chain: evaluate_condition()            [mock_crm/condition_evaluator.py]
 |        |     COND-001: roaming_flag == false ?
 |        |     (no API called; verification_api
 |        |      is only a label)
 |        |
 |        +-- chain: evaluate_chain_condition()         [mock_crm/condition_evaluator.py]
 |              COND-023: zip "94103" from crm_context
 |              +--> get_network_status("94103")        [mock_crm/network_status_store.py]
 |                     outage_active == true ?
 |              other chains, the same way:
 |              +--> get_device_financing(imei)         [mock_crm/device_financing_store.py]
 |              +--> get_fraud_status(customer_id)      [mock_crm/fraud_gateway_store.py]
 |
 +-- ticket check:
     interpret_ticket_condition()                       [agent/procedural_executor.py]
     +--> Claude reads recent_tickets[0]                [agent/ticket_interpreter.py]
```

- **Read-only.** Nothing writes to the CRM or the lookup files.
- **The customer's record is loaded once per turn**, by `inject_crm` (`agent/supervisor.py`). Every check after that reads the copy in state (`crm_context`).
- **Only chain checks reach the other lookup files**, keyed by a value the previous lookup produced.



## How the data files connect

Files point to each other by ID:

```
[ procedures.json ]
  │
  ├─► condition_id ──► [ conditions.json ] ──► (Reads Customer Data)
  │                                                  │
  │                                                  ├─► [ crm_records.json ]
  │                                                  │      │
  │                                                  │      ├─► zip_code ──► [ network_status.json ]
  │                                                  │      ├─► imei ──────► [ device_financing.json ]
  │                                                  │      └─► customer_id─► [ fraud_gateway.json ]
  │
  ├─► action_if_true_id  ──┐
  └─► action_if_false_id ──┴─► [ actions.json ] ◄── applies_to_action_ids ── [ rules.json ]
                                                                                   ▲
                                                                                   │ related_rule_ids
                                                                           [ kb_articles.json ]

```

Conditions and actions are stored once and shared by many steps. `etl/validate.py` checks every ID link above before the data is loaded, except the three lookups by ZIP code, IMEI and customer ID. The main README walks through one procedure, `PROC-001`, step by step.

## The two kinds of condition it answers

**1. Simple check.** Compare one field of the CRM record with a value.

`COND-001` says: `roaming_flag == false`. The code reads `roaming_flag` from the customer's record, compares it to `false`, and returns `True` or `False`.

**2. Chain check.** One field isn't enough, so several lookups run in order. Each lookup uses the result of the one before it.

`COND-023` asks "is there an active outage where the customer lives?". For customer `CUST-1001`:

1. Read `service_area.zip_code` from the CRM record. The result is `94103`.
2. Look up `94103` in `data/network_status.json`. It has `outage_active: true`.
3. The condition needs `outage_active == true`. It is, so the answer is `True`.

The steps of a chain are written down in `conditions.json` under `verification_chain`. The code never decides at runtime which lookups to make.

A third kind, `ticket_text`, means reading a free-text support ticket. That needs a language model, so it is handled in `agent/ticket_interpreter.py`, not here.

## Inside `condition_evaluator.py`

```
 agent/procedural_executor.py, at each step
   |
   |  get_condition("COND-023")   --> the full condition, from conditions.json (read once)
   |  resolve_condition(crm_context, condition)
   v
 resolve_condition: does the condition have a verification_chain?
   |
   +-- no:  evaluate_condition                 simple check
   |          get_nested_field(record, "roaming_flag")    follows the dots, e.g. "device.imei"
   |          compare with == != > < >= <=
   |
   +-- yes: evaluate_chain_condition           chain check, lookups in order:
              source "crm"   --> get_nested_field(record, reads)
              other source   --> CHAIN_SOURCES[source](a value from an earlier lookup)
                                   network_status   --> get_network_status(zip_code)
                                   device_financing --> get_device_financing(imei)
                                   fraud_gateway    --> get_fraud_status(customer_id)
              compare the last value with == != > < >= <=
```

Comparisons convert types first: a boolean on either side compares as booleans (so the text `"true"` matches `true`), a number compares as numbers, and anything else as text. A list field compares by its length. It never uses `eval()`. Errors: `FieldNotFoundError` (a missing field path), `UnsupportedOperatorError` (an unknown operator), `TypeCoercionError` (a value that can't be turned into a number).

## Who uses this folder

- `agent/supervisor.py` calls `get_customer()` once at the start of a request and keeps the CRM record.
- `agent/procedural_executor.py` calls `get_condition()` and `resolve_condition()` at each procedure step.
- `etl/validate.py` uses `CHAIN_SOURCES` to check `conditions.json` before the data is loaded.



## Adding another mock lookup

1. Add `data/<name>.json` and `mock_crm/<name>_store.py`. Copy an existing store.
2. Add one line for it in `CHAIN_SOURCES` in `condition_evaluator.py`.
3. Use it as a `source` in a condition's `verification_chain` in `conditions.json`.



## What is checked, and what isn't

Before loading, `etl/validate.py` checks each chain:

- it has at least 2 steps;
- every `source` is `crm` or a name in `CHAIN_SOURCES`;
- every `param_from` (the value a step looks up) comes from an earlier step;
- every `crm` step reads a field that exists in the CRM record.

It does not check that the values being looked up exist in the store's data. If a customer's ZIP code is missing from `network_status.json`, the error only appears when that condition is evaluated.

Tests are in `tests/test_condition_evaluator.py`. They cover simple checks (including type conversion, list fields and the error cases) and yes/no results for all three chain conditions. They do not yet cover chain error cases, such as an unknown `source` or a missing key.