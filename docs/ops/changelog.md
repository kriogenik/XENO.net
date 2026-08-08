# Ops changelog

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Краткие записи после правок. Без IP, паролей и UUID.

## 2026-08

## 2026-08-08 (~11:25 MSK) cadbl4 — stale RU SNI в sub после Google restore

- **Жалоба:** обновил sub — не работает; паттерн PC RU ок / mobile RU нет (общий контекст).
- **Пруф server mismatch (не Happ-лекция):** live `client-in` = `dl.google.com` + `stream-one`; xenoworth `:2080` = Google MATCH; **cadbl4 primary** `:2080` отдавал 🇷🇺 с `sni=timeweb.cloud` при том же pbk/sid/mode/UUID. Единственный timeweb-sub во флоте. UUID на RU+Direct, active, не banned, token `198005b7…` не ротировали.
- **Логи:** cadbl4 `client-in` historically жирный (176.15.* mobile + 185.97.*); обрыв после Aug 7 (retarget/timeweb день → 120 accepts); **Aug 8 = 0** accepts RU и Direct. xenoworth в том же окне: тысячи `client-in→nl-exit` с PC `88.201.*` (каскад Google жив).
- **Фикс:** `write_access_sub` только cadbl4 → оба профиля `sni=dl.google.com`; Reality **не** трогали; mass-retarget нет. Backup старого sub убран из `www/sub`.
- **Клиенту cadbl4:** VPN выкл → refresh **ту же** `https://nl…:2080/sub/…` → вручную 🇷🇺 60с (сайт). Mobile: Private DNS off / батарея / TUN / переимпорт. Slot‑2 у него inactive — не путать.

## 2026-08-08 (~09:50 MSK) RESTORE Aug3-like BRIDGE — Google SNI снова (эксперимент)

- **Мандат:** «снова ноль» — сравнить NOW vs пик Aug 3–4; не верить в «TSPU навсегда»; onset связывали с фиксом алертов → проверить хронологию.
- **Пик Aug 3–4 (git/changelog/retarget notes):** BRIDGE Reality = `dl.google.com`; XHTTP default в коде = `auto`; hop SelfSteal Python `:9443`; Direct Google; sub `:2080` HTTPS; DNS/IP те же.
- **NOW до restore:** BRIDGE = `timeweb.cloud` (retarget **Aug 7**, не «после алертов»); XHTTP = `stream-one` (Aug 7 Happ-fix); SelfSteal = **nginx** (Aug 5, после hang); hop/Direct `stream-one`; `NL_DOMAIN`→direct + ALPN `http/1.1` (Aug 7); xray **26.3.27** mtime Aug 2; DNS NL/RU = live IP.
- **Честная причинность:** коммиты алертов Aug 5 (`ops_events`, steal-watch, hop canary) **не пишут** Reality/SNI клиентского path. Diag, что трогает сервисы: `steal_watch` (restart nginx `:9443`) и одноразовый canary-route в hop. **Вероятный infra-onset:** Aug 5 вечер SelfSteal hang; затем Aug 7 retarget/timeweb + stream-one. История «алерты → на след. день сдох» сдвинута ~на сутки и смешивает bot-obs с SelfSteal.
- **Сделано live:** BRIDGE `dest/serverNames` → `dl.google.com` (согласованная пара); secrets+`sync_all(rewrite_subs=True)` один раз; 27/27 sub на `:2080` с `sni=dl.google.com` + `stream-one`; E2E loopback Google → exit NL; SelfSteal nginx **не** откатывали (`:9443`=200); Direct Google без изменений; токены/UUID **не** ротировали. XHTTP **`auto` не возвращали** (ломает Happ) — остался `stream-one`. Legacy `:2096` — отдельно к тому же SNI (канон всё равно `:2080`).
- **Клиенту:** VPN выкл → refresh primary `https://nl…:2080/sub/…` → вручную **🇷🇺 RU** 60с (сайт). PC Direct должен остаться живым.

## 2026-08-08 (~09:05 MSK) Split A/B — PC Direct OK / RU «мёртв»; mobile Direct dead

