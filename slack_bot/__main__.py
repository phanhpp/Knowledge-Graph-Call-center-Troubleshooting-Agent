"""`uv run python -m slack_bot` starts the Slack bot."""

from slack_bot.app import serve

if __name__ == "__main__":
    serve()
