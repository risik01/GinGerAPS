# Diario CamAPS

Add-on locale per Home Assistant: affianca ai dati di CamAPS FX (via Glooko,
Nightscout o xDrip+) un diario dei pasti con i tuoi alimenti, foto e stima
IA opzionale dei carboidrati, calendario, report PDF in formato AGP per il
diabetologo, insight settimanali e un'integrazione Home Assistant con sensori
e automazioni pronte. Tutto resta nel tuo Home Assistant: nessun servizio
cloud è obbligatorio.

**Non è un dispositivo medico e non dà indicazioni di terapia.** Registra,
visualizza e confronta i tuoi dati; le stime nutrizionali sono approssimative.
Non sostituisce il parere del tuo diabetologo.

## Funzioni principali

- **Giorno**: grafico di glicemia, boli, basale automatica e pasti; note del
  giorno.
- **Calendario**: vista mensile con indicatore del tempo nel range e foto del
  pasto principale per ogni giorno; ricerca nel diario ("quando ho mangiato
  la pizza?").
- **Pasti**: inserimento rapido con alimenti frequenti, ricerca (tabella
  Ciqual e Open Food Facts), codice a barre, foto con stima IA opzionale dei
  carboidrati (sempre una bozza da confermare, mai salvata da sola), ricette
  personali.
- **Andamento**: profilo AGP, insight settimanali in linguaggio semplice
  (variazione rispetto alla settimana prima, fasce orarie critiche, pasto con
  la risposta migliore/peggiore — tutte osservazioni descrittive, mai
  indicazioni di dose), report PDF per il diabetologo.
- **Impostazioni**: scelta della sorgente dati (Glooko, Nightscout, xDrip+)
  con test di connessione, database alimenti personale.
- **Integrazione Home Assistant** (`custom_components/camaps_diary`): sensori
  (glicemia, TIR, carboidrati, insulina, ultimo pasto/bolo) e servizi
  (`add_meal`, `add_note`, `refresh`, `generate_report`) per automazioni; 4
  blueprint pronte (bolo senza pasto registrato, ipoglicemia notturna,
  riepilogo settimanale, promemoria cambio set).

## Installazione

### Come add-on (consigliato)

1. Impostazioni → Componenti aggiuntivi → Negozio dei componenti aggiuntivi →
   menu ⋮ in alto a destra → Repository.
2. Aggiungi l'URL di questo repository.
3. Trova **Diario CamAPS** tra i componenti aggiuntivi, installalo, inserisci
   le credenziali della tua sorgente dati nella scheda Configurazione e
   avvia.

Installazione manuale (senza repository) e risoluzione problemi:
vedi [INSTALLAZIONE.md](INSTALLAZIONE.md).

### Integrazione Home Assistant (opzionale, in aggiunta all'add-on)

Dopo aver installato e avviato l'add-on, copia la cartella
`custom_components/camaps_diary` nel tuo `/config/custom_components/`,
riavvia Home Assistant, poi Impostazioni → Dispositivi e servizi → Aggiungi
integrazione → "Diario CamAPS". Le blueprint delle automazioni sono in
`blueprints/automation/camaps_diary/`: copiale in `/config/blueprints/automation/`
per trovarle in Impostazioni → Automazioni e scene → Blueprint.

### Passi dallo smartwatch o dal telefono (opzionale)

Il diario può mostrare i passi di oggi, letti dall'entità indicata nell'opzione
`passi_entity_id` dell'add-on (predefinita: `sensor.passi_giornalieri`).
Su iPhone, con un Xiaomi Smart Band o altri dispositivi che scrivono in Apple
Salute, il modo più semplice è **HealthSync**:

1. Installa l'integrazione [HealthSync](https://github.com/mannotfood/healthsync)
   da HACS (oppure copia `custom_components/healthsync/` in
   `/config/custom_components/`) e riavvia Home Assistant.
2. Impostazioni → Dispositivi e servizi → Aggiungi integrazione → HealthSync.
   Scegli un nome e, se Home Assistant è raggiungibile da internet, un
   **segreto condiviso** (obbligatorio nell'app). La notifica mostra l'URL del
   webhook; con Nabu Casa viene creato un cloudhook raggiungibile anche fuori
   casa.
3. Sull'iPhone installa
   [HealthSync for Home Assistant](https://apps.apple.com/us/app/healthsync-for-home-assistant/id6794884113),
   concedi i permessi di lettura a Salute, poi Impostazioni → Home Assistant:
   incolla l'URL e il segreto e tocca **Test Connection**. La sincronizzazione
   automatica in background richiede un acquisto una tantum.
4. Crea un sensore modello (Impostazioni → Dispositivi e servizi → Aiutanti →
   Modello → Sensore) chiamato **Passi giornalieri**, con unità `passi` e
   classe di stato `total_increasing`, e stato:
   `{{ states('sensor.healthsync_NOME_steps_today') | int(0) }}`
   (sostituisci `NOME` con il nome scelto al punto 2). Questo sensore ha le
   statistiche a lungo termine e sopravvive ai riavvii.

## Provare senza un vero account Glooko/Nightscout

Imposta l'opzione `modalita_demo: true` nella configurazione dell'add-on:
genera circa 30 giorni di dati sintetici (glicemie, boli, basale, pasti) così
puoi esplorare l'interfaccia senza collegare dati reali. Da disattivare prima
di usare l'add-on con i tuoi dati veri (i dati demo e quelli reali non si
mescolano, ma è pensata solo per provare l'app).

## Licenza

MIT, vedi [LICENSE](LICENSE). Font Atkinson Hyperlegible e Space Grotesk:
licenza SIL Open Font (file in `app/camaps_diary/static/fonts`). Dati
alimentari: tabella Ciqual (ANSES, dati aperti) e Open Food Facts (licenza
ODbL).

---

## English

Local Home Assistant add-on: pairs CamAPS FX closed-loop data (via Glooko,
Nightscout, or xDrip+) with a personal meal diary — photos with optional
AI carb estimation (always a draft you confirm, never auto-saved), a
calendar, an AGP-format PDF report for your diabetes clinic, deterministic
weekly insights in plain language, and a Home Assistant integration with
sensors and ready-made automation blueprints. Everything stays on your own
Home Assistant instance; no cloud service is required.

**This is not a medical device and gives no treatment advice.** It only
records, displays, and compares your data; nutritional estimates are
approximate. It does not replace guidance from your diabetes care team.

### Installing

Add it as an add-on repository (Settings → Add-ons → Add-on Store → ⋮ menu →
Repositories, paste this repository's URL), install **Diario CamAPS**, fill
in your data source credentials under Configuration, and start it. See
[INSTALLAZIONE.md](INSTALLAZIONE.md) (Italian) for manual installation and
troubleshooting steps.

Optional step counts (iPhone + HealthSync): see "Passi dallo smartwatch o dal telefono" above.

Set `modalita_demo: true` in the add-on configuration to seed about 30 days
of synthetic data and try the UI without a real Glooko/Nightscout account.

Optionally add the `custom_components/camaps_diary` Home Assistant
integration for sensors and automation blueprints.

Licensed under MIT — see [LICENSE](LICENSE).
