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

## Sacred

Бот и скрипты **не** должны менять чужие inbound’ы панели и сторонние деревья на диске.  
ID защищённых inbound’ов — в `XUI_SACRED_INBOUND_IDS` (env).

## Дайджесты

Таймеры collect / smoke / digest пишут файлы на сервере (см. [bot.md](bot.md)).  
Алерты — только `ADMIN_IDS`.
