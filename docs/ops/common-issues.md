# Типичные проблемы клиентов (и как закрыты навсегда)

> Для операторов. Клиентам — краткая версия в боте (**Поддержка**) и [../happ.md](../happ.md).

## Сводка

| Симптом | Частая причина | Сервер / продукт | Действие клиента |
|---------|----------------|------------------|------------------|
| Insecure HTTP / unexpected end of stream | Старая `http://` ссылка в Happ | `SUB_PUBLIC_BASE` всегда `https://` | Удалить профиль → `https://…` из бота |
| Неизвестный тип контента (iOS) | Тело подписки было base64 | Plain `vless://` + `text/plain` | Переимпорт той же `https://` ссылки |
| В разрешении отказано (iOS) | `Content-Disposition: attachment` | Заголовок убран | Переимпорт; Настройки → Happ → VPN |
| «RU мёртв», Direct работает | Happ autoconnect → NL Direct | **Autoconnect отключён** | Вручную **🇷🇺 RU**; не Direct |
| «RU мёртв» у всех, Direct OK, hop=0 | SelfSteal или hop Reality | см. [incident-cascade.md](incident-cascade.md): curl `:9443` → `hop_watch` → restart steal/relay | После фикса — снова **🇷🇺 RU** |
| Ozon / Магнит / WB / продукты / доставка видят VPN | CDN вне `geoip:ru` → NL hop | Явный bypass на entry (маркетплейсы, X5, ВкусВилл, Самокат, СберМаркет, CDEK, …) | Профиль **🇷🇺 RU** + обновить подписку |
| Кино / сериалы (bumazhniy-dom и др.) видят VPN | .com-портал вне geosite → NL hop | `geosite:category-entertainment-ru` + явные media-домены → direct на entry | Профиль **🇷🇺 RU** + обновить подписку |
| Старая клавиатура бота | Telegram не обновляет inline на старых сообщениях | `/start` шлёт новое сообщение | `/start` → заново открыть экран |
| VPN сам отваливается при открытии TG/сайта (iOS) | iOS убивает VPN-процесс по RAM | Happ → Dev Settings → **No Limit Mode**; свежий Happ | Обновить подписку, профиль **🇷🇺 RU** |
| После сброса Happ оба профиля «мертвы», сервер «зелёный» | В URI был `mode=auto` (XHTTP+Reality); или TUN/No Limit/кэш Happ | Подписки с `mode=stream-one`; сверить accepts в RU/NL логах по email | Удалить профиль → свежая `https://` из бота → вручную **🇷🇺 RU**; iOS No Limit; Windows — админ/Proxy. Если в логах уже есть `accepted … email: tg-…` — сервер OK, не крутить UUID |
| «Оба мёртвы», но в логах accepts RU+Direct с IP юзера | Клиентский UX / сброс Happ во время короткого restart sync | Ban/purge чужого tg не трогает остальных; `sync_all` после бана | Та же ссылка; VPN выкл → обновить sub → **🇷🇺 RU** 60с |
| «RU мёртв», Direct OK, но в RU логах accepts + hop 1:1 | Клиент на path, UX/healthcheck; или hop inbound был `auto` | Hop/Direct inbound = `stream-one`; не путать с «нет accepts» | Вручную **🇷🇺 RU** 30с; при нуле accepts — DNS/ISP/SNI plan |
| «RU мёртв», Direct OK; короткие RU accepts → сразу Direct с того же IP | Happ не удерживает 🇷🇺 RU (autoconnect/failover / красный ping); каскад на сервере жив | Сверить URI↔RU inbound; hop 1:1; чужие `client-in→nl-exit`; см. changelog xenoworth localize | Autoconnect off → вручную **🇷🇺 RU** 60с (открыть сайт, не только ping); не крутить UUID; retarget SNI только после нуля accepts |
| Direct «работает, но очень медленно», RU красный | Backup path RF→NL:2053 (шейпинг/RTT); NL не перегружен | load/BW + Direct `stream-one`; stats uplink/downlink; не путать с «нет сети» | Сначала удержать RU; Direct — запасной, скорость чинить отдельно (не retarget RU вслепую) |
| «У всех RU мёртв», Direct OK; IP Timeweb; spamlist чист | ТСПУ/гео-mismatch Reality (Google SNI на RU VPS) | BRIDGE donor = colocated (`timeweb.cloud`); см. retarget | Обновить подписку; профиль **🇷🇺 RU** |
| «У всех RU мёртв», а smoke/canary зелёные | Local canary маскировал hop quiet; `accepted` ≠ UX | `path_stats` + `hop_ru_sourced` + `ru_hop_canary`; см. [cascade-audit.md](cascade-audit.md) | `python scripts/cascade_audit.py`; вручную **🇷🇺 RU** |
| «Все новые ссылки мёртвые / не поднимаются» | Часто: сервер OK (sub 2 профиля, UUID на RU+Direct, steal/hop зелёные), клиент не удерживает RU или не обновил sub после retarget | Triage: `:9443` → hop canary → sample OLD/NEW sub → E2E xenoworth+новый UUID; `sync_all(rewrite_subs=True)` без ротации токенов | VPN выкл → обновить ту же `https://` → **🇷🇺 RU** 60с; не ждать «зелёного ping» |
| Подписка «обновляется с задержкой» | Happ кэш / VPN включён при refresh | `Cache-Control: no-store` | VPN выкл → обновить |

## Инфраструктура vs клиент

**Перед «чинить сервер»** — smoke + digest:

```bash
python scripts/show_digest.py
# Godmode → Диагностика → @nick
```

Если smoke OK, hop fresh, UUID на RU и NL, а у одного юзера только Direct в логах — это **выбор профиля в Happ**, не падение RU.

Если в `nl-relay-access.log` **нет** `xeno-relay-hop` давно, а entry всё ещё пишет `accepted … [client-in -> nl-exit]` — cascade мёртв на hop. Смотри [incident-cascade.md](incident-cascade.md): `curl :9443`, `hop_canary.json` / `diag.hop_watch`. Алерт `hop_reality` / `cascade_split`. Не путать с UUID/SNI юзера.

## Профилактика (оператор)

После смены routing / Reality / формата подписки:

```bash
python scripts/repair_subscriptions.py
```

Делает `sync_all(rewrite_subs=True)` — переписывает все sub-файлы plain `vless://`, синхронизирует UUID на нодах.

## Sacred

Не путать «клиент на Direct» с «RU entry down». Не трогать чужие inbound’ы и `xeno.traiding` при repair.
