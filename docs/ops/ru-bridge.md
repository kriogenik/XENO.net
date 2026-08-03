# Заметки: entry-мост (RU)

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Entry-нода принимает клиентов (VLESS + Reality + XHTTP), режет российский трафик в direct и остальное отправляет на exit через внутренний hop.

## Роль

- DNS/hostname entry задаёте сами (`RU_DOMAIN` в локальном inventory).  
- Reality: TLS-донор под вашу сеть (SNI/dest в env).  
- Outbound hop → IP/порт exit из `hosts.local.env` (`NL_EXIT_IP`, `RELAY_PORT`).  
- Продуктовая HTTP-подписка обычно на exit (`xenonet-sub`); временный bootstrap-sub на entry — опционален.

## Не публикуем

Живые IP, сертификатные пути прод-хоста и пароли SSH — только локально.

## Проверки

См. [runbook.md](runbook.md) и `scripts/verify_cascade_xhttp.py` (читает локальный inventory + secrets).
