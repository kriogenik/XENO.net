# Runbook (оператор)

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Краткие действия при сбоях. Без публикации прод-IP.

## Быстрый чеклист (5 мин)

1. `systemctl is-active` на unit’ы продукта (relay, bot, sub, xray на entry).  
2. Свежий smoke / digest: `python scripts/show_digest.py`.  
3. Порты: entry 443, hop, direct, sub — слушают ли.  
4. Не трогать чужие сервисы и inbound’ы панели.  
5. Клиентам: обновить подписку в Happ; при смене Reality — удалить старый профиль.

## Типичные симптомы

| Симптом | Куда смотреть |
|---------|----------------|
| Нет accepts на entry | xray entry, Reality SNI/dest, UFW 443 |
| Entry жив, hop тихий | relay unit, UFW hop только с entry IP |
| SNI mismatch spike | устаревшие профили Happ |
| Диск / unit down | алерты админам, journalctl |
| «RU мёртв» у части, Direct ок | [common-issues.md](common-issues.md) — чаще Happ autoconnect / выбор Direct |
| Hop accepts = 0, entry accepts есть | SelfSteal NL :9443 (nginx) down → `systemctl restart xeno-steal-nl`; см. common-issues / `cascade_split` |
| iOS тип контента / отказано | plain sub, без Content-Disposition — см. common-issues |

Подробная матрица клиентских кейсов: **[common-issues.md](common-issues.md)**.

После смены routing: `python scripts/repair_subscriptions.py`.

## Sacred

Бот и скрипты **не** должны менять чужие inbound’ы панели и сторонние деревья на диске.  
ID защищённых inbound’ов — в `XUI_SACRED_INBOUND_IDS` (env).

## Алерты админам

Collect каждые 5 мин и smoke каждый час вызывают `maybe_send_alerts`.

- **Open** — один раз при появлении проблемы (стабильный fingerprint).  
- **Remind** — не чаще чем раз в **6 ч**, пока проблема жива.  
- **Recover** — одно сообщение `XENO recover · <key>`, когда снова OK.  

Типичный спам раньше: `sni_spike` с fingerprint `sni:{count}` — счётчик рос → кулдаун сбрасывался каждые 5 мин. Сейчас fingerprint = `day:YYYY-MM-DD`.

SelfSteal: таймер `xenonet-steal-watch` (2 мин) рестартит `xeno-steal-nl`, если `https://127.0.0.1:9443/` не отвечает.
