# Ops changelog

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Краткие записи после правок. Без IP, паролей и UUID.

## 2026-08

## 2026-08-05 (observability)

- Единый ops-журнал `/var/log/xeno/events.jsonl` (smoke, steal-watch restart+reason, sync_all start/end/error, alerts, sub 404, bot unhandled, support flood, godmode, sacred denial, deploy).
- Digest: секция **Stability / security signals (last 24h)**.
- Steal watchdog → `python -m diag.steal_watch` (лог причины перед restart).
- Alert `reality_handshake_spike` (≥100/день).
- Docs: [observability.md](observability.md). Logrotate покрывает `events.jsonl`.

## 2026-08-05 (alerts stability)

- Алерты: state machine open / remind(6h) / recover; стабильные fingerprints (больше не `sni:{count}` / `smoke:{id}`).
- Smoke FAIL только после **2** подряд неудач.
- SelfSteal: `Restart=always` + таймер `xenonet-steal-watch` (curl :9443 → restart).
- Порог sni_spike 50; hop_stale смотрит last_seen за 3 дня и только если steal/relay здоровы.

## 2026-08-05 (retail bypass)

- Entry domestic bypass расширен (~100 доменов): маркетплейсы (Ozon/WB CDN), X5 (Пятёрочка/Перекрёсток/Чижик), ВкусВилл, Лента, Самокат, СберМаркет/Купер, Delivery Club, CDEK/Boxberry/Почта, электроника (М.Видео/DNS/Ситилинк), аптеки и DIY. Источник — v2fly domain-list + известные бренды. Нужен профиль **RU**.

## 2026-08-05 (smoke steal)

- Smoke: критичны `xeno-steal-nl` + локальный HTTPS на `:9443` (`nl_steal_https`) — ловит зависший SelfSteal до жалоб «RU мёртв / Direct ок».

## 2026-08-05 (вечер)

- **Incident:** у всех RU cascade «не работает», NL Direct OK. Причина — зависший SelfSteal `xeno-steal-nl` на `127.0.0.1:9443` (Recv-Q забит): Reality hop `:8443` перестал рукопожаться (~09:48 UTC). Hop accepts = 0 при живых entry accepts. Лечение: поднять steal (при `EADDRINUSE` — только pid steal на 9443), проверить `curl -sk https://127.0.0.1:9443/` и hop e2e. Sacred (3x-ui / trading) не трогали. В шаблоне SelfSteal — `allow_reuse_address` + `daemon_threads`.
- **Sub:** убран Happ `subscription-autoconnect` / `lowestdelay` — из‑за него клиенты молча сидели на NL Direct и думали, что RU «сдох». В боте и docs: вручную **🇷🇺 RU**.
- **Routing:** bypass Wildberries (`wildberries.ru`, `wb.ru`) на entry.
- **Ops:** [common-issues.md](common-issues.md) — матрица типичных кейсов; `scripts/repair_subscriptions.py` — массовый rewrite subs + sync.
- Smoke: проверка `SUB_PUBLIC_BASE` начинается с `https://`.

## 2026-08-05

- Entry routing: явный bypass Ozon/Magnit CDN (`domain:ozon.ru`, `ozone.ru`, `o3.ru`, `magnit.ru` и др.) → direct, в дополнение к `geosite:category-ru` / `geoip:ru`. Иначе API/CDN уходили в NL hop и приложения видели зарубежный IP.
- Клиентам: для магазинов — профиль **RU**, не NL Direct; обновить подписку после выката.

- Sub/Happ iOS: убран `Content-Disposition: attachment` (Safari мог уходить в «скачать файл» → «в разрешении отказано»); тело — plain `vless://`, `text/plain` + Profile-Title/Announce.
- Sub/Happ iOS: тело подписки — plain `vless://` (не base64); legacy base64 на лету декодируется. Было: base64 + `text/plain` → на iOS «неизвестный тип контента».
- Digests, smoke, admin alerts, hot-add (полный inbound для Xray 26 `adu`).  
- UX: флаги профилей, второе устройство, онбординг, waitlist, expiry nags.  
- Публикация репо: inventory → шаблон + `hosts.local.env`; доки на русском без прод-разведки; ссылка на git в Поддержке бота.
- Подписка: бот всегда выдаёт `https://` (`SUB_PUBLIC_BASE`); TLS на `:2080`. Старый `http://` в Happ → «unexpected end of stream» / insecure — переимпорт.
- Донаты: `DONATE_USDT_TRC20` / `DONATE_TON` / `DONATE_BTC` в `bot.env` → **Поддержка → Поддержать** (без значений кнопка скрыта).
- Windows Happ: подсказки (админ / TUN→Proxy) в `docs/happ.md` и гайде бота — та же sub, что на Android; сбой сети на ПК чаще клиентский.
- UX: из Поддержки убраны дубли **Политика / Тарифы / Статус** (остаются только в главном меню).
- UX: пункт меню и экран **Справка** переименованы в **Поддержка** (диалог «Написать нам» без изменений).
- UX: убран дубль меню **Клиенты** (экран/кнопки/`x:apps`); установка Happ остаётся в **Подключить устройство** / онбординге.
- UX: убрана кнопка **Обновить** (`x:refresh`); актуальное состояние — через **Мой доступ**.
- UX: в **Мой доступ** при двух ссылках — **Убрать устройство 2** (подтверждение → revoke slot 2 в DB / Happ sub / 3x-ui / Xray). Primary UUID/token не трогаем.
- UX: убраны кнопки **Устройство 1/2** (open-URL); при двух ссылках — только текст + **Убрать устройство 2**. При одной — **Открыть подписку** как раньше.
- UX: **/start** и **Мой доступ** всегда шлют новое сообщение (не только edit); при сбое edit — fallback на send. Старые inline-кнопки в истории не обновляются сами — нужен `/start` или повторный «Мой доступ», не «очистить чат».
