"""The graph served by LangGraph Server (`langgraph.json`: "super_agent": "./agent/graph.py:graph").

Compiled without a checkpointer: the server provides its own and saves each conversation as a
server thread, in place of the Slack bot's checkpoints.db. The Slack bot keeps compiling its own
copy with SQLite (config.get_checkpointer).
"""

from agent.supervisor import build_supervisor_graph

graph = build_supervisor_graph()
