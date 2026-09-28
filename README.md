# sl-tp-hebel-bot

Ein Telegram-Bot, der Stop-Loss und Take-Profit für Hebelprodukte wie Knock-outs, Turbos und Mini-Futures überwacht. Trade Republic bietet dafür keine SL/TP-Orders an. Der Bot fragt die Kurse regelmäßig bei onvista ab und schickt eine Telegram-Nachricht, sobald ein Level erreicht ist. **Verkaufen musst du selbst** in der Trade-Republic-App. Zwischen Alarm und Verkauf vergeht also immer etwas Zeit.

> **Kein Finanzrat, keine Gewähr.** Der Bot ersetzt keine echte Stop-Order: Bei Kurslücken, etwa über Nacht, kann der Kurs weit unter deinem SL liegen, bevor du reagierst. Wird die KO-Schwelle erreicht, ist das Produkt sofort wertlos. Alarme können ausbleiben, etwa bei Netzproblemen oder wenn die Kursquelle sich ändert. Verlass dich nicht allein darauf. Die Nutzung erfolgt auf eigenes Risiko, für Verluste wird keine Haftung übernommen.

## Was der Bot meldet

| Alarm | Wann |
|---|---|
| 🛑 Stop-Loss erreicht | Geldkurs ≤ SL. Danach Erinnerung alle `REMINDER_MINUTES`, bis `/remove` oder neue Level gesetzt sind |
| 🎯 Take-Profit erreicht | Geldkurs ≥ TP, Erinnerung wie beim SL |
| ⚠️ KO-Schwelle nah | Basiswert weniger als `KO_WARN_PCT` % von der KO-Schwelle entfernt |
| 💥 Knock-out | onvista meldet die Barriere als gerissen |
| ⏸ Kurs veraltet | während der Handelszeit länger als `STALE_MINUTES` kein neuer Kurs |
| 🔌 Kursabfrage gestört | 5 Abfragen in Folge fehlgeschlagen, SL/TP werden gerade **nicht** überwacht |
| ☀️ Bot läuft | werktags um `HEARTBEAT_TIME`. Fehlt die Nachricht, ist der Bot aus |

Fällt das Netz aus, während ein Alarm rausgehen soll, geht die Nachricht nicht verloren: Sie wandert in einen Postausgang in der Datenbank und wird bei jedem Prüflauf erneut versucht, bis sie zugestellt ist. Verspätete Nachrichten tragen einen Hinweis mit dem Alter. Nach 24 Stunden oder 60 vergeblichen Versuchen wird eine Nachricht verworfen. Wie viele Nachrichten warten, zeigt `/status`.

Ausgelöst wird auf den **Geldkurs (Bid)**, also den Kurs, zu dem du verkaufen kannst.

## Telegram-Befehle

```
/add ISIN [SL] [TP] [Einstieg]
```

- Ohne Angaben gilt: SL −20 %, TP +30 %, Einstieg = aktueller Briefkurs.
- Werte **mit %** beziehen sich auf den Einstiegskurs, Werte **ohne %** sind Kurse in Euro.
- Statt der ISIN geht auch die WKN.
- Mit `-` als Platzhalter bleibt der Standardwert: `/add FC7CVG - +50%`.

| Beispiel | Bedeutung |
|---|---|
| `/add DE000FC7CVG4` | Standardwerte, Einstieg = Briefkurs |
| `/add DE000FC7CVG4 -15% +40%` | SL 15 % unter, TP 40 % über dem Briefkurs |
| `/add FC7CVG 6,90 11,00 8,31` | absolute Kurse, Einstieg 8,31 € |
| `/add FC7CVG tp=50% einstieg=8,31` | Standard-SL, TP +50 %, eigener Einstieg |

Weitere Befehle:

- `/sl ISIN WERT`, `/tp ISIN WERT`: Level ändern, z. B. SL nachziehen. Ein bereits ausgelöster Alarm wird dabei zurückgesetzt.
- `/remove ISIN`: Überwachung beenden, z. B. nach dem Verkauf
- `/list`: alle Positionen mit Live-Kurs, Performance und KO-Abstand
- `/kurs ISIN`: Kurs abfragen, ohne die Position zu speichern
- `/status`: Zustand des Bots (letzter Prüflauf, Fehler)
- `/help`: Hilfe

Nur der Chat aus `TELEGRAM_CHAT_ID` darf Befehle geben. Nachrichten aus anderen Chats werden ignoriert.

