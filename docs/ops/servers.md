# Серверы (роли)

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

| Роль | Зачем |
|------|--------|
| Exit | Выход, hop inbound, бот, подписка |
| Entry | Приём клиентов под DPI, split, hop outbound |
| Whitelist (опционально) | Отложено |

Порты по умолчанию в шаблонах: клиент `443`, hop `8443`, direct `2053`, sub `2080`, HY2 `8444/udp` (может быть parked).

Firewall: hop по возможности только с IP entry.

Реальные адреса — в `inventory/hosts.local.env`, не в git.
