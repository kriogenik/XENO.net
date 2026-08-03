# Каталог секретов
#
# Сюда кладите локальные env-файлы. В git попадает только этот README.
#
# Обычно:
# - bot.env          — BOT_TOKEN, ADMIN_IDS, XUI_*, Reality-ключи каскада,
#                      SUB_PUBLIC_BASE / SUB_TLS_* (HTTPS подписки для Happ, любая ОС),
#                      опционально DONATE_USDT_TRC20 / DONATE_TON / DONATE_BTC
#                      (публичные адреса → Поддержка → Поддержать; без значений кнопка скрыта)
# - bridge.env       — ключи Reality для клиента / hop
# - nl-access.env    — SSH NL для деплоя
# - ru-access.env    — SSH RU для деплоя
# - deploy_key       — ключ для git push (опционально)
#
# HTTPS подписки (обязательно): python scripts/enable_sub_https.py (LE на NL_DOMAIN, :80 ACME, TLS :2080).
#
# Донаты (опционально, в bot.env):
#   DONATE_USDT_TRC20=<TRC20-адрес>
#   DONATE_TON=<TON-адрес>
#   DONATE_BTC=<BTC-адрес>
# Пустые / отсутствующие ключи — экран «Поддержать» не показывается.
# Адреса для донатов публичны по сути; в git их всё равно не коммитим — только env на сервере.
#
# Никогда не коммитьте пароли, API-токены, приватные Reality-ключи,
# живые UUID клиентов и URL подписок.
#
# Публичный репозиторий намеренно не содержит прод-инвентаря:
# реальные IP — в inventory/hosts.local.env (тоже gitignore).