## Einrichtung

1. **Telegram-Bot anlegen:** In Telegram @BotFather öffnen, `/newbot` ausführen und den Token notieren.
2. **Chat-ID ermitteln:** Dem neuen Bot eine beliebige Nachricht schicken, dann `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser öffnen. Die Chat-ID steht unter `message.chat.id`.
3. **Lokal installieren:**

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
   ```

   ```bash
   cp .env.example .env
   ```

   In `.env` dann `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` eintragen.
4. **Testen:**

   ```bash
   .venv/bin/python -m pytest -q
   ```

   ```bash
   PYTHONPATH=src .venv/bin/python -m hebelbot.main --selftest DE000FC7CVG4
   ```

   Der Selbsttest ruft einen Kurs bei onvista ab und schickt eine Testnachricht.
5. **Starten:**

   ```bash
   PYTHONPATH=src .venv/bin/python -m hebelbot.main
   ```

## Dauerbetrieb auf einem Server

Der Bot ist eine Dauerschleife und sollte auf einem Rechner laufen, der immer an ist, zum Beispiel einem kleinen Server, einer VM oder einem Raspberry Pi. Auf einem Laptop läuft er nur, solange dieser wach ist.

Für Alpine Linux liegt eine OpenRC-Service-Datei bei ([deploy/sl-tp-hebel-bot.openrc](deploy/sl-tp-hebel-bot.openrc)). Sie erwartet das Projekt unter `/home/trading-bot/sl-tp-hebel-bot` mit einer venv im Unterordner `.venv`, beides über die Variablen `BOT_USER`, `BOT_HOME` und `BOT_LOG` anpassbar. Installation:

```bash
doas install -m 0755 deploy/sl-tp-hebel-bot.openrc /etc/init.d/sl-tp-hebel-bot
```

```bash
doas rc-update add sl-tp-hebel-bot default && doas rc-service sl-tp-hebel-bot start
```

Für systemd oder Docker gibt es keine fertige Datei, der Startbefehl ist aber derselbe: `python -m hebelbot.main` mit `src/` im `PYTHONPATH`.

**Wichtig:** Es darf immer nur *eine* Instanz mit demselben Bot-Token laufen. Zwei Instanzen nehmen sich gegenseitig die Telegram-Befehle weg.

Alle Einstellungen sind in [.env.example](.env.example) beschrieben.

## Kursquelle

onvista (`api.onvista.de`) liefert Geld- und Briefkurs des Emittenten, KO-Schwelle, Bezugsverhältnis, Basiswert und den Knock-out-Status. Die Schnittstelle ist **inoffiziell und undokumentiert**. Sie kann sich ohne Vorwarnung ändern, und genau dafür gibt es den Alarm „Kursabfrage gestört“.

- onvista blockt den Standard-User-Agent von Python `requests` mit HTTP 429. Der Bot schickt deshalb einen Browser-User-Agent.
- Zwischen zwei Anfragen liegt mindestens 1 Sekunde. Nach einer 429-Antwort pausiert der Bot.
- Lang & Schwarz (ls-tc.de) findet Fremdemittenten-Produkte wie die von Société Générale nicht über seine Suche. Als Quelle taugt die Seite deshalb nicht.

Für die Einhaltung der onvista-Nutzungsbedingungen ist jeder selbst verantwortlich. Frag sparsam ab: Der Standardwert von 30 Sekunden je Position ist bewusst zurückhaltend gewählt. Die Dateien unter `tests/fixtures/` sind zwei echte API-Antworten von onvista (17.09.2026), die nur zum Testen des Parsers dienen.

## Aufbau

```
src/hebelbot/
  main.py      Einstiegspunkt, Hauptschleife (Prüflauf + Telegram-Long-Polling)
  monitor.py   SL/TP/KO-Prüfung, Erinnerungen, Veraltet-Warnung, Heartbeat
  commands.py  Telegram-Befehle
  onvista.py   Kursabfrage und Parsing (einzige Stelle mit onvista-Wissen)
  telegram.py  Bot-API-Client (sendMessage, getUpdates, setMyCommands)
  notify.py    Zustellung mit Postausgang, damit kein Alarm verloren geht
  store.py     SQLite (state/positions.db, inkl. Postausgang)
  levels.py    SL/TP-Parsing, deutsche Zahlenformate
  config.py    Einstellungen aus .env
```

## Lizenz

MIT, siehe [LICENSE](LICENSE).
