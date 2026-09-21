"""Slack front end for the Super Agent. See slack_bot/slack_setup.md for setup.

A consultant types the /agent command with a customer ID and a question:

    /agent CUST-1005 customer cannot activate roaming

    parsing.py     splits the command text into (customer_id, question)
    formatting.py  turns the agent's answer and citations into a Slack message, plus fixed messages
    app.py         connects to Slack, runs the agent, and replies to the command
"""
