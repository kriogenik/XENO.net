# Ops changelog

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Краткие записи после правок. Без IP, паролей и UUID.

## 2026-08

## 2026-08-07 (permanent ban + ice1477)

- Таблица `banned_users`, `ban_and_purge` (DB + удаление sub dirs + sync Xray/3x-ui).
- `claim_demo` / `grant_access` / `has_access` блокируют бан навсегда; UX «Доступ закрыт».
- Ops: [bot.md](bot.md) § Постоянный бан.

## 2026-08-07 (RU Reality retarget → timeweb.cloud)

- Entry Timeweb AS9123: SNI/`dest` был `dl.google.com` (гео/ASN mismatch → ТСПУ легче режет). Скан с VPS: `timeweb.cloud` ~0.7 ms.
- Переключены только **BRIDGE** Reality (`timeweb.cloud` + serverNames timeweb.*). **NL Direct** оставлен на Google (у пользователей работает).
- `sync_all(rewrite_subs=True)`; клиентам обновить подписку → в URI RU `sni=timeweb.cloud`.
- Скрипт: `scripts/retarget_reality_ru_donor.py`.

## 2026-08-07 (cascade audit + telemetry blind spots)

- Полный аудит (`scripts/cascade_audit.py`): RU→hop **1:1** (`hop_ru_sourced`), pair keys/`stream-one` OK, `ru_hop` probe OK. Direct accepts ≫ RU unique — клиенты часто на backup, каскад серверно жив.
- **Закрыты дыры:** `path_stats` (canary vs RU-sourced hop); `cascade_split`/`hop_stale` больше не маскируются local canary; таймер `xenonet-ru-hop-watch`; classify XHTTP EOF/version; smoke учитывает `ru_hop_path` + path cascade.
- Docs: [cascade-audit.md](cascade-audit.md), observability/principles.

## 2026-08-07 (cadbl4: RU «мёртв», Direct OK — не cascade)

- Живые логи `tg-6941226246`: UUID на RU+NL Direct, sub URI = live Reality (pbk/sid/sni/path/`stream-one`).
- Hop с RU: ipify → NL exit OK; `hop_canary` ok; steal `:9443`=200; другие клиенты `client-in → nl-exit` + `xeno-relay-hop` непрерывно.
- У юзера: **12:19 UTC** длинная сессия RU (`185.97.201.22`); **13:26:57–59** четыре `accepted … [client-in → nl-exit]` (`185.97.201.67`: microsoft/cursor/opera), затем с **13:27** только `xeno-direct-in` с того же IP.
- Классификация: **не B** (hop/keys/steal живы), **не D** (не «только DNS»), **не C** (full-path accepts были). Ближе к **A′ / выбор профиля**: entry достижим, каскад серверно цел; сессия уходит на Direct (Happ autoconnect / не удерживают 🇷🇺 RU). NL→RU ipify не считали доказательством.
- Retarget SNI/host **не** делали — ждать «делаем», если при ручном RU 60с accepts снова ноль с их IP.

## 2026-08-07 (hop inbound mode=stream-one)

- Живой NL hop inbound (`xeno-relay-in`) был `mode=auto`, тогда как RU `nl-exit` и клиентские URI — `stream-one`. Исправлено на сервере + шаблоны `nl-coexist` / `nl-relay-only` / `relay` + `sync_nl_direct_clients_local` теперь форсит `stream-one` и на hop.
- Диагностика xenoworth: accepts на RU **есть** и бьются 1:1 с hop; sub URI ок (`ru:443` / `nl:2053`, верные pbk/sid, `stream-one`). «RU не работает» при зелёном hop ≠ «клиент не доходит» — смотреть выбор профиля / качество path, не крутить UUID.
- NL→RU ipify по-прежнему **не** доказательство Happ в РФ.

## 2026-08-07 (hop canary spam + iOS)

- Hop canary: transient `curl_rc=56/52` после недавнего OK — soft-skip (не открывает `hop_reality`); алерт только после **5** жёстких FAIL подряд (~15 мин).
- Probe: settle + 3 retry. FP по умолчанию `chrome` (вместо `randomized`) — стабильнее Reality/iOS.
- Docs: iOS VPN самоотключение → Happ Dev Settings → **No Limit Mode**.

## 2026-08-05 (hop Reality canary)

- Живой canary `:8443`: `diag.hop_watch` каждые 3 мин — VLESS+Reality → HTTP `127.0.0.1:19443` (узкое исключение из `geoip:private`→block; иначе ложный FAIL при живом каскаде).
- State `/var/log/xeno/hop_canary.json`; алерт `hop_reality` после ≥2 FAIL; `canary_busy` не эскалирует.
- Smoke critical: `nl_hop_reality`. Цепочка: units/steal → canary → log split/stale.
- Чеклист: [incident-cascade.md](incident-cascade.md).

## 2026-08-05 (media bypass)

- Entry domestic bypass: кино/сериалы. Явно `geosite:category-entertainment-ru` (ivi/okko/kinopoisk/…) + домены `bumazhniy-dom.com` (жалоба), more.tv, megogo, premier.one, kinorium, CDN (ivicdn/cdnvideohub/kinescope/trbcdn). Иначе .com-порталы уходили в NL hop → geo/VPN block.
- Клиентам: профиль **🇷🇺 RU** + обновить подписку. NL Direct не поможет.

## 2026-08-05 (SelfSteal nginx + cascade_split)

- SelfSteal на NL: **dedicated nginx** (`/etc/xeno/steal-nginx.conf`, unit `xeno-steal-nl`) вместо Python `ThreadingTCPServer` — устраняет клины :9443 под Reality-пробами.
- Миграция: `scripts/migrate_steal_nginx.py` (идемпотентно; sibling nginx/sites не трогает).
- Алерт `cascade_split`: RU accepts есть, hop quiet ≥45 мин при здоровых steal/relay.
- Принципы: [principles.md](principles.md).

## 2026-08-05 (observability)

- Единый ops-журнал `/var/log/xeno/events.jsonl` (smoke, steal-watch restart+reason, sync_all start/end/error, alerts, sub 404, bot unhandled, support flood, godmode, sacred denial, deploy).
- Digest: секция **Stability / security signals (last 24h)**.
- Steal watchdog → `python -m diag.steal_watch` (лог причины перед restart).
- Alert `reality_handshake_spike` (≥100/день).
- Docs: [observability.md](observability.md). Logrotate покрывает `events.jsonl`.

## 2026-08-05 (observability harden)

- Smoke: `xenonet-bot` + `xenonet-sub` в critical.
- Alerts: `unit_sub`, `sub_404_spike` (≥30/ч).
- `sub_404` events rate-limited 1/min per IP.

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
