"""Inline-only keyboards. No reply keyboard under the input field."""
from __future__ import annotations

CB_HOME = "x:home"
CB_ONBOARD = "x:go"
CB_DEMO = "x:demo"
CB_ACCESS = "x:access"
CB_SECOND = "x:second"
CB_SECOND_DEL = "x:second:del"
CB_SECOND_DEL_YES = "x:second:del:yes"
CB_SECOND_DEL_NO = "x:second:del:no"
CB_CONNECT = "x:connect"
CB_PLAT_IOS = "x:p:ios"
CB_PLAT_AND = "x:p:and"
CB_PLAT_WIN = "x:p:win"
CB_PLAT_MAC = "x:p:mac"
CB_PRICING = "x:pricing"
CB_WAITLIST = "x:wait"
CB_STATUS = "x:status"
CB_METRICS = "x:metrics"
CB_HELP = "x:help"
CB_DONATE = "x:donate"
CB_POLICY = "x:policy"
CB_SOURCE = "x:src"
CB_GOD = "x:god"
CB_GOD_CREATE = "x:god:new"
CB_GOD_CREATE_30 = "x:god:n30"
CB_GOD_CREATE_90 = "x:god:n90"
CB_GOD_CREATE_365 = "x:god:n365"
CB_GOD_LIST = "x:god:list"
CB_DIAG = "x:diag"
CB_SUPPORT = "x:sup"
CB_SUPPORT_COMPOSE = "x:sup:go"
CB_SUPPORT_HELP = "x:sup:faq"
CB_SUPPORT_MORE = "x:sup:more"
CB_SUPPORT_CLOSE = "x:sup:close"
CB_SUPPORT_REPLY = "x:sup:r:"  # + dialog_id
CB_SUPPORT_ADMIN_CLOSE = "x:sup:c:"  # + dialog_id

HAPP_SITE = "https://www.happ.su/"
REPO_URL = "https://github.com/kriogenik/XENO.net"
# Official Happ downloads (RU-friendly where App Store has a RU listing).
HAPP_IOS = "https://apps.apple.com/ru/app/happ-proxy-utility/id6783623643"
HAPP_ANDROID = "https://play.google.com/store/apps/details?id=com.happproxy"
HAPP_WINDOWS = (
    "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe"
)
HAPP_MAC = "https://apps.apple.com/ru/app/happ-proxy-utility/id6783623643"

HAPP_BY_PLATFORM = {
    "ios": HAPP_IOS,
    "android": HAPP_ANDROID,
    "windows": HAPP_WINDOWS,
    "mac": HAPP_MAC,
}

HAPP_BTN_BY_PLATFORM = {
    "ios": "🍎 App Store · Happ",
    "android": "🤖 Google Play · Happ",
    "windows": "🪟 Скачать Happ · Windows",
    "mac": "💻 App Store · Happ",
}

PLATFORM_CALLBACKS = {
    CB_PLAT_IOS: "ios",
    CB_PLAT_AND: "android",
    CB_PLAT_WIN: "windows",
    CB_PLAT_MAC: "mac",
}


def remove_reply_keyboard() -> dict:
    return {"remove_keyboard": True}


def kb(*rows: list[dict]) -> dict:
    return {"inline_keyboard": list(rows)}


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def link(text: str, url: str) -> dict:
    return {"text": text, "url": url}


def home(*, godmode: bool = False, has_access: bool = False, banned: bool = False) -> dict:
    if banned:
        rows = [
            [btn("❓ Поддержка", CB_HELP), btn("📜 Политика", CB_POLICY)],
            [btn("📡 Статус", CB_STATUS)],
        ]
    elif has_access:
        rows = [
            [btn("🔑 Мой доступ", CB_ACCESS)],
            [btn("📱 Подключить устройство", CB_CONNECT)],
            [btn("💎 Тарифы", CB_PRICING), btn("📡 Статус", CB_STATUS)],
            [btn("❓ Поддержка", CB_HELP), btn("📜 Политика", CB_POLICY)],
        ]
    else:
        rows = [
            [btn("🚀 Подключиться за 1 минуту", CB_ONBOARD)],
            [btn("✨ Открыть демо", CB_DEMO)],
            [btn("💎 Тарифы", CB_PRICING), btn("📡 Статус", CB_STATUS)],
            [btn("❓ Поддержка", CB_HELP), btn("📜 Политика", CB_POLICY)],
        ]
    if godmode and not banned:
        rows.append([btn("⚙️ Godmode", CB_GOD)])
    return kb(*rows)


