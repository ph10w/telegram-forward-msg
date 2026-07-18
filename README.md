# Telegram Voice Forwarder

Dieses Tool überwacht eine oder mehrere Telegram-Gruppen und überträgt neue
Sprachnachrichten mit deinem persönlichen Telegram-Account in einen privaten
Kanal. Es verwendet Telegrams MTProto-API über Telethon; Audiodateien werden
nicht lokal heruntergeladen.

## Voraussetzungen

- Python 3.10 oder neuer
- Dein Account ist Mitglied der Quellgruppen.
- Dein Account darf im Zielkanal Nachrichten veröffentlichen.
- Zustimmung der betroffenen Gruppenmitglieder bzw. eine passende
  Rechtsgrundlage für die Weiterleitung

## Einrichtung

1. Öffne [my.telegram.org/apps](https://my.telegram.org/apps), registriere eine
   Anwendung und notiere `api_id` und `api_hash`.
2. Erstelle und aktiviere eine virtuelle Umgebung:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e .
   ```

3. Kopiere `.env.example` nach `.env` und trage die Zugangsdaten ein:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Melde dich an und zeige deine Dialog-IDs an:

   ```powershell
   python -m telegram_voice_forwarder list-chats
   ```

   Beim ersten Aufruf fragt Telegram nach Telefonnummer, Login-Code und – falls
   aktiviert – dem 2FA-Passwort. Danach liegt die Sitzung lokal unter
   `TELEGRAM_SESSION`. Behandle die erzeugte `.session`-Datei wie ein Passwort.

5. Trage Quell- und Ziel-ID in `.env` ein und starte das Monitoring:

   ```powershell
   python -m telegram_voice_forwarder run
   ```

Mehrere Quellen werden mit Kommas getrennt:

```dotenv
TELEGRAM_SOURCE_CHATS=-1001234567890,@weitere_gruppe
TELEGRAM_TARGET_CHAT=-1009876543210
```

Kurze Sprachnachrichten können über eine Mindestdauer in Sekunden ausgefiltert
werden. Dezimalwerte verwenden einen Punkt:

```dotenv
MIN_VOICE_DURATION_SECONDS=3.5
```

Im Beispiel werden Nachrichten unter 3,5 Sekunden ignoriert; Nachrichten mit
genau 3,5 Sekunden werden weitergeleitet. Der Standardwert `0` deaktiviert den
Filter.

Telegram-IDs von Supergruppen und Kanälen beginnen in der Regel mit `-100`.
Private Gruppen ohne öffentlichen Benutzernamen sollten über ihre numerische ID
konfiguriert werden.

## Verhalten bei Neustarts

Beim ersten Start werden standardmäßig die 100 neuesten Nachrichten jeder
Quelle geprüft. `INITIAL_SCAN_LIMIT=0` beginnt ohne historischen Import. Danach
merkt sich das Tool pro Gruppe die letzte geprüfte Nachrichten-ID. Bereits
weitergeleitete Nachrichten und fehlgeschlagene Versuche werden in SQLite
gespeichert; dadurch werden Doppelweiterleitungen weitgehend vermieden und
temporäre Fehler beim nächsten Start erneut versucht.

Um den Scan-Zustand zurückzusetzen und beim nächsten Start erneut die neuesten
`INITIAL_SCAN_LIMIT` Nachrichten zu prüfen:

```powershell
python -m telegram_voice_forwarder reset
```

Der Befehl löscht sowohl die Scan-Cursor als auch die Historie bereits
weitergeleiteter oder ignorierter Nachrichten. Dadurch können die beim nächsten
Scan gefundenen Nachrichten erneut weitergeleitet werden. Er verwendet den in
`STATE_DB` konfigurierten Pfad und benötigt weder Telegram-Zugangsdaten noch
eine Netzwerkverbindung. Stoppe das laufende Monitoring vor dem Zurücksetzen.

Falls die Quellgruppe die Telegram-Einstellung zum Schützen von Inhalten
aktiviert hat, verweigert Telegram die Weiterleitung. Das Tool protokolliert den
Fehler, umgeht diese Schutzfunktion aber bewusst nicht.

Voice-Notes werden als einzelne Nachricht mit dem ursprünglichen Text, dem
Anzeigenamen des ursprünglichen Autors, dem Originaldatum in lokaler Zeitzone
und einem klickbaren Link
`Ursprungsnachricht` im Zielkanal veröffentlicht. Wenn vorhanden, wird zusätzlich
der öffentliche `@username` angegeben. Bei anonymen Admin-Beiträgen verwendet
das Tool nach Möglichkeit die Telegram-Autorensignatur. Telegrams
Forward-API erlaubt keine zusätzliche Caption; deshalb verwendet das Tool die
bereits bei Telegram gespeicherte Medienreferenz als serverseitige Kopie. Es
findet weiterhin kein Download oder erneuter Upload der Audiodatei statt. Links
auf private Supergruppen funktionieren nur für Telegram-Nutzer, die Mitglied der
Quellgruppe sind. Runde Video-Nachrichten werden ohne Link normal weitergeleitet,
da Telegram für Video-Notes keine Caption unterstützt.

## Dauerbetrieb

Unter Linux kann der Prozess beispielsweise als systemd-Dienst laufen. Wichtig
ist, dass `.session`-Datei und SQLite-Datenbank auf einem persistenten,
zugriffsgeschützten Datenträger liegen. Ein normaler Prozess-Stopp mit `Ctrl+C`
schließt die Verbindung und die Datenbank sauber.

## Sicherheit und Telegram-Regeln

Die verwendete Session gewährt Zugriff auf den Telegram-Account. Sie darf nie
committet, geteilt oder in ein öffentliches Container-Image eingebaut werden.
Telegram weist außerdem darauf hin, dass Drittanbieter-Clients überwacht werden
und Missbrauch wie Spam zur Sperre führen kann. Verwende das Tool nur in
Gruppen, in denen du die Nachrichten weiterleiten darfst.
