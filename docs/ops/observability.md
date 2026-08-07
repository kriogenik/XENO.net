# Наблюдаемость (операторы)

Структурированные ops-события + существующие journal/file-логи каскада.
Не APM и не MITM клиентского трафика: **назначения (куда ходил пользователь) не пишем**.

## Главный журнал событий

| Что | Где |
|-----|-----|
| JSON Lines | `/var/log/xeno/events.jsonl` |
| Ротация | `/etc/logrotate.d/xeno` — daily, 14 копий, max 100M, `copytruncate` |
| Писатель | `bot/ops_events.py` (`emit`) |

Каждая строка — один JSON-объект: `ts` (UTC ISO), `kind`, плюс поля события.

### Виды (`kind`)

| kind | Когда | Полезные поля |
|------|--------|----------------|
| `smoke_result` | hourly smoke | `ok`, `summary`, `failed` |
| `steal_watch_restart` | watchdog рестартит SelfSteal | `reason` (`https_9443_no_response`), `action` |
| `hop_canary` | Reality canary :8443 | `ok`, `detail`, `consecutive_fail`, `elapsed_ms` |
| `sync_all_start` / `sync_all_end` / `sync_all_error` | `provision.sync_all` | `clients`, `duration_ms`, `error`/`detail` |
| `alert_open` / `alert_remind` / `alert_recover` | Telegram alert FSM | `key`, `fingerprint` |
| `sub_404` | неверный sub token | `token_prefix` (4 символа…), `ip_trunc`, `ip_hash` |
| `bot_unhandled_error` | исключение в update loop | `where`, `error` |
| `support_flood` | превышен rate limit поддержки | `tg_id`, `limit`, `window_sec` |
| `godmode_action` | выдача / отказ godmode | `action`, `admin_tg_id`, `days` |
| `sacred_denied` | отказ трогать sacred inbound | `detail` |
| `deploy` | конец `deploy_bot.py` | `host`, `ok`, `root` |

### Как читать

```bash
# хвост
tail -n 50 /var/log/xeno/events.jsonl

# только рестарты steal / ошибки sync
grep -E 'steal_watch_restart|sync_all_error' /var/log/xeno/events.jsonl | tail

# 404 по подписке за сегодня (UTC дата в ts)
grep sub_404 /var/log/xeno/events.jsonl | grep "$(date -u +%Y-%m-%d)" | wc -l

# jq (если установлен)
tail -n 200 /var/log/xeno/events.jsonl | jq -r 'select(.kind=="alert_open") | [.ts,.key] | @tsv'
```

Полный IP и полный sub-token **не** хранятся: только `a.b.c.x` / короткий hash и префикс токена.

## Что ещё есть (не events.jsonl)

| Компонент | Файл / journal | Примечание |
|-----------|----------------|------------|
| RU Xray access/error | `/var/log/xeno/ru-access.log`, `ru-error.log` | ingest → SQLite digests |
| NL relay access/error | `/var/log/xeno/nl-relay-access.log`, `nl-relay-error.log` | hop = email `xeno-relay-hop` |
| Provision / sync actions | `/var/log/xeno/provision.log` | строки `ru sync:` / user lines |
| Digests | `/var/log/xeno/digests/{daily,weekly,monthly}/`, `latest-*.md` | секция **Stability / security signals** за 24h |
| Smoke | `/var/log/xeno/digests/smoke/latest.md` | + `smoke_result` в events |
| Bot / sub | `journalctl -u xenonet-bot` / `xenonet-sub` | плюс events для unhandled / 404 |
| Steal HTTP | **намеренно silent** | доступ не логируем; смотрим HTTPS health + watch |
| Alerts | Telegram → `ADMIN_IDS` | state в SQLite `diag_alert_state` |

Таймеры (не второй стек мониторинга):

- `xenonet-diag-collect.timer` — 5 мин, ingest + alerts  
- `xenonet-diag-smoke.timer` — 1 ч  
- `xenonet-diag-digest.timer` — 03:10 UTC  
- `xenonet-steal-watch.timer` — 2 мин → `python -m diag.steal_watch`  
- `xenonet-hop-watch.timer` — 3 мин → `python -m diag.hop_watch` (Reality :8443 → SelfSteal)

## Алерты (Telegram)

Ключи: `hop_reality` (canary ×5+ жёстких FAIL; transient curl_rc=56/52 после недавнего OK — soft-skip), `hop_stale`, `cascade_split` (RU accepts, hop quiet ≥45м), `unit_xeno_relay`, `unit_xeno_steal`, `steal_https`, `unit_bot`, `unit_sub`, `disk`, `sni_spike`, `reality_handshake_spike` (≥100/день), `sub_404_spike` (≥30/час), `smoke_fail` (×2 подряд).

Цепочка (пересечения глушатся): **steal/units → hop_reality → cascade_split / hop_stale**.  
Инцидент «все RU лежат»: [incident-cascade.md](incident-cascade.md).

Переходы open / remind(6h) / recover пишутся в `events.jsonl`.

`sub_404` в events — **rate-limited** 1/мин на IP (сканеры не заливают диск).

## Digest: секция stability/security

В daily/weekly/monthly Markdown после Hints:

- smoke OK/FAIL counts  
- hop Reality canary OK/FAIL  
- steal watch restarts + reason  
- sync_all errors  
- sub 404 count + top truncated IPs  
- bot unhandled  
- support flood / godmode / sacred denials / alert actions  

Источник — разбор `events.jsonl` за последние 24 часа (`ops_events.summarize_last_hours`).

## Retention

| Артефакт | Политика |
|----------|----------|
| `*.log` + `events.jsonl` | logrotate 14 дней / 100M |
| digests `*.md` | `retention_cleanup` ~90 дней (кроме `latest-*`) |
| SQLite rollups | живут в DB бота; отдельные TTL не режем |

## Остаточные слепые зоны (честно)

- Нет MITM и **нет логов destinations** (куда ходил клиент) — политика privacy.  
- SelfSteal = dedicated nginx на `:9443` (`access_log off`); здоровье — HTTPS probe + `xenonet-steal-watch`.
- Hop Reality = локальный canary (`hop_canary.json` + `xenonet-hop-watch`): VLESS на `:8443` → HTTP `127.0.0.1:19443` (исключение из private-block). Чужие URL не трогает.  
- HY2: unit есть, отдельного access-журнала в продукте нет (сейчас parked).  
- Нет Prometheus/Sentry/APM — files + Telegram + digest.  
- SSH mid-sync: `sync_all_error` + journal; полный SSH transcript не пишем.

См. также: [incident-cascade.md](incident-cascade.md), [runbook.md](runbook.md), [common-issues.md](common-issues.md), [bot.md](bot.md).
