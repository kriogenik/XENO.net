# Инцидент: у всех «RU мёртв», Direct OK

> 3 ночи, без воды. Sacred (3x-ui / trading) не трогать.

## Симптомы

- Клиенты на профиле **🇷🇺 RU** не ходят; **NL Direct** работает.
- В логах entry есть accepts, hop `xeno-relay-hop` тихий **или** алерт `hop_reality` / `steal_https` / `cascade_split`.

## Цепочка проверок (по порядку)

```
entry (RU :443) → hop (NL :8443 Reality) → SelfSteal (:9443 nginx) → exit
```

### 1. SelfSteal `:9443`

```bash
systemctl is-active xeno-steal-nl
curl -sk --max-time 5 -o /dev/null -w '%{http_code}\n' https://127.0.0.1:9443/
ss -lntp | grep 9443
```

Ожидание: unit `active`, HTTP `200`, listener = **nginx**.  
Если нет:

```bash
systemctl restart xeno-steal-nl
# если порт залип:
fuser -k 9443/tcp; systemctl start xeno-steal-nl
```

### 2. Hop Reality canary `:8443`

```bash
systemctl is-active xeno-relay
cat /var/log/xeno/hop_canary.json
systemctl start xenonet-hop-watch.service
cat /var/log/xeno/hop_canary.json
```

Ожидание: `"ok": true`, `detail=canary_ok`.  
Canary: локальный VLESS+Reality на `:8443` → HTTP `127.0.0.1:19443` (узкое исключение из `geoip:private` block). Чужие сайты не трогает.

> **Ложный FAIL:** если canary ходит на любой другой `127.0.0.0/8` без правила `:19443`→direct, hop пишет `→ block` при **живом** каскаде для клиентов. Не рестартить relay «на всякий» — сначала `hop_canary.json` / routing.

Если steal ↑, а canary ↓:

```bash
systemctl restart xeno-relay
journalctl -u xeno-relay -n 50 --no-pager
tail -n 30 /var/log/xeno/nl-relay-error.log
```

### 3. Entry (RU)

```bash
# с NL, через ru-ssh.env
systemctl is-active xray   # на RU
# TCP RU:443 снаружи / smoke tcp_ru_443
```

### 4. Клиент

После починки hop — в Happ снова выбрать **🇷🇺 RU**, не Direct.  
Не ротировать UUID/sub без нужды.

## Алерты (кто о чём)

| Ключ | Смысл | Первым смотреть |
|------|--------|-----------------|
| `unit_xeno_steal` / `steal_https` | dest Reality мёртв | п.1 |
| `unit_xeno_relay` | hop unit down | п.2 |
| `hop_reality` | живой canary :8443 FAIL ×2+ | п.2 (steal уже зелёный) |
| `cascade_split` | логи: RU accepts есть, hop quiet | п.1–2; canary мог отставать |
| `hop_stale` | canary OK, клиентов давно нет | не паника; LTE/юзеры |
| `smoke_fail` | hourly smoke ×2 | `/var/log/xeno/digests/smoke/latest.md` |

## Не делать

- `ufw --force reset` на NL  
- Править чужие inbound’ы 3x-ui  
- Логировать destinations клиентов  
- Mute алертов вместо починки  

См. также: [common-issues.md](common-issues.md), [observability.md](observability.md), [principles.md](principles.md).
