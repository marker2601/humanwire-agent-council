import json
import sys

from caspian_sdk import CommClient

from secondsignal.config import Settings

PRIVATE_WARNING = (
    "PRIVATE CONFIGURATION: sender and conversation identifiers must not be committed. "
    "Stop with Ctrl+C after sending one message to the bot."
)


def main() -> None:
    settings = Settings()
    api_key, telegram_bot_token = settings.require_listener_credentials()
    client = CommClient(api_key=api_key, base_url=settings.caspian_base_url)
    client.connect_telegram(bot_token=telegram_bot_token)
    print(PRIVATE_WARNING, file=sys.stderr)

    @client.on_message
    def capture(message) -> None:
        sender = message.sender or {}
        print(
            json.dumps(
                {
                    "sender_address": sender.get("address", ""),
                    "conversation_id": message.conversation_id,
                }
            ),
            flush=True,
        )

    try:
        client.listen(concurrency="queue")
    except KeyboardInterrupt:
        print("Telegram route capture stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