- **Симптом (TRUST):** PC — Direct работает, 🇷🇺 RU нигде; телефон — даже Direct нет.
- **URI↔Reality:** primary + slot‑2 `:2080` → 200 / 2×`vless://`; RU `sni=timeweb.cloud` + `stream-one` == live `client-in`; Direct Google == live. **Mismatch нет** → `rewrite_subs` не делали.
- **Cascade:** SelfSteal `:9443`=200; hop + `ru_hop_canary` ok; E2E с RU по public hostname (параметры из sub) → exit NL + YouTube 200; Direct с RU→NL:2053 → exit NL. Токены/UUID **не** ротировали.
- **Accepts PC:** сегодня после restore были `client-in→nl-exit` (в т.ч. YouTube/Cursor ~05:08 UTC) — Reality+hop с Wi‑Fi PC **живы**. Сейчас PC сидит на Direct (пачка accepts Cursor), на RU:443 ESTAB с PC нет → профиль 🇷🇺 не удерживается / не выбирается, не «каскад лёг».
- **Не «нужен новый RU VPS» сейчас:** критерий «zero RU accepts с PC при живом Direct» **не** выполнен (были accepts сегодня; URI ок; E2E ок). Новый ASN — только если после ручного 🇷🇺 60с снова **ноль** accepts с PC при живом Direct.
- **Mobile Direct:** с PC Direct ESTAB/accepts есть; с mobile IP — редкие DNS/короткие, без устойчивого ESTAB → клиент Happ/TUN/OS, не down `:2053`.
- **Legacy RU `:2096`:** unit `xeno-sub` жив, но это **старый однотокенный** bootstrap (другой token) — primary token там 404/пусто. Канон только `:2080`.
- **Сделано live:** soft restart `xray` (RU) + `xeno-relay` (сброс Send‑Q зомби на Direct). BRIDGE остаётся `timeweb.cloud`.
- **Клиенту:** одна ссылка для чистого импорта — **slot‑2** `:2080` (primary цел). PC: VPN выкл → refresh → вручную 🇷🇺 60с (сайт). Mobile: 3 шага ниже в ответе юзеру.

## 2026-08-08 (~08:50 MSK) FORENSICS + ROLLBACK — честно, без Happ-диагноза

- **Мандат:** хватит кругов с «сервер зелёный». Читали changelog + `git log` Aug 2–8; откат рискованных конфигов на live NL+RU.
- **Что ломали МЫ (не unpaid / не SelfSteal hang):**
  1. **Aug 7 Reality retarget → `timeweb.cloud`** без доводки всех поверхностей: live Reality уже timeweb, а legacy RU `:2096` ещё отдавал 🇷🇺 с `sni=dl.google.com` → handshake EOF / «VPN есть, интернета нет». Канон `:2080` был ок; bootstrap — отравлен.
  2. **Мягкие рестарты Aug 8** → краткие дубли unit (`xray-relay` поверх `xeno-relay`, `xeno-sub` поверх `xenonet-sub`) роняли `:2080`/relay на минуты.
  3. **Процесс:** canary/E2E с VPS выдавали за UX → gaslighting.
- **Что НЕ откатываем (реальные инциденты / фиксы):** SelfSteal nginx (Aug 5 hang был настоящий); unpaid/restore Aug 8; `stream-one` (чинит Happ при `mode=auto`); `NL_DOMAIN`→direct + ALPN `http/1.1` (чинит refresh RST); ban ice1477 (xenoworth не трогал).
- **Решение по SNI:** **НЕ** романтизировать Aug 3 `dl.google.com` на BRIDGE. Доказано E2E: timeweb SNI → exit NL; Google SNI против live timeweb Reality → EOF. Google был яд **mismatch на `:2096`**, не «донор убил Wi‑Fi». BRIDGE остаётся **`timeweb.cloud`**; Direct — Google.
- **Сделано live:** backup `bot.db` → `bot.db.bak.forensic_*`; secrets/inbound согласованы (RU dest/names=timeweb, mode=`stream-one`; NL Direct Google + hop `stream-one`); `sync_all(rewrite_subs=True)` один раз; дублей `xray-relay`/`xeno-sub` нет; `:2080` ALPN `http/1.1`, primary sub 200 / 2×`vless://` (RU `sni=timeweb.cloud`, Direct `dl.google.com`); RU cascade E2E → exit NL. Токены/UUID **не** ротировали. UFW force reset **не** делали.
- **Клиенту (primary):** обновить **ту же** primary `https://nl…:2080/sub/…` (не `:2096`) → вручную 🇷🇺 RU 60с. Slot‑2 — запасной чистый профиль, primary не заменяли.

## 2026-08-08 (~08:40 MSK) P0 «нихуя не работает» — внешний пруф + slot‑2

