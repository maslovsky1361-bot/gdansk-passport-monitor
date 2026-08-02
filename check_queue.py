import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


QUEUE_URL = "https://gdansk.pasport.org.ua/solutions/e-queue"

BOT_TOKEN = "8783949502:AAFfhkXCmxKL_8AzGt4sERxBEKyeY7uwFxA"

STATE_FILE = Path("state.json")

NO_SLOTS_TEXTS = [
    "наразі всі місця зайняті",
    "будь ласка, спробуйте в інший час або день",
]

AVAILABLE_TEXTS = [
    "номер телефону",
    "продовжити",
]

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 Version/18.0 "
    "Mobile/15E148 Safari/604.1"
)


def telegram_api(method: str, data: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    encoded_data = None

    if data:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=encoded_data,
        headers={"User-Agent": USER_AGENT},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Ошибка Telegram: {result}")

    return result


def find_chat_id() -> str:
    result = telegram_api("getUpdates")
    updates = result.get("result", [])

    for update in reversed(updates):
        message = (
            update.get("message")
            or update.get("edited_message")
        )

        if not message:
            continue

        chat = message.get("chat", {})

        if chat.get("type") == "private":
            return str(chat["id"])

    raise RuntimeError(
        "Чат с ботом не найден. Открой бота, нажми Start "
        "и отправь ему любое сообщение."
    )


def send_message(text: str) -> None:
    telegram_api(
        "sendMessage",
        {
            "chat_id": find_chat_id(),
            "text": text,
            "disable_web_page_preview": "false",
        },
    )


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "last_status": "unknown",
            "notification_sent": False,
        }

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {
            "last_status": "unknown",
            "notification_sent": False,
        }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def download_page() -> str:
    request = urllib.request.Request(
        QUEUE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8",
            "Cache-Control": "no-cache",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:
        html = response.read().decode(
            "utf-8",
            errors="ignore",
        )

    return html.lower()


def determine_status(html: str) -> str:
    no_slots = any(
        text in html
        for text in NO_SLOTS_TEXTS
    )

    available = all(
        text in html
        for text in AVAILABLE_TEXTS
    )

    if available:
        return "available"

    if no_slots:
        return "no_slots"

    return "changed"


def main() -> int:
    state = load_state()
    html = download_page()
    status = determine_status(html)

    print(f"Статус страницы: {status}")

    if status == "available":
        if not state.get("notification_sent", False):
            send_message(
                "🚨 В ГДАНЬСКЕ ПОЯВИЛИСЬ СВОБОДНЫЕ МЕСТА!\n\n"
                "На странице появилась форма записи с номером "
                "телефона и кнопкой «Продовжити».\n\n"
                "Открывай и бронируй:\n"
                f"{QUEUE_URL}"
            )

        state = {
            "last_status": "available",
            "notification_sent": True,
        }

    elif status == "no_slots":
        state = {
            "last_status": "no_slots",
            "notification_sent": False,
        }

    else:
        if state.get("last_status") != "changed":
            send_message(
                "⚠️ Страница электронной очереди изменилась.\n\n"
                "Сообщение «Наразі всі місця зайняті» "
                "не найдено. Проверь страницу вручную:\n"
                f"{QUEUE_URL}"
            )

        state = {
            "last_status": "changed",
            "notification_sent": state.get(
                "notification_sent",
                False,
            ),
        }

    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            f"Ошибка: {type(error).__name__}: {error}"
        )
        sys.exit(1)
