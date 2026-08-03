# Архитектура

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Личный каскад: **клиент → entry (зона DPI) → exit (за рубежом)**.

```
Клиент (Happ)
  → entry :443  VLESS + Reality + XHTTP
      ├─ geoip/geosite:ru → direct
      └─ остальное → hop → exit
  запасной профиль: прямой вход на exit (отдельный порт)
```

## Роли нод

| Роль | Назначение |
|------|------------|
| `entry` | Приём клиентов, split RU→direct, hop на exit |
| `exit` | Выход в интернет; внутренний hop; HTTP-подписка; бот |

Реальные IP/DNS — только в `inventory/hosts.local.env`.

## Плоскости

- **Data plane:** Xray (шаблоны в `configs/xray/`).  
- **Control plane:** Telegram-бот + SQLite + sync клиентов; опционально зеркало в отдельный inbound панели 3x-ui.  
- **Чужие сервисы** на той же VPS — не трогаем (см. [nl-coexist.md](nl-coexist.md)).

## Подписка

По умолчанию `SUB_FORMAT=links`: **plain** список `vless://` (не base64) + заголовки Happ (Profile-Title, Announce, autoconnect). Без `Content-Disposition: attachment` — на iOS это провоцировало диалог скачивания / «в разрешении отказано».  
`Content-Type`: `text/plain; charset=utf-8` (URI-list) или `application/json` (opt-in balancer).  
iOS Happ строже Android: opaque base64 в теле → «неизвестный тип контента».  
Профили с флагами стран в имени (география label).  
HY2 может быть выключен (`HY2_ENABLED=0`).

Публичный URL подписки — **всегда HTTPS** (`SUB_PUBLIC_BASE`, обычно `https://<NL_DOMAIN>:2080`).  
Cleartext `http://` бот не выдаёт (любая ОС). TLS на `:2080` (Let's Encrypt), порт `:443` Reality не трогаем.  
Выдача: `python scripts/enable_sub_https.py`, затем `python scripts/deploy_bot.py`.

## Безопасность репозитория

Секреты и прод-инвентарь не коммитятся. См. [../open-source.md](../open-source.md).