- **Внешняя досягаемость (не curl NL→NL):** с workspace TCP OK `nl:2080`, `nl:2053`, `ru:443`; sub HTTPS 200, 2×`vless://`. С RU→NL TCP 2053/2080/8443 OK; NL→RU TCP 443 OK. OpenSSL Reality-dest: NL:2053 → `*.google.com`, RU:443 → `*.timeweb.cloud`, NL:2080 → LE `nl.…`.
- **Health:** оба VPS uptime ~25–30 мин после утреннего unpaid/restore (boot ~05:08 UTC). Disk/load OK. `xeno-relay` `:2053`/`:8443`, `xenonet-sub` `:2080`, RU `xray` `:443`, UFW на месте. Reality pbk live == sub (Direct/RU/hop).
- **Реальный VLESS E2E (xray client → ipify):** NL local Direct → `37.220…`; с RU: hop `:8443`, Direct hostname `:2053`, RU hostname `:443` — все три → exit NL. Access log: `tg-7880252399` / hop accepts на тесте.
- **Ложный «всё красное» в раннем triage:** CRLF в залитых с Windows shell-скриптах рвал remote bash; плюс минутный дубль unit `xray-relay` поверх `xeno-relay` (откатили). После binary-safe прогона — зелёный.
- **Сделано:** убран дубль unit; soft restart `xeno-relay` + RU `xray`; выдан **slot‑2** xenoworth (новый UUID/token, primary не трогали). Backup Direct на `:443` **не** добавляли — порт занят 3x-ui coexist.
- **Клиенту сейчас:** импорт **свежей** slot‑2 ссылки (чистый профиль). Primary URL цел. Если slot‑2 тоже мёртв при зелёном E2E с VPS — это клиент/ISP на телефоне, не «сервер down»; новый RU VPS не требуется.

## 2026-08-08 (~08:25 MSK) xenoworth «пару сек и умерло — ни LTE ни Wi‑Fi, ни RU ни Direct»

- **Отбой** гипотезы «LTE OK / Wi‑Fi fail» как primary: юзер уточнил — коротко поднялось, потом мёртво на обеих сетях и обоих профилях.
- **Triage NL+RU (live):** оба VPS SSH OK, uptime ~20 мин после утреннего unpaid/restore (не повторный suspend). Disk/OOM чисты. SelfSteal `:9443` Recv‑Q=0 HTTPS 200 (не Aug‑5 hang). `xeno-relay` active, listens `:2053`/`:8443`, UFW 2053 Anywhere / 8443←RU. `xenonet-sub` `:2080` active, sub token → 200 / 2×`vless://`. RU `xray` `:443` active. Hop canary ok.
- **E2E (после soft restart):** Direct loopback → exit NL; RU cascade (timeweb SNI) → exit NL; bad Google SNI → EOF. Токены/UUID **не** ротировали.
- **Логи xenoworth:** ~05:22 UTC короткая пачка mobile `client-in→nl-exit` (TG/IG/WA), затем тишина; при этом до restart на NL/RU висели **ESTAB** с mobile IP на `:2053` и `:443` с большим Send‑Q (зомби‑сессии: «подключилось на секунды» → трафик встал, сокеты живы).
- **Сделано на сервере:** soft restart `xeno-relay` + RU `xray` (сброс зомби ESTAB). Краткий самострел: ошибочно создали дубль unit `xeno-sub` поверх живого `xenonet-sub` → `:2080` упал на ~1 мин; откатили, `xenonet-sub` снова active/200.
- **Вывод:** infra path не down. Симптом = короткая сессия + залипший клиентский туннель после restore, не «VPS выключили» и не Wi‑Fi‑ISP. Клиенту: VPN выкл → та же `https://…:2080` → вручную RU 60с (сайт); при красном UI — выкл/вкл VPN чтобы сбросить ESTAB.
- Не считать NL canary доказательством UX на телефоне.

## 2026-08-08 (~08:10 MSK) unpaid → restore: «RU zero / Direct PC OK / mobile Direct fail»

