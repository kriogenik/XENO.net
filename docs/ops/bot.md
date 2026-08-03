# Бот — эксплуатация

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Клиентский UX: [../bot.md](../bot.md). Здесь — только деплой и сопровождение.

## Установка

Каталог на сервере и unit’ы задаёт оператор (пример: `xenonet-bot`, `xenonet-sub`).

```bash
# secrets/bot.env, secrets/*-access.env, inventory/hosts.local.env
python scripts/deploy_bot.py
```

В `bot.env`: `BOT_TOKEN`, `ADMIN_IDS`, Reality-ключи, `XUI_*`, при необходимости `XUI_SACRED_INBOUND_IDS`.

Опционально — донаты (кнопка **Поддержка → Поддержать** только если задан хотя бы один адрес):

```bash
DONATE_USDT_TRC20=T...   # USDT TRC20
DONATE_TON=UQ...         # TON
DONATE_BTC=bc1...        # BTC
```

Для Happ (iOS / Android / desktop) URL подписки всегда **HTTPS**:

```bash
python scripts/enable_sub_https.py   # LE-сертификат на NL_DOMAIN, :80 ACME, TLS на :2080
python scripts/deploy_bot.py
```

В env: `SUB_PUBLIC_BASE=https://<NL_DOMAIN>:2080`, `SUB_TLS_CERT`, `SUB_TLS_KEY`.  
Бот никогда не выдаёт `http://…` для подписки. Токены клиентов не ротируются — меняется только схема/хост в ссылке.  
`xenonet-sub` отдаёт plain список `vless://` (`text/plain` + `Content-Disposition`); iOS Happ не принимает opaque base64 как тип контента.

## Поведение sync

- Новые клиенты: hot-add через Xray API (полный inbound для Xray 26+).  
- Чужие inbound’ы панели (sacred IDs) — отказ.  
- Сторонние сервисы на машине не трогаем.

## Таймеры

Collect / smoke / digests / expiry nags — systemd timers на exit-ноде.  
Алерты только `ADMIN_IDS`. Дайджесты — файлы на диске, без истории серфинга.

## Godmode

Выдача 30/90/365, список, диагностика по `@nick` — только админам из env.

## Диалог (поддержка)

Клиент пишет через **Поддержка → Написать нам** или `/dialog`.  
Бот шлёт всем `ADMIN_IDS` карточку контекста (`@nick`, tg id, план, активен/истёк, waitlist, число устройств, короткий id `D-xxxx`) и копию сообщения.

**Как ответить**

1. **Reply** на карточку или на копию сообщения в личке с ботом — ответ уйдёт клиенту от имени бота.  
2. Или кнопка **Ответить** под карточкой → следующее сообщение.

Закрыть: кнопка **Закрыть** у админа или **Закрыть диалог** у клиента.  
Тишина ~72 ч — авто-закрытие. Тела сообщений в SQLite не храним (только метаданные и map `message_id`).
