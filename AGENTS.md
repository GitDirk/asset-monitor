# AGENTS.md

Anleitung für AI-Coding-Agents in diesem Repo.

## Projektüberblick

Telegram-Bot, der SL/TP-Level für Hebelprodukte (Knock-outs) überwacht und alarmiert. Er handelt **nicht** selbst: Der Nutzer verkauft manuell in Trade Republic. Die Kurse kommen über die inoffizielle onvista-API. Details stehen in der README.

Sprache im Repo: Code-Kommentare, Log-Meldungen, Telegram-Texte und Doku sind **Deutsch**.

## Wichtige Regeln

- Ein **fehlender Alarm ist der schlimmste Fehler.** Fehler bei der Kursabfrage dürfen nie still verschluckt werden: `QuoteError` führt über `Monitor` zum Alarm „Kursabfrage gestört“. Die Hauptschleife in `main.py` darf nie abbrechen.
- Alarme laufen über `Notifier` (`notify.py`), nie direkt über `TelegramClient.send`. Nur so landen sie bei einer Netzstörung im Postausgang. Die Hauptschleife leert ihn vor jedem Prüflauf.
- onvista-Wissen (URLs, JSON-Felder, User-Agent, 429-Handling) gehört ausschließlich in `src/hebelbot/onvista.py`.
- Test-Fixtures in `tests/fixtures/` sind echte onvista-Antworten vom 17.09.2026. Wenn onvista das Format ändert, eine neue Antwort speichern und den Parser anpassen.
- Der Code bleibt kompatibel mit Python 3.9 (lokal 3.9, VM 3.12).
- Der Bot-Token darf nie geloggt werden. `telegram.py` gibt deshalb keine Exception-Texte mit URL weiter, und urllib3 läuft auf WARNING.

## Befehle

```bash
.venv/bin/python -m pytest -q
```

```bash
PYTHONPATH=src .venv/bin/python -m hebelbot.main --selftest DE000FC7CVG4
```

```bash
PYTHONPATH=src .venv/bin/python -m hebelbot.main
```

## Deployment

Das Deployment auf die Alpine-VM beschreibt der Skill `.claude/skills/deploy-vm/SKILL.md`. Dort laufen mehrere andere Bots: strikte Isolation beachten.
