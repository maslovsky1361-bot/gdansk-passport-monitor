import asyncio
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright


QUEUE_URL = "https://gdansk.pasport.org.ua/solutions/e-queue"

# Сейчас здесь тестовый токен.
# Потом замени только значение между кавычками.
BOT_TOKEN = "8783949502:AAFfhkXCmxKL_8AzGt4sERxBEKyeY7uwFxA"

STATE_FILE = Path("state.json")


def telegram_api(method: str, data: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    encoded_data = None

    if data:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=encoded_data,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Ошибка Telegram API: {result}")

    return result


def find_chat_id() -> str:
    """
    Автоматически находит последний личный чат,
    который написал этому Telegram-боту.
    """

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

        if (
            chat.get("type") == "private"
            and chat.get("id") is not None
        ):
            return str(chat["id"])

    raise RuntimeError(
        "Личный чат с ботом не найден. "
        "Открой бота в Telegram, нажми Start "
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
            "last_check": None,
        }

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {
            "last_status": "unknown",
            "last_check": None,
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


async def check_page() -> str:
    """
    Возможные результаты:

    available — настоящая форма записи появилась;
    no_slots — сайт прямо сообщает, что мест нет;
    unknown — страница загрузилась нестандартно.
    """

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
            viewport={
                "width": 430,
                "height": 932,
            },
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 "
                "like Mac OS X) AppleWebKit/605.1.15 "
                "Version/18.0 Mobile/15E148 Safari/604.1"
            ),
        )

        page = await context.new_page()

        await page.goto(
            QUEUE_URL,
            wait_until="domcontentloaded",
            timeout=90_000,
        )

        # Даём динамическим элементам страницы загрузиться.
        await page.wait_for_timeout(10_000)

        body_text = (
            await page.locator("body").inner_text()
        ).lower()

        # 1. Проверяем наличие настоящего поля телефона.
        phone_input_count = await page.locator(
            'input[type="tel"], '
            'input[placeholder*="+380"], '
            'input[placeholder*="50 123"], '
            'input[name*="phone" i]'
        ).count()

        phone_input_exists = phone_input_count > 0

        # 2. Проверяем наличие настоящего select для услуги.
        select_count = await page.locator("select").count()
        select_exists = select_count > 0

        # Дополнительная проверка подписи «Послуга».
        service_label_count = await page.get_by_text(
            re.compile(
                r"^\s*послуга\s*\*?\s*$",
                re.IGNORECASE,
            )
        ).count()

        service_label_exists = service_label_count > 0

        # 3. Проверяем настоящую кнопку «Продовжити».
        continue_button_count = await page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*продовжити\s*$",
                re.IGNORECASE,
            ),
        ).count()

        if continue_button_count == 0:
            continue_button_count = await page.locator(
                'input[type="submit"][value*="Продовжити" i]'
            ).count()

        continue_button_exists = continue_button_count > 0

        # Красное сообщение о занятых местах.
        no_slots_exists = (
            "наразі всі місця зайняті" in body_text
        )

        # Сохраняем скриншот внутри запуска GitHub.
        await page.screenshot(
            path="last_check.png",
            full_page=True,
        )

        await browser.close()

        # Считаем, что запись появилась, только если
        # одновременно найдены реальные элементы формы.
        if (
            phone_input_exists
            and select_exists
            and service_label_exists
            and continue_button_exists
        ):
            return "available"

        if no_slots_exists:
            return "no_slots"

        return "unknown"


def status_name(status: str) -> str:
    names = {
        "available": "форма записи доступна",
        "no_slots": "все места заняты",
        "unknown": "не удалось точно определить состояние страницы",
    }

    return names.get(status, status)


async def main() -> None:
    state = load_state()

    previous_status = state.get(
        "last_status",
        "unknown",
    )

    current_status = await check_page()

    previous_name = status_name(previous_status)
    current_name = status_name(current_status)

    checked_at = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    print(f"Предыдущий статус: {previous_status}")
    print(f"Текущий статус: {current_status}")
    print(f"Время проверки: {checked_at}")

    status_changed = current_status != previous_status

    if status_changed:
        if current_status == "available":
            send_message(
                "🚨 В ГДАНЬСКЕ ПОЯВИЛАСЬ ФОРМА ЗАПИСИ!\n\n"
                f"Предыдущий статус: {previous_name}\n"
                f"Новый статус: {current_name}\n\n"
                "Найдены реальные элементы формы:\n"
                "• выбор услуги;\n"
                "• номер телефона;\n"
                "• кнопка «Продовжити».\n\n"
                "Открывай страницу и бронируй:\n"
                f"{QUEUE_URL}"
            )

        elif current_status == "no_slots":
            send_message(
                "ℹ️ Статус очереди изменился.\n\n"
                f"Было: {previous_name}\n"
                f"Стало: {current_name}\n\n"
                "Сейчас на странице указано:\n"
                "«Наразі всі місця зайняті».\n\n"
                f"{QUEUE_URL}"
            )

        else:
            send_message(
                "⚠️ Статус страницы изменился.\n\n"
                f"Было: {previous_name}\n"
                f"Стало: {current_name}\n\n"
                "Сайт загрузился нестандартно. "
                "Это не означает, что места появились.\n\n"
                f"{QUEUE_URL}"
            )

    else:
        if current_status == "available":
            send_message(
                "✅ Проверка выполнена.\n\n"
                "Статус без изменений: "
                "форма записи по-прежнему доступна.\n\n"
                f"{QUEUE_URL}"
            )

        elif current_status == "no_slots":
            send_message(
                "✅ Проверка выполнена.\n\n"
                "Статус без изменений: "
                "все места по-прежнему заняты.\n\n"
                f"{QUEUE_URL}"
            )

        else:
            send_message(
                "⚠️ Проверка выполнена.\n\n"
                "Статус без изменений: "
                "страница снова загрузилась нестандартно.\n\n"
                f"{QUEUE_URL}"
            )

    save_state(
        {
            "last_status": current_status,
            "last_check": checked_at,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
