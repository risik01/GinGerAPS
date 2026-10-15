# Installare Diario CamAPS su Home Assistant OS

L'add-on è "locale": Home Assistant lo costruisce dai file di questa cartella,
senza scaricarlo da un repository. Servono tre cose: copiare la cartella,
installare dall'interfaccia, configurare.

## 1. Copiare la cartella

La cartella `camaps_diary`, quella che contiene `config.yaml`, deve finire in
`/addons/camaps_diary/`. Il risultato deve essere `/addons/camaps_diary/config.yaml`,
non `/addons/camaps_diary/camaps_diary/config.yaml`.

Scegli uno di questi modi:

- **Samba share** (add-on ufficiale): installalo, imposta utente e password
  nella sua configurazione e avvialo. Da Windows apri
  `\\homeassistant.local\addons`, oppure `\\<IP di Home Assistant>\addons`,
  e copia lì la cartella `camaps_diary`.
- **Terminal & SSH** (add-on ufficiale): la cartella è `/addons`. Copia lo zip
  e scompattalo lì con `unzip`.
- **Un agent dentro Home Assistant**: vedi il testo pronto più sotto.
  Funziona solo se l'agent vede la cartella `/addons`.

## 2. Installare

1. Apri Impostazioni, poi Componenti aggiuntivi. Nelle versioni più recenti
   la voce può chiamarsi App.
2. Apri il negozio, con il pulsante in basso a destra.
3. Dal menu ⋮ in alto a destra scegli "Controlla aggiornamenti", poi ricarica
   la pagina.
4. In fondo compare la sezione dei componenti locali con **Diario CamAPS**:
   aprilo e premi Installa. La prima installazione costruisce l'immagine e
   può richiedere 5–10 minuti su un Raspberry Pi.

Dal terminale, con la CLI `ha`, i passi equivalenti sono:

    ha store reload
    ha addons install local_camaps_diary

## 3. Configurare e avviare

1. Nella scheda **Configurazione** inserisci email e password di Glooko, cioè
   lo stesso account collegato in CamAPS (Menu, Condividi, Glooko). Salva.
2. Nella scheda **Informazioni** premi Avvia e attiva "Mostra nella barra
   laterale". "Avvio all'accensione" è già attivo.
3. Nella scheda **Registro** dopo un minuto devi trovare
   `in ascolto sulla porta 8099`, le righe della sincronizzazione (boli,
   basali, glicemie) e `Tabella Ciqual importata`.
4. Apri **Diario CamAPS** dalla barra laterale, anche nell'app Home Assistant
   sul telefono.

## Aggiornare a una nuova versione

Sostituisci i file in `/addons/camaps_diary/`, poi nel negozio usa
"Controlla aggiornamenti" e premi Aggiorna (oppure Ricostruisci) nella pagina
dell'add-on. I dati restano: stanno nella cartella dati dell'add-on, non in
questi file.

## Testo da dare all'agent

Copia e incolla, dopo avergli dato lo zip:

> Installa su questo Home Assistant OS l'add-on locale contenuto nello zip
> `diario-camaps-addon-0.2.0.zip`.
> 1. Controlla di poter scrivere in `/addons`. Se la cartella non esiste o non
>    è scrivibile, fermati e dimmelo.
> 2. Scompatta lo zip in modo che esista `/addons/camaps_diary/config.yaml`,
>    senza una cartella `camaps_diary` doppia. Se `/addons/camaps_diary` esiste
>    già, sostituisci i file. Non toccare nient'altro in `/addons`.
> 3. Se hai la CLI `ha` o l'API del Supervisor, ricarica lo store
>    (`ha store reload`) e installa `local_camaps_diary`. Se non puoi,
>    dimmelo e lo faccio io dall'interfaccia.
> 4. Non chiedermi la password di Glooko: la inserisco io nella scheda
>    Configurazione dell'add-on.
> 5. Dopo che l'ho avviato, leggi il registro dell'add-on
>    (`ha addons logs local_camaps_diary`) e dimmi se compaiono
>    "in ascolto sulla porta 8099" e "Tabella Ciqual importata", oppure quali
>    errori ci sono.

## Se qualcosa non va

- **Diario CamAPS non compare tra i locali**: controlla che il percorso sia
  `/addons/camaps_diary/config.yaml`, poi ripeti "Controlla aggiornamenti" e
  ricarica la pagina.
- **La build fallisce**: copia le ultime righe del registro di sistema
  (Impostazioni, Sistema, Registri, Supervisor).
- **"Credenziali Glooko rifiutate"**: prova le stesse credenziali su
  eu.my.glooko.com.
- **Tabella Ciqual non scaricata**: dalla pagina Stato del diario usa
  "Scarica la tabella alimenti".