def platform_pick(*, has_access: bool = False) -> dict:
    rows = [
        [btn("🍎 iOS", CB_PLAT_IOS), btn("🤖 Android", CB_PLAT_AND)],
        [btn("🪟 Windows", CB_PLAT_WIN), btn("💻 Mac", CB_PLAT_MAC)],
    ]
    if has_access:
        rows.append([btn("🔑 Мой доступ", CB_ACCESS)])
    else:
        rows.append([btn("✨ Открыть демо сразу", CB_DEMO)])
    rows.append([btn("🏠 В меню", CB_HOME)])
    return kb(*rows)


def platform_guide_actions(
    *,
    platform: str = "ios",
    has_access: bool = False,
    sub_url: str | None = None,
) -> dict:
    store = HAPP_BY_PLATFORM.get(platform, HAPP_SITE)
    store_label = HAPP_BTN_BY_PLATFORM.get(platform, "📲 Скачать Happ")
    rows: list[list[dict]] = [[link(store_label, store)]]
    if has_access and sub_url:
        rows.append([link("🔗 Открыть подписку", sub_url)])
        rows.append([btn("🔑 Мой доступ", CB_ACCESS)])
    else:
        rows.append([btn("✨ Открыть демо", CB_DEMO)])
    rows.append([btn("📱 Другое устройство", CB_CONNECT), btn("🏠 В меню", CB_HOME)])
    return kb(*rows)


def after_access(
    *,
    sub_url: str | None = None,
    sub_url_2: str | None = None,
    can_add_second: bool = False,
) -> dict:
    """Access screen after first (or second) link.

    - 0 links: not used (no_access / demo flow)
    - 1 link: «Открыть подписку» + optional «Второе устройство»
    - 2 links: no open-URL buttons (links are in the message body);
      «Убрать устройство 2» only; no add button
    """
    rows: list[list[dict]] = []
    if sub_url and sub_url_2:
        rows.append([btn("Убрать устройство 2", CB_SECOND_DEL)])
    elif sub_url:
        rows.append([link("🔗 Открыть подписку", sub_url)])
    if can_add_second and not sub_url_2:
        rows.append([btn("➕ Второе устройство", CB_SECOND)])
    rows.append([btn("📱 Подключить устройство", CB_CONNECT)])
    rows.append([btn("🏠 В меню", CB_HOME)])
    return kb(*rows)


def second_delete_confirm() -> dict:
    """Inline Yes/Cancel — never delete on the first tap."""
    return kb(
        [btn("Да, убрать", CB_SECOND_DEL_YES)],
        [btn("Отмена", CB_SECOND_DEL_NO)],
    )


def no_access() -> dict:
    return kb(
        [btn("🚀 Подключиться за 1 минуту", CB_ONBOARD)],
        [btn("✨ Открыть демо", CB_DEMO)],
        [btn("💎 Тарифы", CB_PRICING)],
        [btn("🏠 В меню", CB_HOME)],
    )


def back_home() -> dict:
    return kb([btn("🏠 В меню", CB_HOME)])


def connect_actions(*, sub_url: str | None = None) -> dict:
    """Legacy alias — prefer platform_pick / platform_guide_actions."""
    return platform_pick(has_access=bool(sub_url))


def pricing(*, on_waitlist: bool = False) -> dict:
    rows: list[list[dict]] = [
        [btn("🚀 Подключиться за 1 минуту", CB_ONBOARD)],
        [btn("✨ Открыть демо", CB_DEMO)],
    ]
    if on_waitlist:
        rows.append([btn("✅ Вы в листе ожидания", CB_WAITLIST)])
    else:
        rows.append([btn("🔔 Узнать первым об оплате", CB_WAITLIST)])
    rows.append([btn("🔑 Мой доступ", CB_ACCESS)])
    rows.append([btn("🏠 В меню", CB_HOME)])
    return kb(*rows)