- **Billing:** оба VPS остановлены ~03:22 UTC, boot ~05:08 UTC (uptime минут после оплаты). IP **не** сменились: `ru.`/`nl.` DNS = live RU/NL. Disk RW, UFW на месте, NL SelfSteal `:9443`=200, hop canary + `ru_hop_canary` ok, `:2080` слушает.
- **Не Aug‑5 SelfSteal hang** на entry: RU Reality dest = `timeweb.cloud` (не `:9443`). Nginx на RU inactive — для текущего entry не нужен.
- **Поезон:** legacy bootstrap sub на RU `:2096` отдавал **🇷🇺 RU с `sni=dl.google.com`** при live `serverNames=timeweb.*` → Reality handshake EOF / «VPN есть, интернета нет». Direct в том же файле с Google SNI — ок. **Канон `https://nl…:2080` уже был с `sni=timeweb.cloud`.**
- **Фикс live:** переписали RU `:2096` sub → `sni=timeweb.cloud` + `mode=stream-one`, `systemctl restart xeno-sub`.
- **Слой-пруф:** loopback RU + NL→RU public с timeweb SNI → `api.ipify.org` = NL exit; тот же клиент с `dl.google.com` SNI → TLS EOF. После boot у owner PC (`88.201…`) были `client-in→nl-exit` + hop 1:1 (YouTube/Cursor) — каскад для РФ-Wi‑Fi жив. Mobile IP (`81.9…`) на RU только DNS; Direct с телефона (LTE+Wi‑Fi) при живом PC Direct → **клиент Happ/OS**, не down inbound `:2053`.
- **Честно про прошлые «всё зелёное»:** canary/E2E с VPS ≠ UX в Happ; unpaid-окно реально роняло всё; после restore серверный каскад поднялся, но отравленный legacy sub и mobile-клиент давали ложную картину «RU мёртв навсегда».
- Клиентам: VPN выкл → обновить **`https://nl…:2080/sub/…`** (не `:2096`) → вручную **🇷🇺 RU** 60с. Mobile Direct: Private DNS off / battery / TUN / переимпорт; не крутить UUID пока PC Direct OK.

## 2026-08-07 (~22:30 MSK) refresh sub на RU → «удаленный хост закрыл соединение»

- **Что ломалось:** refresh `https://nl.<domain>:2080/sub/…` при включённом 🇷🇺 RU.
- **Корни (два слоя):**
  1. Hop Reality `serverName` = тот же `NL_DOMAIN`. Уводить sub-host в `nl-exit` → XHTTP RST / unexpected EOF (проверено e2e). Hairpin через hop **нельзя**, пока SNI hop = hostname sub.
  2. Sub TLS: aiohttp только HTTP/1.1; без явного ALPN клиент может слать h2 PRI → обрыв («удаленный хост закрыл соединение»).
- **Фикс:** на entry явно `NL_DOMAIN` → **direct** (до category-ru; так и задумано: cleartext RU→NL:2080). Sub: ALPN только `http/1.1`. Токены не ротировали.
- **Клиентам:** надёжнее обновлять sub с **VPN выкл**; на RU после фикса тоже должно открываться. Обычные сайты на RU — через hop (серверно OK; в Happ держать 🇷🇺 RU вручную 60с).
- Код: `control_plane_sub_direct_rules`, `bot/sub_server.py` ALPN, шаблон `relay.json.template`.

## 2026-08-07 (~22:00 MSK) xenoworth «ру лежит, директ лежит» — сервер НЕ down

- Жалоба: оба профиля мёртвы в Happ (после ответа ~20:33 MSK «сервер OK»). Мандат: re-triage + client-mirror E2E + accepts по UUID, не списывать на Happ без пруфа.
- **Инфра сейчас:** xray NL relay (2053/8443) + RU `:443` active; SelfSteal `:9443` Recv-Q=0 HTTPS 200; UFW 2053 Anywhere / 8443←RU; disk/OOM OK; `ru_hop_canary` + hop canary ok.
- **Sub xenoworth:** 200, 2×`vless://`, pbk/sid/sni/path/`stream-one` = secrets = live Reality (Direct private→public MATCH; RU sid/pbk MATCH). UUID на `xeno-direct-in` и `client-in`. Токен не ротировали.
- **E2E (параметры из sub):** с RU → Direct `:2053` → exit NL; RU loopback cascade → exit NL; с NL loopback Direct → exit NL. В access: `accepted … email: tg-7880252399` на обоих path во время пробы.
- **Пруф «не мёртв для юзера»:** в момент триажа ESTAB на Direct с IP юзера (Wi‑Fi + mobile); accepts Direct ~18:55–18:57 UTC (Cursor/TG); RU `client-in→nl-exit` с mobile ~18:55–18:56; другие tg активно на Direct.
- **Вывод:** не bad pbk/sid, не xray down, не UFW, не poisoned rewrite, не wipe. Симптом UX/Happ при живом туннеле — см. [common-issues.md](common-issues.md) «оба мёртвы + ESTAB/accepts». Сервер не меняли.
- Скрипты: `scripts/_triage_both_down.py`, `scripts/_final_status.py` (отчёты `*.out.txt` не коммитить).

