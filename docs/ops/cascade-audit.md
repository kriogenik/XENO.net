# Аудит каскада (операторы)

> Когда «у всех RU мёртв», а smoke/canary зелёные — не верь TCP и local canary.

## Почему зелёное врёт

1. `accepted` на entry ≠ рабочий UX.  
2. Local hop canary (`127.0.0.1:8443`) обновляет hop `last_seen` и раньше **маскировал** `cascade_split`.  
3. Smoke с NL на RU:443 ≠ Happ из РФ.

## One-shot

```bash
# с рабочей станции (secrets + inventory локально)
python scripts/cascade_audit.py
# отчёт: scripts/_cascade_audit_report.json (не коммитить)
```

Фазы: snapshot → population (`hop_ru_sourced` vs canary) → RU/NL/ключи → RU→hop probe.

## Постоянная телеметрия

| Артефакт | Смысл |
|----------|--------|
| `path_stats.json` | популяция 5/15/60м/24ч |
| `hop_canary.json` | local NL Reality |
| `ru_hop_canary.json` | path с entry |
| алерты `cascade_split` / `cascade_ratio_break` / `hop_path_ru` | без маски canary |

Таймеры: `xenonet-diag-collect` (path_stats), `xenonet-hop-watch`, `xenonet-ru-hop-watch`.

## Remediation

Строго по Fail фазы — см. [incident-cascade.md](incident-cascade.md).  
Retarget SNI/entry — только после явного «делаем».
