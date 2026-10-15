# Diario CamAPS

Raccoglie i dati che CamAPS FX carica su Glooko (glicemie del sensore, boli,
insulina automatica, modalità del sistema) e li affianca a un diario dei pasti
con i tuoi alimenti. Si apre dalla barra laterale di Home Assistant, anche
nell'app sul telefono.

## Installazione su Home Assistant OS

I passi dettagliati, anche da far eseguire a un agent, sono in
`INSTALLAZIONE.md` in questa cartella. In breve:

1. Copia la cartella `camaps_diary` dentro la cartella `addons` di Home
   Assistant, cioè in `/addons/camaps_diary/`.
2. Impostazioni, Componenti aggiuntivi, negozio dei componenti aggiuntivi,
   menu in alto a destra, "Controlla aggiornamenti". Diario CamAPS compare
   tra i componenti locali.
3. Installa, inserisci email e password di Glooko nella scheda
   Configurazione, avvia e attiva "Mostra nella barra laterale".

## Cosa fa

- **Sincronizzazione**: all'avvio e poi ogni 30 minuti (configurabile). La
  prima volta recupera gli ultimi 30 giorni. CamAPS carica su Glooko a
  intervalli di ore: in cima al giorno trovi l'ora degli ultimi dati ricevuti.
- **Voci di CamAPS**: l'app distingue
  - i boli del calcolatore;
  - i **pasti a lenta digestione**: CamAPS li registra come 6 quote ogni
    30 minuti, ma non sono boli. L'insulina per quella parte arriva come
    automatica, solo se la glicemia sale;
  - i **pasti o spuntini senza bolo** inseriti con Aggiungi pasto;
  - i **trattamenti dell'ipoglicemia**. Glooko non li distingue dagli
    spuntini: l'app li riconosce da una glicemia sotto 80 mg/dL in quel
    momento e li indica come probabili.
- **Giorno**: grafico della glicemia con la fascia del range, i boli (in alto,
  triangoli), le voci senza insulina (punti) e i pasti del diario (barre).
  Sotto il grafico, la striscia delle modalità di CamAPS.
- **Pasti**: scegli tipo e ora e aggiungi gli alimenti con i grammi. Il pasto
  si collega da solo alle voci di CamAPS vicine, comprese le quote di un pasto
  a lenta digestione. Per ogni pasto vedi:
  - carboidrati mangiati e inseriti in CamAPS;
  - insulina del bolo e insulina automatica (3 ore, o fino a un'ora dopo
    l'ultima quota lenta);
  - glicemia prima del pasto, picco e valore a 2 ore.

  Il tipo "Correzione ipo" serve per annotare cosa hai preso durante
  un'ipoglicemia: non entra nei rapporti tra grammi e unità.
- **Alimenti**: il tuo elenco personale, con carboidrati per 100 g e porzione
  abituale. Ci sono tre fonti:
  - la **tabella Ciqual** dell'ANSES: circa 3.000 alimenti generici, cioè
    pasta cruda e cotta, integrale, riso, pane, frutta, piatti pronti. Viene
    scaricata una volta sola al primo avvio. I nomi sono in inglese e
    francese, ma puoi cercare in italiano ("pasta integrale cotta");
  - **Open Food Facts** per i prodotti confezionati;
  - il **codice a barre**: nell'app di Home Assistant la fotocamera di solito
    non è disponibile, quindi scrivi il numero.

  Con "Salva come piatto" trasformi un pasto in un alimento riutilizzabile,
  per esempio pasta, passata e olio diventano "Pasta al pomodoro".
- **Andamento**: medie e tabella degli ultimi 7, 14 o 30 giorni.

## Dati e sicurezza

- Tutto resta su Home Assistant, nel file `diario.db` dentro i dati
  dell'add-on, ed entra nei backup. Durante un backup l'add-on si ferma per
  qualche secondo, così il database viene copiato integro.
- L'interfaccia passa dall'accesso di Home Assistant: non è esposta in rete.
- Su Glooko l'add-on fa solo letture, usando l'interfaccia interna di Glooko,
  che non è documentata. Se Glooko la cambia, la sincronizzazione può fermarsi
  finché non si aggiorna l'add-on.

## Fonti dei dati alimentari

- Ciqual: ANSES, Table de composition nutritionnelle des aliments Ciqual,
  dati aperti. I carboidrati sono quelli disponibili, fibra esclusa, come
  nelle etichette europee.
- Open Food Facts: licenza ODbL.

## Limiti

- I numeri servono a rivedere l'andamento, anche con il diabetologo. Non sono
  indicazioni di dose.
