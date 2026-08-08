# Инженерные принципы XENO.net

> Для операторов и тех, кто развивает продукт. Клиентам не обязательно.

## Политика (нерушимо)

1. **Приватность > удобство метрик.** Не логируем destinations (куда ходил клиент), не MITM, не продаём данные.  
2. **Secrets вне git.** `secrets/*` (кроме README), `hosts.local.env` — только локально.  
3. **Sacred.** Чужие inbound’ы 3x-ui и sibling-сервисы на NL не трогаем.

## Стабильность (как строим)

1. **Каскад RU→NL — критичный путь.** SelfSteal (`:9443`) и hop (`:8443`) важнее косметики бота.  
2. **SelfSteal = nginx**, не Python `ThreadingTCPServer` (клинит под Reality-пробами).  
3. **Клиентский XHTTP mode = `stream-one`**, не `auto`. У Happ / части сборок Xray `mode=auto` + Reality даёт «TLS ок, трафика нет» или unexpected response version. То же для **hop inbound** (`xeno-relay-in`) и RU `nl-exit`: держать `stream-one` в паре. Проверка e2e с NL→RU с `auto` **не** доказывает, что Happ у пользователя жив.  
4. **Entry `:443` = только xray Reality.** Никакого nginx stream / SNI-router / docker LE на публичном `:443` entry и никакого увода `client-in` на `:10443`. SelfSteal — отдельно (`127.0.0.1:9443`).  
5. **BRIDGE Reality donor** — из `secrets` / шаблонов (`BRIDGE_REALITY_*`), согласованный с sub URI. Для текущего XENO-деплоя канон = **Google** (`dl.google.com`), как в рабочем пике Aug 2–4. Ad-hoc retarget (timeweb и т.п.) без полного `sync_all(rewrite_subs=True)` и без доводки всех поверхностей — запрещён.  
6. **Local hop canary ≠ клиентский каскад.** Loopback canary на NL не доказывает RU→NL; `cascade_split` / `hop_stale` смотрят только RU-sourced hop. Аудит: [cascade-audit.md](cascade-audit.md).  
7. **Алерты без спама:** open → remind(6ч) → recover; стабильные fingerprints.  
8. **Наблюдаемость файлами + Telegram**, не обязательный Prometheus. Журнал: `/var/log/xeno/events.jsonl` — см. [observability.md](observability.md).  
9. **Чиним корень**, не mute. Watchdog / canary / unit Restart=always — да; «выключить алерт» — нет.  
10. При конфликте changelog↔architecture/principles/templates — **побеждают architecture + principles + templates**.

## Продукт

1. Документация для людей снаружи — спокойный тон; ops — в `docs/ops/`.  
2. Подписка всегда `https://`, plain `vless://`, без autoconnect на Direct.  
3. Магазины РФ — bypass на entry; клиент на профиле **🇷🇺 RU**.  
4. Малые частые изменения; публичная история — осознанный снимок, без секретов.

## Когда сомневаешься

Сначала: smoke + `events.jsonl` + digest.  
Потом: правка причины.  
Потом: док в `ops/` на русском.
