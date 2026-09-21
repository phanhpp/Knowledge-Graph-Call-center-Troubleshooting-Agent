"""End-to-end tests: a query and a customer_id go into the full supervisor graph, and an answer
comes out. They use the real Neo4j graph, and real Claude and OpenAI calls where the path needs
them. Grouped by what is being tested:

    troubleshooting/   the procedure walk, by kind of condition
    open_ended/        the knowledge-base search
    errors/            bad input, which must give a clear message and never crash

Run only these with `pytest -m e2e`, or leave them out with `pytest -m "not e2e"`.
"""
