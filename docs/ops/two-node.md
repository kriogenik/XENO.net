# Две ноды (каскад)

> Для операторов и self-host. Клиентам XENO не требуется — см. [../README.md](../README.md).

Минимальный рабочий контур: **entry + exit**.

## Схема

1. Клиент → entry `:443` (Reality + XHTTP).  
2. Entry: RU-сети direct, остальное → hop на exit.  
3. Запасной профиль: клиент → exit direct-порт.  
4. Бот и HTTP-подписка — обычно на exit.

## Деплой (оператор)

```bash
cp inventory/hosts.env inventory/hosts.local.env  # заполнить
# secrets/nl-access.env, secrets/ru-access.env, secrets/bot.env, …
python scripts/deploy_two_node.py
python scripts/deploy_bot.py
python scripts/verify_cascade_xhttp.py
```

## Жёсткие правила

- Не `ufw --force reset` на exit с чужими сервисами.  
- Не трогать чужие inbound’ы панели.  
- Не коммитить `hosts.local.env` и `secrets/*`.

См. также [architecture.md](architecture.md), [nl-coexist.md](nl-coexist.md), [runbook.md](runbook.md).