def help_actions(*, donate: bool = False) -> dict:
    """Help-only actions. Политика / Тарифы / Статус live on the home menu."""
    rows: list[list[dict]] = [
        [btn("🚀 Подключиться за 1 минуту", CB_ONBOARD)],
        [btn("💬 Написать нам", CB_SUPPORT)],
    ]
    if donate:
        rows.append([btn("🤍 Поддержать", CB_DONATE)])
    rows.extend(
        [
            [link("📂 GitHub · открытый код", REPO_URL)],
            [btn("🏠 В меню", CB_HOME)],
        ]
    )
    return kb(*rows)


def donate_actions() -> dict:
    return kb(
        [btn("❓ Поддержка", CB_HELP), btn("🏠 В меню", CB_HOME)],
    )


def policy_actions() -> dict:
    return kb(
        [btn("❓ Поддержка", CB_HELP), btn("🏠 В меню", CB_HOME)],
    )


def support_intro_actions() -> dict:
    return kb(
        [btn("✍️ Написать", CB_SUPPORT_COMPOSE)],
        [btn("📖 Сначала самопомощь", CB_SUPPORT_HELP)],
        [btn("🏠 В меню", CB_HOME)],
    )


def support_compose_actions() -> dict:
    return kb(
        [btn("❌ Отмена", CB_HOME)],
    )


def support_after_send_actions() -> dict:
    return kb(
        [btn("✍️ Ещё сообщение", CB_SUPPORT_MORE)],
        [btn("🗂 Закрыть диалог", CB_SUPPORT_CLOSE)],
        [btn("🏠 В меню", CB_HOME)],
    )


def support_closed_actions() -> dict:
    return kb(
        [btn("💬 Написать снова", CB_SUPPORT)],
        [btn("❓ Поддержка", CB_HELP), btn("🏠 В меню", CB_HOME)],
    )


def support_admin_actions(dialog_id: int) -> dict:
    return kb(
        [
            btn("↩️ Ответить", f"{CB_SUPPORT_REPLY}{dialog_id}"),
            btn("✅ Закрыть", f"{CB_SUPPORT_ADMIN_CLOSE}{dialog_id}"),
        ],
    )


def status_actions(*, admin: bool = False) -> dict:
    if admin:
        return kb(
            [btn("📊 Метрики", CB_METRICS), btn("🔑 Мой доступ", CB_ACCESS)],
            [btn("🏠 В меню", CB_HOME)],
        )
    return kb(
        [btn("🔑 Мой доступ", CB_ACCESS)],
        [btn("🏠 В меню", CB_HOME)],
    )


def god_home() -> dict:
    return kb(
        [btn("➕ Выдать · 30 дней", CB_GOD_CREATE_30)],
        [btn("➕ Выдать · 90 дней", CB_GOD_CREATE_90)],
        [btn("➕ Выдать · 365 дней", CB_GOD_CREATE_365)],
        [btn("🩺 Диагностика", CB_DIAG), btn("📋 Выданные", CB_GOD_LIST)],
        [btn("📊 Метрики", CB_METRICS), btn("🏠 В меню", CB_HOME)],
    )


def god_after_create() -> dict:
    return kb(
        [btn("➕ Ещё выдача", CB_GOD)],
        [btn("🩺 Диагностика", CB_DIAG), btn("📋 Выданные", CB_GOD_LIST)],
        [btn("🏠 В меню", CB_HOME)],
    )


def diag_actions() -> dict:
    return kb(
        [btn("🩺 Ещё nick", CB_DIAG)],
        [btn("⚙️ Godmode", CB_GOD), btn("🏠 В меню", CB_HOME)],
    )


def expiry_nudge_actions() -> dict:
    return kb(
        [btn("🔑 Мой доступ", CB_ACCESS)],
        [btn("💎 Тарифы", CB_PRICING)],
    )
