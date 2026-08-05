# Типичные проблемы клиентов (и как закрыты навсегда)

> Для операторов. Клиентам — краткая версия в боте (**Поддержка**) и [../happ.md](../happ.md).

## Сводка

| Симптом | Частая причина | Сервер / продукт | Действие клиента |
|---------|----------------|------------------|------------------|
| Insecure HTTP / unexpected end of stream | Старая `http://` ссылка в Happ | `SUB_PUBLIC_BASE` всегда `https://` | Удалить профиль → `https://…` из бота |
| Неизвестный тип контента (iOS) | Тело подписки было base64 | Plain `vless://` + `text/plain` | Переимпорт той же `https://` ссылки |
| В разрешении отказано (iOS) | `Content-Disposition: attachment` | Заголовок убран | Переимпорт; Настройки → Happ → VPN |
| «RU мёртв», Direct работает | Happ autoconnect → NL Direct | **Autoconnect отключён** | Вручную **🇷🇺 RU**; не Direct |
| «RU мёртв» у всех, Direct OK, hop=0 | SelfSteal `xeno-steal-nl` (:9443, nginx) не отвечает → Reality :8443 не рукопожается | `systemctl restart xeno-steal-nl`; `curl -sk https://127.0.0.1:9443/`; при залипшем порте — `fuser -k 9443/tcp` и снова start | После фикса hop — снова **🇷🇺 RU** |
| Ozon / Магнит / WB / продукты / доставка видят VPN | CDN вне `geoip:ru` → NL hop | Явный bypass на entry (маркетплейсы, X5, ВкусВилл, Самокат, СберМаркет, CDEK, …) | Профиль **RU** + обновить подписку |
| Старая клавиатура бота | Telegram не обновляет inline на старых сообщениях | `/start` шлёт новое сообщение | `/start` → заново открыть экран |
| Windows: подключено, сети нет | TUN без прав админа | — | Happ от администратора; режим **Proxy** |
| Подписка «обновляется с задержкой» | Happ кэш / VPN включён при refresh | `Cache-Control: no-store` | VPN выкл → обновить |

## Инфраструктура vs клиент

**Перед «чинить сервер»** — smoke + digest:

```bash
python scripts/show_digest.py
# Godmode → Диагностика → @nick
```

Если smoke OK, hop fresh, UUID на RU и NL, а у одного юзера только Direct в логах — это **выбор профиля в Happ**, не падение RU.

Если в `nl-relay-access.log` **нет** `xeno-relay-hop` давно, а entry всё ещё пишет `accepted … [client-in -> nl-exit]` — cascade мёртв на hop. Частый виновник: `curl -sk https://127.0.0.1:9443/` таймаут → SelfSteal (nginx) down. Алерт `cascade_split` / smoke `nl_steal_https`. Не путать с UUID/SNI юзера.

## Профилактика (оператор)

После смены routing / Reality / формата подписки:

```bash
python scripts/repair_subscriptions.py
```

Делает `sync_all(rewrite_subs=True)` — переписывает все sub-файлы plain `vless://`, синхронизирует UUID на нодах.

## Sacred

Не путать «клиент на Direct» с «RU entry down». Не трогать чужие inbound’ы и `xeno.traiding` при repair.
