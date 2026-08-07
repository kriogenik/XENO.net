# Эксплуатация (не для клиентов)

Этот каталог — для людей, которые поднимают **свой** контур по шаблонам репозитория,  
или для внутренней работы операторов XENO.

Если вы просто пользуетесь [@xenonet_bot](https://t.me/xenonet_bot) — этот раздел вам не нужен.  
См. [документацию для людей](../README.md).

| Файл | О чём |
|------|--------|
| [architecture.md](architecture.md) | Схема каскада, плоскости |
| [two-node.md](two-node.md) | Минимальный деплой entry+exit |
| [servers.md](servers.md) | Роли и порты по умолчанию |
| [bot.md](bot.md) | Деплой бота, sync, таймеры |
| [runbook.md](runbook.md) | Сбои и проверки |
| [common-issues.md](common-issues.md) | Типичные жалобы клиентов |
| [rebuild-plan.md](rebuild-plan.md) | Восстановление контура |
| [order-checklist.md](order-checklist.md) | Заказ VPS |
| [nl-coexist.md](nl-coexist.md) | Сосуществование на одной VPS |
| [ru-bridge.md](ru-bridge.md) | Entry-мост |
| [incident-cascade.md](incident-cascade.md) | «RU мёртв / Direct ок» — чеклист |
| [cascade-audit.md](cascade-audit.md) | Полный аудит + path_stats / ru_hop (когда зелёное врёт) |
| [observability.md](observability.md) | Логи, `events.jsonl`, digests, алерты |
| [principles.md](principles.md) | Инженерные принципы |
| [changelog.md](changelog.md) | Краткий ops-журнал |

Секреты и реальные IP — только локально (`secrets/`, `inventory/hosts.local.env`).
