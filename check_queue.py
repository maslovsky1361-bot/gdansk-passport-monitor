import asyncio
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright


QUEUE_URL = "https://gdansk.pasport.org.ua/solutions/e-queue"

# Тестовый токен. Потом замени только значение между кавычками.
BOT_TOKEN = "8783949502:AAFfhkXCmxKL_8AzGt4sERxBEKyeY7uwFxA"

STATE_FILE = Path("state.json")


def telegram_api(method: str, data: dict | None = None) -> dict:
    """Выполняет запрос к Telegram Bot API."""

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    encoded_data = None

    if data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=encoded_data,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Ошибка Telegram API: {result}")

    return result


def find_chat_id() -> str:
    """
    Находит последний личный чат, который написал боту.
    Перед первым запуском нужно открыть бота, нажать Start
    и отправить ему любое сообщение.
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
        "Личный чат с ботом не найден. Открой бота в Telegram, "
        "нажми Start и отправь ему любое сообщение."
    )


def send_message(text: str) -> None:
    """Отправляет сообщение в личный чат с ботом."""

    telegram_api(
        "sendMessage",
        {
            "chat_id": find_chat_id(),
            "text": text,
            "disable_web_page_preview": "false",
        },
    )


def load_state() -> dict:
    """Загружает статус предыдущей проверки."""

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


def save_state(status: str, checked_at: str) -> None:
    """Сохраняет результат текущей проверки."""

    STATE_FILE.write_text(
        json.dumps(
            {
                "last_status": status,
                "last_check": checked_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def check_page() -> str:
    """
    Возвращает один из статусов:

    available — появилась настоящая форма записи;
    no_slots — отображается «Наразі всі місця зайняті»;
    unknown — состояние страницы не удалось определить.
    """

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            locale="uk-UA",
            viewport={
                "width": 1280,
                "height": 1600,
            },
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )

        page = await context.new_page()

        try:
            await page.goto(
                QUEUE_URL,
                wait_until="domcontentloaded",
                timeout=90_000,
            )

            # Ждём появления либо красного сообщения,
            # либо настоящей формы записи.
            try:
                await page.wait_for_function(
                    """
                    () => {
                        const bodyText =
                            (document.body?.innerText || '').toLowerCase();

                        const noSlots =
                            bodyText.includes(
                                'наразі всі місця зайняті'
                            );

                        const phoneInput =
                            document.querySelector(
                                'input[type="tel"], ' +
                                'input[name*="phone" i], ' +
                                'input[placeholder*="+380"], ' +
                                'input[placeholder*="50 123"]'
                            );

                        const serviceField =
                            document.querySelector('select') ||
                            bodyText.includes('послуга');

                        const controls = [
                            ...document.querySelectorAll(
                                'button, input[type="submit"]'
                            )
                        ];

                        const continueButton = controls.some(
                            element => {
                                const value = (
                                    element.innerText ||
                                    element.value ||
                                    ''
                                ).toLowerCase();

                                return value.includes('продовжити');
                            }
                        );

                        return (
                            noSlots ||
                            (
                                phoneInput &&
                                serviceField &&
                                continueButton
                            )
                        );
                    }
                    """,
                    timeout=40_000,
                )
            except Exception:
                # Если условие не появилось, всё равно анализируем страницу.
                pass

            await page.wait_for_timeout(3_000)

            body_text = (
                await page.locator("body").inner_text()
            ).lower()

            # Точное сообщение об отсутствии мест.
            no_slots = (
                "наразі всі місця зайняті" in body_text
            )

            # Настоящее поле номера телефона.
            phone_input = (
                await page.locator(
                    'input[type="tel"], '
                    'input[name*="phone" i], '
                    'input[placeholder*="+380"], '
                    'input[placeholder*="50 123"]'
                ).count()
                > 0
            )

            # Настоящее поле выбора услуги.
            select_exists = (
                await page.locator("select").count()
                > 0
            )

            service_label = (
                await page.get_by_text(
                    re.compile(
                        r"^\s*послуга\s*\*?\s*$",
                        re.IGNORECASE,
                    )
                ).count()
                > 0
            )

            # Настоящая кнопка «Продовжити».
            continue_button = (
                await page.locator(
                    'button:has-text("Продовжити"), '
                    'input[type="submit"][value*="Продовжити" i]'
                ).count()
                > 0
            )

            await page.screenshot(
                path="last_check.png",
                full_page=True,
            )

            # Форма считается доступной только при одновременном
            # наличии поля услуги, телефона и кнопки продолжения.
            if (
                phone_input
                and (select_exists or service_label)
                and continue_button
            ):
                return "available"

            if no_slots:
                return "no_slots"

            return "unknown"

        finally:
            await browser.close()


def status_name(status: str) -> str:
    names = {
        "available": "форма записи доступна",
        "no_slots": "все места заняты",
        "unknown": "состояние страницы не удалось определить",
    }

    return names.get(status, status)


async def main() -> None:
    state = load_state()

    previous_status = state.get(
        "last_status",
        "unknown",
    )

    current_status = await check_page()

    checked_at = datetime.now(
        timezone.utc
    ).strftime("%d.%m.%Y %H:%M:%S UTC")

    previous_name = status_name(previous_status)
    current_name = status_name(current_status)

    changed = current_status != previous_status

    print(f"Предыдущий статус: {previous_status}")
    print(f"Текущий статус: {current_status}")
    print(f"Время проверки: {checked_at}")

    if current_status == "available":
        if changed:
            message = (
                "🚨 В ГДАНЬСКЕ ПОЯВИЛАСЬ ФОРМА ЗАПИСИ!\n\n"
                f"Было: {previous_name}\n"
                f"Стало: {current_name}\n\n"
                "Найдены:\n"
                "• выбор услуги;\n"
                "• поле номера телефона;\n"
                "• кнопка «Продовжити».\n\n"
                "Открывай страницу и бронируй:\n"
                f"{QUEUE_URL}"
            )
        else:
            message = (
                "🚨 Проверка выполнена.\n\n"
                "Статус без изменений: форма записи "
                "по-прежнему доступна.\n\n"
                f"{QUEUE_URL}"
            )

    elif current_status == "no_slots":
        if changed:
            message = (
                "ℹ️ Статус очереди изменился.\n\n"
                f"Было: {previous_name}\n"
                f"Стало: {current_name}\n\n"
                "На странице сейчас указано:\n"
                "«Наразі всі місця зайняті».\n\n"
                f"{QUEUE_URL}"
            )
        else:
            message = (
                "✅ Проверка выполнена.\n\n"
                "Статус без изменений: "
                "все места по-прежнему заняты.\n\n"
                f"{QUEUE_URL}"
            )

    else:
        if changed:
            message = (
                "⚠️ Проверка выполнена.\n\n"
                f"Статус изменился: {current_name}.\n\n"
                "Это не означает, что места появились. "
                "Сайт мог загрузиться не полностью.\n\n"
                f"{QUEUE_URL}"
            )
        else:
            message = (
                "⚠️ Проверка выполнена.\n\n"
                "Статус без изменений: состояние страницы "
                "снова не удалось определить.\n\n"
                f"{QUEUE_URL}"
            )

    send_message(message)
    save_state(current_status, checked_at)


if __name__ == "__main__":
    asyncio.run(main())
