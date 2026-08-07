"""Тексты XENO.net — спокойный премиум, HTML, полностью на русском."""
from __future__ import annotations

import html
import time
from datetime import datetime, timezone

from db import IssuedLink, User

BRAND = "XENO.net"
RULE = "──────────────"


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def plan_label(plan: str) -> str:
    return {
        "demo": "Демо",
        "month": "Месяц",
        "year": "Год",
        "issued": "Выданная",
        "issued-30": "Выданная · 30 дн.",
        "issued-90": "Выданная · 90 дн.",
        "issued-365": "Выданная · 365 дн.",
    }.get(plan, plan or "—")


def days_left(expires_at: int) -> int:
    remaining = expires_at - int(time.time())
    if remaining <= 0:
        return 0
    return max(1, (remaining + 86399) // 86400)


def format_expiry(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y · %H:%M UTC")


def _days_word(n: int) -> str:
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 14:
        return "дней"
    if n1 == 1:
        return "день"
    if 2 <= n1 <= 4:
        return "дня"
    return "дней"


def bar(pct: float, *, width: int = 12) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _header(title: str | None = None) -> str:
    if title:
        return f"<b>{BRAND}</b>\n{RULE}\n\n<b>{esc(title)}</b>\n\n"
    return f"<b>{BRAND}</b>\n{RULE}\n\n"


def _profile_hint() -> str:
    return (
        "<b>В Happ</b>\n"
        "Вручную выберите <code>🇷🇺XENO RU</code> — основной.\n"
        "<code>🇳🇱XENO NL Direct</code> · только запасной, если RU не встаёт.\n"
        "<i>Автовыбор часто цепляет Direct — тогда кажется, что RU «мёртв».</i>"
    )


def _copy_hint() -> str:
    return (
        "\n\n<i>Нажмите на ссылку, чтобы скопировать.\n"
        "После смены параметров — удалите старую подписку в Happ и добавьте заново.</i>"
    )


def home(*, demo_days: int = 30, has_access: bool = False) -> str:
    tagline = "«Приватность — не привилегия.»"
    if has_access:
        body = (
            f"{tagline}\n\n"
            "Доступ активен.\n"
            "Откройте <b>Мой доступ</b> — ссылка и срок там.\n\n"
            "<i>До двух устройств · без лишнего шума.</i>"
        )
    else:
        body = (
            f"{tagline}\n\n"
            "Личный доступ в сеть.\n"
            "Свой ключ · без общего пула.\n\n"
            "<b>Подключиться за 1 минуту</b> — выберите устройство,\n"
            "установите Happ, откройте демо.\n\n"
            f"<i>Демо · {demo_days} {_days_word(demo_days)}, один раз на Telegram ID · "
            "до двух ссылок.</i>"
        )
    return _header() + body


def connecting() -> str:
    return _header() + "Собираем доступ…\n\n<i>Обычно несколько секунд.</i>"


def connecting_second() -> str:
    return _header() + "Готовим второе устройство…\n\n<i>Первая ссылка не меняется.</i>"


def onboard_pick_platform() -> str:
    return (
        _header("Подключение")
        + "На каком устройстве включите доступ?\n\n"
        + "Подскажем короткий путь под вашу платформу.\n"
        + "Клиент · <b>Happ</b>."
    )


def demo_ok(*, expires_at: int, sub: str, vless: str, demo_days: int) -> str:
    left = days_left(expires_at)
    pct = 100.0 * left / max(demo_days, 1)
    return (
        _header("Доступ открыт")
        + f"План · <b>Демо</b>\n"
        + f"Срок · <b>{left} {_days_word(left)}</b>\n"
        + f"<code>{bar(pct)}</code>\n"
        + f"До · <b>{esc(format_expiry(expires_at))}</b>\n\n"
        + "<b>Ссылка подписки</b>\n"
        + f"<code>{esc(sub)}</code>\n\n"
        + f"{_profile_hint()}\n\n"
        + "1. Скачайте Happ\n"
        + "2. Нажмите «Открыть подписку» или вставьте ссылку\n"
        + "3. Обновите → включите VPN\n\n"
        + "<i>Второе устройство — кнопка в Мой доступ.\n"
        "Запасной ключ там же, если понадобится.</i>"
    )


def demo_reuse_active(*, expires_at: int, sub: str) -> str:
    left = days_left(expires_at)
    return (
        _header("Демо уже активно")
        + f"Осталось · <b>{left} {_days_word(left)}</b>\n"
        + f"До · <b>{esc(format_expiry(expires_at))}</b>\n\n"
        + "<b>Ссылка подписки</b>\n"
        + f"<code>{esc(sub)}</code>\n\n"
        + "<i>В Happ обновите подписку.\n"
        "Если не коннектится — удалите старый профиль и добавьте ссылку заново.</i>"
    )


def demo_reuse_spent() -> str:
    return (
        _header("Демо уже использовано")
        + "На этот Telegram ID повторно не выдаётся.\n\n"
        + "Когда откроем оплату — продление будет здесь же."
    )


def demo_fail() -> str:
    return (
        _header("Не удалось выдать")
        + "Попробуйте через минуту.\n\n"
        + "Если снова ошибка — напишите в поддержку."
    )


def access_none() -> str:
    return (
        _header("Мой доступ")
        + "Активного доступа пока нет.\n\n"
        + "Нажмите <b>Подключиться за 1 минуту</b> —\n"
        + "устройство, Happ, демо."
    )


def access_active(
    *,
    user: User,
    sub: str,
    vless: str,
    plan_days_hint: int = 30,
    sub_2: str | None = None,
    vless_2: str | None = None,
    can_add_second: bool = False,
    notice: str | None = None,
) -> str:
    left = days_left(user.expires_at)
    total = max(plan_days_hint, left)
    pct = 100.0 * left / total
    if sub_2:
        links_block = (
            "<b>Устройство 1</b>\n"
            f"<code>{esc(sub)}</code>\n\n"
            "<b>Устройство 2</b>\n"
            f"<code>{esc(sub_2)}</code>\n\n"
        )
        second_hint = ""
    else:
        links_block = (
            "<b>Ссылка подписки</b>\n"
            f"<code>{esc(sub)}</code>\n\n"
        )
        second_hint = (
            "\n\n<i>Нужен второй телефон или ПК —\n"
            "кнопка «Второе устройство» ниже.\n"
            "Эта ссылка останется как есть.</i>"
            if can_add_second
            else ""
        )
    notice_block = f"{notice}\n\n" if notice else ""
    return (
        _header("Мой доступ")
        + notice_block
        + "Статус · <b>активен</b>\n"
        + f"План · <b>{esc(plan_label(user.plan))}</b>\n"
        + f"Осталось · <b>{left} {_days_word(left)}</b>\n"
        + f"<code>{bar(pct)}</code>\n"
        + f"До · <b>{esc(format_expiry(user.expires_at))}</b>\n\n"
        + links_block
        + f"{_profile_hint()}"
        + second_hint
        + "\n\n<i>Нажмите на ссылку, чтобы скопировать.</i>"
    )


def second_ok(*, expires_at: int, sub: str, vless: str, sub_1: str) -> str:
    left = days_left(expires_at)
    return (
        _header("Второе устройство")
        + "Новая ссылка готова.\n"
        + "Первая не затронута.\n\n"
        + f"Общий срок · <b>{left} {_days_word(left)}</b>\n"
        + f"До · <b>{esc(format_expiry(expires_at))}</b>\n\n"
        + "<b>Ссылка · устройство 2</b>\n"
        + f"<code>{esc(sub)}</code>\n\n"
        + "<i>Устройство 1 — по-прежнему в Мой доступ.</i>\n"
        + "<i>Нажмите на ссылку, чтобы скопировать.</i>\n\n"
        + "<i>Windows: Happ от администратора; если нет сети — режим Proxy.</i>"
    )


def second_already() -> str:
    return (
        _header("Лимит ссылок")
        + "Уже выданы обе ссылки на этот доступ.\n\n"
        + "Откройте <b>Мой доступ</b> — там обе подписки."
    )


def second_need_first() -> str:
    return (
        _header("Сначала первая ссылка")
        + "Откройте демо — первая подписка,\n"
        + "затем можно добавить второе устройство."
    )


def second_delete_confirm() -> str:
    return (
        _header("Убрать устройство 2")
        + "Вторая ссылка будет отключена.\n"
        + "Первая подписка и срок доступа — без изменений.\n\n"
        + "<i>В Happ на втором устройстве профиль можно удалить вручную.</i>"
    )


def second_deleting() -> str:
    return _header() + "Отключаем устройство 2…\n\n<i>Первая ссылка не меняется.</i>"


def second_deleted() -> str:
    return (
        "Устройство 2 отключено.\n"
        "Основной доступ — без изменений."
    )


def second_delete_none() -> str:
    return (
        _header("Мой доступ")
        + "Второго устройства сейчас нет.\n"
        + "Первая ссылка на месте."
    )


def access_expired(*, user: User) -> str:
    return (
        _header("Мой доступ")
        + "Статус · <b>истёк</b>\n"
        + f"План · <b>{esc(plan_label(user.plan))}</b>\n"
        + f"Был активен до · <b>{esc(format_expiry(user.expires_at))}</b>\n\n"
        + "Оплата скоро — продление будет в этом боте."
    )


def connect_guide(*, has_access: bool = False) -> str:
    """Generic connect — prefer connect_guide_platform after device pick."""
    return onboard_pick_platform() if not has_access else connect_guide_platform(
        "ios", has_access=True
    )


def connect_guide_platform(platform: str, *, has_access: bool = False) -> str:
    labels = {
        "ios": "iOS",
        "android": "Android",
        "windows": "Windows",
        "mac": "Mac",
    }
    label = labels.get(platform, "устройство")
    store_hint = {
        "ios": "кнопка App Store ниже",
        "android": "кнопка Google Play ниже",
        "windows": "кнопка скачивания Windows ниже",
        "mac": "кнопка App Store ниже",
    }.get(platform, "кнопка Happ ниже")

    if has_access:
        steps = (
            f"1. Установите <b>Happ</b> ({esc(store_hint)})\n"
            "2. <b>Мой доступ</b> → скопируйте нужную ссылку из текста\n"
            "3. В Happ вставьте → обновите → включите VPN\n\n"
            "Вручную выберите <code>🇷🇺XENO RU</code> — основной профиль."
        )
    else:
        steps = (
            f"1. Установите <b>Happ</b> ({esc(store_hint)})\n"
            "2. Нажмите <b>«Открыть демо»</b> — ссылка сразу\n"
            "3. «Открыть подписку» или вставьте URL в Happ\n"
            "4. Обновите → включите VPN"
        )
    win_note = ""
    if platform == "windows":
        win_note = (
            "\n\n<b>Windows</b>\n"
            "· Запуск Happ <b>от имени администратора</b> (нужно для TUN)\n"
            "· Нет сети при «подключено» → режим <b>Proxy</b> вместо TUN\n"
            "· Обновлять подписку только при выключенном VPN\n"
            "· Второе устройство: вторая ссылка в Мой доступ, не первая"
        )
    return (
        _header(f"Подключение · {label}")
        + steps
        + win_note
        + "\n\nВнешний IP · NL · сайты РФ · напрямую\n\n"
        + "<i>Не коннектится — удалите старый профиль XENO и добавьте свежую ссылку.</i>"
    )


def pricing(*, on_waitlist: bool = False) -> str:
    wait = (
        "Вы уже в листе ожидания — напишем в этот чат,\n"
        "когда откроем оплату.\n\n"
        if on_waitlist
        else "Можно записаться · узнаете первыми, без сторонних рассылок.\n\n"
    )
    return (
        _header("Тарифы")
        + "Оплата ещё закрыта.\n"
        + wait
        + "Сейчас · <b>демо 30 дней</b>\n"
        + "один раз на Telegram ID · до двух устройств\n\n"
        + "<i>Без общих аккаунтов · без перепродажи ключей.</i>"
    )


def waitlist_joined() -> str:
    return (
        _header("Лист ожидания")
        + "Готово — вы в списке.\n\n"
        + "Когда откроем оплату, напишем <b>сюда</b>.\n"
        + "Без сторонних каналов и спама.\n\n"
        + "<i>Демо и доступ работают как раньше.</i>"
    )


def waitlist_already() -> str:
    return (
        _header("Лист ожидания")
        + "Вы уже в списке.\n\n"
        + "Как только оплата откроется — сообщение в этот чат."
    )


def expiry_nudge(*, days: int, expires_at: int, plan: str) -> str:
    left = max(1, days)
    title = "Срок доступа скоро закончится" if left > 1 else "Срок доступа заканчивается завтра"
    return (
        _header(title)
        + f"План · <b>{esc(plan_label(plan))}</b>\n"
        + f"Осталось · <b>{left} {_days_word(left)}</b>\n"
        + f"До · <b>{esc(format_expiry(expires_at))}</b>\n\n"
        + "Продление откроем в этом боте.\n"
        + "Пока доступ активен — можно пользоваться как обычно.\n\n"
        + "<i>Это одно короткое напоминание · без рассылок.</i>"
    )


def status_ok(
    *,
    xray_state: str = "running",
    public_ip: str = "",
    entry_host: str = "",
) -> str:
    online = xray_state == "running"
    if online:
        state_line = "● Сеть · <b>в порядке</b>"
        vibe = "Сервис работает штатно."
    else:
        state_line = f"○ Сервис · <b>сбой</b> · {esc(xray_state)}"
        vibe = "Идёт восстановление — попробуйте позже."
    entry = f"\nВход · <code>{esc(entry_host)}</code>" if entry_host else ""
    ip_line = f"\nВыход · <code>{esc(public_ip)}</code>" if public_ip else ""
    return (
        _header("Статус")
        + f"{state_line}\n"
        + f"{vibe}\n\n"
        + "Схема · РФ мост → NL exit"
        + entry
        + ip_line
        + "\n\n"
        + "<i>Если сеть в порядке, а у вас нет доступа —\n"
        "обновите подписку в Happ.</i>"
    )


def status_fail(*, detail: str | None = None) -> str:
    extra = f"\n\n<code>{esc(detail[:200])}</code>" if detail else ""
    return (
        _header("Статус")
        + "Не удалось проверить состояние сети.\n"
        + "Попробуйте через минуту."
        + extra
    )


def _fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(x)} {unit}"
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{n} B"


def _fmt_uptime(sec: int) -> str:
    d, rem = divmod(max(0, sec), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}д {h}ч"
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def metrics(
    *,
    cpu: float,
    mem_used: int,
    mem_total: int,
    disk_used: int,
    disk_total: int,
    uptime: int,
    load1: float,
    load5: float,
    load15: float,
    xray_state: str,
    xray_version: str,
    inbound_up: int,
    inbound_down: int,
    inbound_clients: int,
    active_users: int,
    issued_active: int,
    tcp: int,
    udp: int,
    net_up: int,
    net_down: int,
) -> str:
    mem_pct = (100.0 * mem_used / mem_total) if mem_total else 0
    disk_pct = (100.0 * disk_used / disk_total) if disk_total else 0
    xray = "ok" if xray_state == "running" else esc(xray_state)
    return (
        _header("Метрики")
        + f"CPU · <b>{cpu:.0f}%</b>\n<code>{bar(cpu)}</code>\n"
        + f"RAM · <b>{mem_pct:.0f}%</b> · {_fmt_bytes(mem_used)} / {_fmt_bytes(mem_total)}\n"
        + f"<code>{bar(mem_pct)}</code>\n"
        + f"Disk · <b>{disk_pct:.0f}%</b> · {_fmt_bytes(disk_used)} / {_fmt_bytes(disk_total)}\n"
        + f"<code>{bar(disk_pct)}</code>\n\n"
        + f"Load · <b>{load1:.2f}</b> / {load5:.2f} / {load15:.2f}\n"
        + f"Uptime · <b>{_fmt_uptime(uptime)}</b>\n\n"
        + f"Xray · <b>{xray}</b> · {esc(xray_version)}\n"
        + f"Сессии · TCP <b>{tcp}</b> · UDP <b>{udp}</b>\n"
        + f"Сеть · ↑{_fmt_bytes(net_up)}/s · ↓{_fmt_bytes(net_down)}/s\n\n"
        + "<b>Клиенты XENO</b>\n"
        + f"Активных · <b>{active_users}</b>\n"
        + f"Выданных · <b>{issued_active}</b>\n"
        + f"На inbound · <b>{inbound_clients}</b>\n"
        + f"Панель · ↑{_fmt_bytes(inbound_up)} · ↓{_fmt_bytes(inbound_down)}"
    )


def help_text(*, demo_days: int, admin: bool = False, donate: bool = False) -> str:
    metrics_line = "· <b>Метрики</b> — ops-панель NL\n" if admin else ""
    metrics_cmd = " · /metrics" if admin else ""
    donate_line = "· <b>Поддержать</b> — добровольно, крипто\n" if donate else ""
    return (
        _header("Поддержка")
        + "· <b>Подключиться за 1 минуту</b> — устройство → Happ → демо\n"
        + f"· <b>Открыть демо</b> — {demo_days} {_days_word(demo_days)}, один раз\n"
        + "· <b>Написать нам</b> — диалог в этом чате\n"
        + donate_line
        + metrics_line
        + "\n"
        + "Тарифы, статус сети и политика — в главном меню.\n\n"
        + "<b>Если не коннектится</b>\n"
        + "1. Выключите VPN в Happ\n"
        + "2. Удалите старый профиль XENO\n"
        + "3. Ссылка из <b>Мой доступ</b> — только <code>https://…</code>\n"
        + "4. Обновите подписку → вручную <code>🇷🇺XENO RU</code>\n"
        + "5. iOS: «неизвестный тип» / «отказано» — удалить профиль, импорт заново\n"
        + "6. iOS: VPN сам отключается в TG — Happ → Dev Settings → <b>No Limit Mode</b>\n"
        + "7. Windows: Happ <b>от администратора</b>; нет сети — режим <b>Proxy</b>\n"
        + "8. Магазины / доставка (Ozon, WB, Магнит, X5, Самокат…) — только профиль <b>RU</b>, не Direct\n\n"
        + f"Команды · /start · /help · /dialog · /status{metrics_cmd}\n\n"
        + "Исходный код · публичный репозиторий.\n"
        + "Убедитесь сами, как устроена выдача — не «на слово».\n"
        + '<a href="https://github.com/kriogenik/XENO.net">github.com/kriogenik/XENO.net</a>\n\n'
        + "<i>Личное использование.\n"
        "Не передавайте ссылку подписки третьим лицам.</i>"
    )


def policy_text() -> str:
    """Zero-tolerance stance on data trade — calm, serious, Telegram-length."""
    return (
        _header("Политика")
        + "Приватность здесь — не слоган на витрине.\n"
        + "Это линия, за которую мы не заходим.\n\n"
        + "<b>Нулевая терпимость</b>\n"
        + "Мы не собираем, не продаём и не обмениваем ваши данные.\n"
        + "Ни «партнёрам». Ни рекламным сетям. Ни рынку слежки.\n\n"
        + "<b>Внимание — не валюта</b>\n"
        + "Мы не монетизируем браузинг и не рисуем графы «кто куда ходил».\n"
        + "Сервис живёт доступом — не чужим любопытством.\n\n"
        + "<b>Минимум для работы</b>\n"
        + "Чтобы выдать ключ, нужны Telegram ID, ссылка и срок.\n"
        + "Не больше. Не для досье.\n\n"
        + "<b>Честные границы</b>\n"
        + "Оператор видит то, что вы сами присылаете в диалог.\n"
        + "Мы не обещаем магическую невидимость —\n"
        + "обещаем не превращать ваш трафик в товар.\n\n"
        + "<b>Прозрачность</b>\n"
        + "Код открыт — можно убедиться, чего бот не делает.\n"
        + '<a href="https://github.com/kriogenik/XENO.net">github.com/kriogenik/XENO.net</a>\n\n'
        + "<i>Доступ — для личного использования.\n"
        + "Ссылку подписки не пересылают.</i>"
    )


def donate_text(wallets: list | tuple) -> str:
    """Calm donation screen. wallets: sequence of objects with .coin / .address."""
    body = (
        _header("Поддержать")
        + "Если сервис полезен — можно поддержать.\n"
        + "Без обязательств.\n\n"
    )
    if not wallets:
        return (
            body
            + "<i>Адреса пока не указаны.\n"
            + "Когда появятся — будут здесь.</i>"
        )
    lines: list[str] = []
    for w in wallets:
        coin = esc(getattr(w, "coin", "") or "")
        addr = esc(getattr(w, "address", "") or "")
        if not addr:
            continue
        lines.append(f"<b>{coin}</b>\n<code>{addr}</code>")
    if not lines:
        return (
            body
            + "<i>Адреса пока не указаны.\n"
            + "Когда появятся — будут здесь.</i>"
        )
    return (
        body
        + "\n\n".join(lines)
        + "\n\n"
        + "<i>Нажмите на адрес, чтобы скопировать.</i>"
    )


def support_intro() -> str:
    return (
        _header("Диалог")
        + "Сначала три шага из поддержки — часто этого достаточно.\n\n"
        + "1. Выключите VPN в Happ\n"
        + "2. Удалите старый профиль XENO\n"
        + "3. Добавьте свежую ссылку из <b>Мой доступ</b> → обновите\n\n"
        + "Если уже пробовали — напишите, что видите в Happ.\n"
        + "Скрин ошибки можно. Ответим в этот чат.\n\n"
        + "<i>Не присылайте ссылку подписки целиком — нам она не нужна.</i>"
    )


def support_compose_prompt() -> str:
    return (
        _header("Диалог")
        + "Следующее сообщение уйдёт нам.\n"
        + "Текст или скрин — одним сообщением.\n\n"
        + "<i>Без ссылок подписки и чужих токенов.</i>"
    )


def support_accepted(*, public_id: str) -> str:
    return (
        _header("Диалог")
        + "Сообщение ушло. Держим диалог здесь —\n"
        + "без сторонних ботов и форм.\n\n"
        + f"<i>{esc(public_id)}</i>"
    )


def support_closed_user() -> str:
    return (
        _header("Диалог")
        + "Диалог закрыт.\n"
        + "Снова — через <b>Поддержка → Написать нам</b>\n"
        + "или команду /dialog."
    )


def support_closed_idle() -> str:
    return (
        _header("Диалог")
        + "Диалог закрыт по тишине.\n"
        + "Нужно ещё — <b>Поддержка → Написать нам</b>."
    )


def support_rate_limited() -> str:
    return (
        _header("Диалог")
        + "Пауза: слишком много сообщений подряд.\n"
        + "Напишите чуть позже или закройте диалог."
    )


def support_reopen_limited() -> str:
    return (
        _header("Диалог")
        + "Недавно закрывали диалог.\n"
        + "Подождите немного — или загляните в поддержку."
    )


def support_media_rejected() -> str:
    return (
        _header("Диалог")
        + "Стикеры, голос и видео сюда не принимаем.\n"
        + "Напишите текстом или пришлите скрин / файл."
    )


def support_deliver_prefix() -> str:
    return _header("Ответ") + ""


def support_admin_card(
    *,
    public_id: str,
    username: str | None,
    tg_id: int,
    plan: str,
    access_label: str,
    expires_label: str,
    waitlist: bool,
    devices: int,
) -> str:
    handle = f"@{esc(username)}" if username else "—"
    wl = "да" if waitlist else "нет"
    return (
        _header(f"Диалог · {public_id}")
        + f"{handle} · <code>{tg_id}</code>\n"
        + f"{esc(plan)} · <b>{esc(access_label)}</b>\n"
        + f"До · <b>{esc(expires_label)}</b>\n"
        + f"Устройств · <b>{devices}</b> · waitlist · {wl}\n\n"
        + "<i>Reply на это сообщение или «Ответить».</i>"
    )


def support_admin_reply_prompt(*, public_id: str) -> str:
    return (
        _header(f"Ответ · {public_id}")
        + "Следующее сообщение уйдёт клиенту.\n"
        + "Текст или скрин."
    )


def support_admin_closed(*, public_id: str) -> str:
    return _header(f"Диалог · {public_id}") + "Закрыт."


def support_admin_deliver_fail(*, public_id: str) -> str:
    return (
        _header(f"Диалог · {public_id}")
        + "Не удалось доставить — клиент, возможно, остановил бота."
    )


def god_home(*, active_users: int, issued_active: int, waitlist: int = 0) -> str:
    return (
        _header("Godmode")
        + "Выдача доступов и диагностика.\n"
        + "Не списывает демо с Telegram ID.\n\n"
        + f"Пользователей · <b>{active_users}</b>\n"
        + f"Выданных активных · <b>{issued_active}</b>\n"
        + f"Лист ожидания · <b>{waitlist}</b>"
    )


def diag_ask() -> str:
    return (
        _header("Диагностика")
        + "Кому посмотреть срез подключений?\n\n"
        + "Одним сообщением: <code>@username</code> или numeric id\n\n"
        + "<i>Без destination URL · только accepts / errors / last_seen.</i>"
    )


def diag_report(
    *,
    label: str,
    email: str,
    day: str,
    accepts_ru: int,
    accepts_nl: int,
    rejects: int,
    error_classes: str,
    last_seen_ru: str,
    last_seen_nl: str,
    bytes_line: str,
    smoke_line: str,
) -> str:
    return (
        _header(f"Диагностика · {label}")
        + f"Email · <code>{esc(email)}</code>\n"
        + f"День · <b>{esc(day)}</b> UTC\n\n"
        + f"RU accepts · <b>{accepts_ru}</b>\n"
        + f"NL Direct · <b>{accepts_nl}</b>\n"
        + f"Rejects · <b>{rejects}</b>\n"
        + f"Errors · {esc(error_classes)}\n"
        + f"Last RU · <b>{esc(last_seen_ru)}</b>\n"
        + f"Last Direct · <b>{esc(last_seen_nl)}</b>\n"
        + f"Traffic · {esc(bytes_line)}\n\n"
        + f"Smoke · {smoke_line}\n\n"
        + "<i>Правки — по runbook / digests, не автофикс.</i>"
    )


def diag_none(*, label: str, email: str) -> str:
    return (
        _header(f"Диагностика · {label}")
        + f"Email · <code>{esc(email)}</code>\n\n"
        + "За сегодня в rollup пусто — ещё не коннектился\n"
        + "или collect ещё не подтянул логи."
    )


def god_ask_target(*, days: int) -> str:
    return (
        _header(f"Кому выдать · {days} дн.")
        + "Одним сообщением:\n"
        + "· <code>@username</code>\n"
        + "· или числовой <code>user id</code>\n"
        + "· или <code>-</code> — ссылка без привязки\n\n"
        + "<i>Чтобы @ник резолвился, человек должен хотя бы раз\n"
        "написать боту /start.</i>"
    )


def god_created(
    *,
    link: IssuedLink,
    sub: str,
    vless: str,
    assigned_tg_id: int | None = None,
    assigned_username: str | None = None,
) -> str:
    who = "без привязки"
    if assigned_tg_id:
        handle = f"@{assigned_username}" if assigned_username else "—"
        who = f"{handle} · <code>{assigned_tg_id}</code>"
    return (
        _header("Ссылка создана")
        + f"ID · <code>{link.id}</code>\n"
        + f"План · <b>{esc(plan_label(link.plan))}</b>\n"
        + f"Кому · {who}\n"
        + f"До · <b>{esc(format_expiry(link.expires_at))}</b>\n\n"
        + "<b>Подписка</b>\n"
        + f"<code>{esc(sub)}</code>\n\n"
        + "<b>Запасной ключ</b>\n"
        + f"<code>{esc(vless)}</code>\n\n"
        + "<i>Получателю: Happ → добавить подписку → обновить.</i>"
    )


def god_resolve_fail(*, username: str) -> str:
    return (
        _header("Не нашли пользователя")
        + f"<code>@{esc(username)}</code>\n\n"
        + "Пусть откроет бота и нажмёт /start,\n"
        + "затем повторите выдачу — или пришлите numeric id."
    )


def god_fail(*, detail: str | None = None) -> str:
    extra = f"\n\n<code>{esc(detail[:300])}</code>" if detail else ""
    return _header("Не удалось выдать") + "Попробуйте ещё раз." + extra


def god_list(links: list[IssuedLink]) -> str:
    if not links:
        return _header("Выданные") + "Пока пусто. Создайте ссылку выше."
    lines = [_header("Выданные").rstrip(), ""]
    for link in links[:20]:
        st = "активна" if link.is_active else "выкл."
        who = ""
        if link.assigned_tg_id:
            handle = f"@{link.assigned_username}" if link.assigned_username else ""
            who = f" · {handle} <code>{link.assigned_tg_id}</code>".rstrip()
        lines.append(
            f"#{link.id} · {esc(plan_label(link.plan))} · {st}{who}\n"
            f"до {esc(format_expiry(link.expires_at))}"
        )
        lines.append("")
    if len(links) > 20:
        lines.append(f"…и ещё {len(links) - 20}")
    return "\n".join(lines).rstrip()


def denied() -> str:
    return _header() + "Недостаточно прав."
