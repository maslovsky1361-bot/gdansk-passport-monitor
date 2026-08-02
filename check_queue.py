import asyncio
import json
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright


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


def telegram_api(method: str, data: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    encoded = None

    if data:
        encoded = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(url, data=encoded)

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(result)

    return result


def find_chat_id() -> str:
    result = telegram_api("getUpdates")

    for update in reversed(result.get("result", [])):
        message = update.get("message") or update.get("edited_message")

        if not message:
            continue

        chat = message.get("chat", {})

        if chat.get("type") == "private":
            return str(chat["id"])

    raise RuntimeError(
        "Открой Telegram-бота, нажми Start и отправь ему сообщение."
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
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_status": "unknown",
            "notification_sent": False,
        }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def get_page_text() -> str:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = await browser.new_context(
            locale="uk-UA",
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Version/18.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 430, "height": 932},
        )

        page = await context.new_page()

        await page.goto(
            QUEUE_URL,
            wait_until="domcontentloaded",
            timeout=90000,
        )

        await page.wait_for_timeout(8000)

        text = await page.locator("body").inner_text()

        await page.screenshot(
            path="last_check.png",
            full_page=True,
        )

        await browser.close()

        return text.lower()


def determine_status(text: str) -> str:
    if all(marker in text for marker in AVAILABLE_TEXTS):
        return "available"

    if any(marker in text for marker in NO_SLOTS_TEXTS):
        return "no_slots"

    return "changed"


async def main() -> None:
    state = load_state()

    text = await get_page_text()
    status = determine_status(text)

    print(f"Статус: {status}")

    if status == "available":
        if not state.get("notification_sent", False):
            send_message(
                "🚨 В ГДАНЬСКЕ ПОЯВИЛИСЬ СВОБОДНЫЕ МЕСТА!\n\n"
                "На странице появилась форма записи.\n\n"
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
                "⚠️ Страница очереди изменилась.\n\n"
                "Проверь вручную — возможно, появилась запись:\n"
                f"{QUEUE_URL}"
            )

        state = {
            "last_status": "changed",
            "notification_sent": False,
        }

    save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