## 2026-08-07 (триаж «все новые ссылки мёртвые»)

- Жалоба: новые sub «не поднимаются». Мандат: live triage NL+RU, без нового VPS.
- **SelfSteal `/9443`:** nginx, Recv-Q=0, HTTPS 200. **Hop:** local canary ok, `ru_hop_canary` ok, path `/182d55ce2f74acd1` + `stream-one` совпадает RU `nl-exit` ↔ NL `xeno-relay-in`. Smoke OK.
- **Subs:** `https://…:2080/sub/<token>/` → 200, 2×`vless://`, RU `sni=timeweb.cloud` / Direct Google, `mode=stream-one`. Токены/UUID **не** ротировали (в т.ч. xenoworth).
- **Клиенты:** все active UUID на RU `client-in` и NL `xeno-direct-in`. Новые (напр. asS0701) серверно E2E OK: RU cascade + Direct → exit NL (пробы с RU и NL→RU; NL→RU ≠ доказательство РФ).
- **Repair:** backup `bot.db` → `sync_all(rewrite_subs=True)` на NL. Расхождение UUID/шаблонов не найдено.
- **Вывод:** не повтор Aug‑5 (steal hang), не wipe sync, не битый provision «только для новых». Живой трафик в логах — в основном Direct; каскад принимает (canary + редкие RU). Симптом у клиентов совпадает с Happ (ping/failover / не удерживают 🇷🇺 RU) + медленный Direct — см. запись localize A–F и [common-issues.md](common-issues.md).
- **Клиентам:** VPN выкл → обновить **ту же** `https://` ссылку → вручную **🇷🇺 RU** на 60с (сайт, не только ping).

## 2026-08-07 (xenoworth: RU ping fail / Direct OK but slow — localize A–F)

- UX: **RU ping fail**, Direct connects; Direct **скорость плохая**. Не «Happ/сеть мёртвы целиком».
- Evidence (MSK ~18:24–18:57, тот же client IP на обоих path):
  - **RU:** короткие пачки `accepted … [client-in → nl-exit]` (18:24, 18:30) + **1:1** hop с entry IP на те же dest; pbk/sid/path/`stream-one` = live; Reality errors 0.
  - Затем только **Direct** (`xeno-direct-in`) непрерывно (18:50+), в т.ч. googlevideo.
  - Другой клиент держал RU ~1ч в том же окне — entry не «мёртв для всей РФ».
  - `path_stats` 5/15м: `accepts_ru=0`, Direct жив; `ru_hop_canary` ok; TCP RU→NL hop ~45 ms.
  - NL: load ~0.4, Direct inbound `stream-one`/Google SNI OK; stats после restart — заметный downlink на Direct у юзера (не «accepts без байт»), средний thruput слабый.
- **Слой:** **E** (Happ ping/failover не удерживает 🇷🇺 RU) при живом каскаде; **не A/C/D/F**. **B** не тотальный fail (handshake+hop были); soft-B (RF↔Timeweb sustain) — только если при ручном RU 60с accepts снова 0.
- **Direct slow:** не CPU/конфиг NL; вероятнее RF→NL:2053 shaping / RTT / XHTTP single-stream. Quick server fix нет.
- Действие: autoconnect off → вручную RU 60с (сайт, не только ping); **ту же sub**; UUID не ротировать; новый entry/retarget — только после нуля accepts в окне теста.
- Локальный снимок: `scripts/_localize_ru_xenoworth.py` (отчёты с IP не коммитить).

## 2026-08-07 (xenoworth «оба профиля мёртвы» после сброса Happ)

- Жалоба: свежий Happ + HTTPS sub — ни RU, ни NL Direct. Бан ice1477 **не** затронул UUID/token xenoworth.
- Проверки: sub 2×`vless://`, `mode=stream-one`, RU `sni=timeweb.cloud`, Direct `dl.google.com`; UUID на `client-in` + `xeno-direct-in` + 3x-ui; user `active=1`, не в `banned_users`.
- E2E с RU: Direct `:2053` и RU loopback → exit NL OK. Smoke 15:21 UTC OK.
- Живые accepts с IP юзера (~15:24–15:30 UTC) на **обоих** path (`client-in→nl-exit` и `xeno-direct-in`). Сервер не чинили — sub перезаписали без ротации UUID/token.
- Вывод: не wipe sync / не битый Reality; типичный клиентский кейс после reset Happ (см. common-issues / happ.md).

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
