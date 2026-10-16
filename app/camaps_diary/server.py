"""Server dell'add-on: interfaccia web + API JSON, e sync periodica da Glooko.

In Home Assistant l'interfaccia passa da Ingress (autenticazione di HA):
le richieste arrivano solo dal proxy 172.30.32.2. Fuori da HA, per prove,
si avvia con  DIARIO_DATA=./dati DIARIO_APERTO=1 python -m camaps_diary.server
"""

import json
import logging
import mimetypes
import os
import signal
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, g, jsonify, request, send_from_directory

from . import __version__
from . import attivita
from . import ciqual
from . import demo_data
from . import foto
from . import insights
from . import off_import
from . import report
from .analysis import (agp_profile, calendar_month, day_payload, food_occurrences, ha_snapshot,
                       period_summary, steps_summary)
from .diary import (MEAL_TYPES, NOTE_KINDS, DiaryError, create_food, create_note, delete_food,
                    delete_meal, delete_note, diet_summary, food_by_barcode, frequent_foods, get_food, get_meal,
                    notes_for_day, save_meal, save_recipe, search_diary, search_foods, update_food)
from .foto import VisionError
from .datasource import DataSourceError
from .glooko import GlookoClient, GlookoError
from .nightscout_source import NightscoutDataSource
from .off import OffError, OpenFoodFacts
from .store import Store
from .sync import sync, sync_generic
from .xdrip_source import XDripDataSource
from .timeutil import DEFAULT_TZ, now_local

DATA_DIR = Path(os.environ.get("DIARIO_DATA", "/data"))
OPTIONS_FILE = DATA_DIR / "options.json"
PHOTOS_DIR = DATA_DIR / "foto"
REPORTS_DIR = DATA_DIR / "report"
STATIC = Path(__file__).with_name("static")
INGRESS_IPS = {
    "172.30.32.2",  # proxy di Ingress
    "172.30.32.1",  # gateway della rete hassio: Home Assistant Core usa rete "host" e passa di qui
    "127.0.0.1",
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")

log = logging.getLogger("diario")

DEFAULT_OPTIONS = {
    "glooko_email": "",
    "glooko_password": "",
    "glooko_server": "eu",
    "sync_minuti": 30,
    "giorni_iniziali": 30,
    "off_contatto": "",
    "anthropic_api_key": "",
    "ia_modello": "claude-sonnet-5",
    "passi_entity_id": "sensor.passi_giornalieri",
    "modalita_demo": False,
}


def load_options() -> dict:
    opts = dict(DEFAULT_OPTIONS)
    if OPTIONS_FILE.exists():
        opts.update(json.loads(OPTIONS_FILE.read_text(encoding="utf-8")))
    for key in ("glooko_email", "glooko_password"):
        if os.environ.get(key.upper()):
            opts[key] = os.environ[key.upper()]
    return opts


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Syncer(threading.Thread):
    """Sincronizza all'avvio e poi ogni sync_minuti; trigger() anticipa la prossima."""

    def __init__(self, db_path: Path, opts: dict):
        super().__init__(daemon=True, name="glooko-sync")
        self.db_path = db_path
        self.opts = opts
        self.wake = threading.Event()
        self.busy = threading.Event()

    def trigger(self) -> bool:
        if self.busy.is_set():
            return False
        self.wake.set()
        return True

    def run_once(self) -> None:
        store = Store(self.db_path)
        try:
            sorgente = store.get("sorgente_dati", "glooko")
            if sorgente == "nightscout":
                self._run_generic(store, "nightscout_url",
                                  "Inserisci l'indirizzo di Nightscout in Impostazioni.",
                                  lambda url: NightscoutDataSource(url, store.get("nightscout_token", ""),
                                                                    tz=store.get("timezone", DEFAULT_TZ)))
            elif sorgente == "xdrip":
                self._run_generic(store, "xdrip_url",
                                  "Inserisci l'indirizzo del servizio web di xDrip+ in Impostazioni.",
                                  lambda url: XDripDataSource(url, tz=store.get("timezone", DEFAULT_TZ)))
            elif not (self.opts.get("glooko_email") and self.opts.get("glooko_password")):
                store.set("sync_stato", "non_configurato")
                store.set("sync_errore", "Inserisci email e password di Glooko nella configurazione dell'add-on.")
            else:
                store.set("sync_stato", "in_corso")
                client = GlookoClient(server=self.opts.get("glooko_server", "eu"),
                                      device_file=DATA_DIR / ".glooko_device.json",
                                      api_base=os.environ.get("DIARIO_GLOOKO_BASE"),
                                      web_origin=os.environ.get("DIARIO_GLOOKO_BASE"))
                client.login(self.opts["glooko_email"], self.opts["glooko_password"])
                report = sync(client, store, backfill_days=int(self.opts.get("giorni_iniziali", 30)),
                              log=log.info)
                store.set("sync_stato", "ok")
                store.set("sync_errore", "")
                store.set("sync_esito", json.dumps(report))
        except (GlookoError, DataSourceError) as exc:
            log.warning("Sync non riuscita: %s", exc)
            store.set("sync_stato", "errore")
            store.set("sync_errore", str(exc))
        except Exception as exc:  # noqa: BLE001 - lo stato deve sempre essere aggiornato
            log.exception("Errore inatteso durante la sync")
            store.set("sync_stato", "errore")
            store.set("sync_errore", f"Errore inatteso: {exc}")
        finally:
            store.set("sync_tentativo_utc", utc_now())
            self._sync_steps(store)
            store.close()

    def _run_generic(self, store: Store, url_key: str, messaggio_non_configurato: str, crea_sorgente) -> None:
        """Sincronizzazione per Nightscout/xDrip+: fetch a intervallo diretto
        (sync_generic), non il cursore incrementale usato per Glooko."""
        url = (store.get(url_key) or "").strip()
        if not url:
            store.set("sync_stato", "non_configurato")
            store.set("sync_errore", messaggio_non_configurato)
            return
        store.set("sync_stato", "in_corso")
        source = crea_sorgente(url)
        report = sync_generic(source, store, backfill_days=int(self.opts.get("giorni_iniziali", 30)), log=log.info)
        store.set("sync_stato", "ok")
        store.set("sync_errore", "")
        store.set("sync_esito", json.dumps(report))

    def _sync_steps(self, store: Store) -> None:
        """Passi di oggi da Home Assistant: errore qui non deve mai bloccare la sync Glooko."""
        entity_id = (self.opts.get("passi_entity_id") or "").strip()
        if not entity_id:
            return
        try:
            tz = store.get("timezone", DEFAULT_TZ)
            attivita.sync_today(store, os.environ.get("SUPERVISOR_TOKEN", ""), entity_id, tz)
            store.set("passi_stato", "ok")
            store.set("passi_errore", "")
        except attivita.StepsError as exc:
            log.warning("Sync passi non riuscita: %s", exc)
            store.set("passi_stato", "errore")
            store.set("passi_errore", str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Errore inatteso nella sync dei passi")
            store.set("passi_stato", "errore")
            store.set("passi_errore", f"Errore inatteso: {exc}")

    def run(self) -> None:
        interval = max(10, int(self.opts.get("sync_minuti", 30))) * 60
        while True:
            self.busy.set()
            self.run_once()
            self.busy.clear()
            self.wake.wait(timeout=interval)
            self.wake.clear()


class CiqualImporter:
    """Scarica la tabella Ciqual in background (una volta sola)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.busy = False

    def start(self, force: bool = False) -> bool:
        with self.lock:
            if self.busy:
                return False
            self.busy = True
        threading.Thread(target=self._run, args=(force,), daemon=True, name="ciqual").start()
        return True

    def _run(self, force: bool) -> None:
        store = Store(self.db_path)
        try:
            if not force and ciqual.count(store) > 0 and not ciqual.needs_macros(store):
                return
            store.set("ciqual_stato", "in_corso")
            n = ciqual.import_ciqual(store, latest_url=os.environ.get("DIARIO_CIQUAL_URL", ciqual.ZENODO_LATEST))
            store.set("ciqual_stato", "ok")
            store.set("ciqual_errore", "")
            log.info("Tabella Ciqual importata: %d alimenti", n)
        except Exception as exc:  # noqa: BLE001
            log.warning("Import Ciqual non riuscito: %s", exc)
            store.set("ciqual_stato", "errore")
            store.set("ciqual_errore", "Download della tabella Ciqual non riuscito. Riprova da Stato.")
        finally:
            store.close()
            with self.lock:
                self.busy = False


class OffImporter:
    """Importa in blocco i prodotti Open Food Facts venduti in Italia (su richiesta, non all'avvio)."""

    def __init__(self, db_path: Path, contact: str = ""):
        self.db_path = db_path
        self.contact = contact
        self.lock = threading.Lock()
        self.busy = False

    def start(self) -> bool:
        with self.lock:
            if self.busy:
                return False
            self.busy = True
        threading.Thread(target=self._run, daemon=True, name="off-import").start()
        return True

    def _run(self) -> None:
        store = Store(self.db_path)
        try:
            store.set("off_import_stato", "in_corso")
            store.set("off_import_errore", "")
            risultato = off_import.import_italy(store, self.contact, log_fn=log.info)
            store.set("off_import_stato", "ok")
            store.set("off_import_errore", "")
            store.set("off_import_alimenti", risultato["alimenti_italiani"])
            log.info("Import Open Food Facts: %d alimenti italiani", risultato["alimenti_italiani"])
        except off_import.OffImportError as exc:
            log.warning("Import Open Food Facts non riuscito: %s", exc)
            store.set("off_import_stato", "errore")
            store.set("off_import_errore", str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Errore inatteso nell'import Open Food Facts")
            store.set("off_import_stato", "errore")
            store.set("off_import_errore", f"Errore inatteso: {exc}")
        finally:
            store.close()
            with self.lock:
                self.busy = False


class PhotoAnalyzer:
    """Stima IA di una foto in un thread separato: la richiesta HTTP che la avvia
    torna subito, il risultato si legge con il polling di /api/foto/<id>/stato.
    Serve perche' Claude puo' metterci diversi secondi (con un eventuale retry anche
    oltre il minuto) e tenere la richiesta HTTP in attesa esponeva al timeout del
    proxy Ingress: la pagina sembrava "fallire" anche se l'analisi poi finiva bene."""

    def __init__(self, db_path: Path, photos_dir: Path, opts: dict):
        self.db_path = db_path
        self.photos_dir = photos_dir
        self.opts = opts
        self.lock = threading.Lock()
        self.busy_ids: set[int] = set()

    def start(self, photo_id: int, hint: str | None) -> bool:
        with self.lock:
            if photo_id in self.busy_ids:
                return False
            self.busy_ids.add(photo_id)
        threading.Thread(target=self._run, args=(photo_id, hint), daemon=True,
                          name=f"foto-analizza-{photo_id}").start()
        return True

    def _run(self, photo_id: int, hint: str | None) -> None:
        store = Store(self.db_path)
        try:
            foto.analyze(store, self.photos_dir, photo_id,
                         self.opts.get("anthropic_api_key", ""), self.opts.get("ia_modello", ""), hint)
        except Exception as exc:  # noqa: BLE001 - lo stato errore e' gia' salvato da analyze() quando possibile
            log.warning("Analisi IA foto %s non riuscita: %s", photo_id, exc)
        finally:
            store.close()
            with self.lock:
                self.busy_ids.discard(photo_id)


def _day(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        abort(400, "Data non valida (formato AAAA-MM-GG).")


def create_app(db_path: Path, syncer: Syncer | None, off: OpenFoodFacts, restrict: bool,
               ciq: CiqualImporter | None = None, opts: dict | None = None,
               off_imp: "OffImporter | None" = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    opts = opts or {}
    analizzatore_foto = PhotoAnalyzer(db_path, PHOTOS_DIR, opts)

    @app.before_request
    def _open():
        if restrict and request.remote_addr not in INGRESS_IPS:
            abort(403)
        g.store = Store(db_path)

    @app.teardown_request
    def _close(_exc):
        store = g.pop("store", None)
        if store:
            store.close()

    @app.errorhandler(DiaryError)
    def _diary_error(exc):
        return jsonify(errore=str(exc)), 400

    @app.errorhandler(OffError)
    def _off_error(exc):
        return jsonify(errore=str(exc)), exc.status

    @app.errorhandler(400)
    def _bad_request(exc):
        return jsonify(errore=getattr(exc, "description", "Richiesta non valida.")), 400

    @app.errorhandler(404)
    def _not_found(_exc):
        return jsonify(errore="Non trovato."), 404

    @app.errorhandler(413)
    def _too_large(_exc):
        return jsonify(errore="La foto e' troppo grande (limite 20 MB)."), 413

    @app.errorhandler(VisionError)
    def _vision_error(exc):
        return jsonify(errore=str(exc)), 502

    def today() -> date:
        return now_local(g.store.get("timezone", DEFAULT_TZ)).date()

    # -- interfaccia ----------------------------------------------------------

    @app.get("/")
    def index():
        resp = send_from_directory(STATIC, "index.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/static/<path:name>")
    def static_files(name):
        resp = send_from_directory(STATIC, name)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/manifest.webmanifest")
    def manifest():
        # Servito alla radice (non sotto /static) perche' "start_url"/"scope" nel
        # manifest sono relativi ".": devono risolvere contro l'indirizzo dell'app,
        # non contro la cartella static.
        resp = send_from_directory(STATIC, "manifest.webmanifest")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # -- stato e sync -----------------------------------------------------------

    @app.get("/api/stato")
    def stato():
        s = g.store
        return jsonify(
            versione=__version__,
            sorgente_dati=s.get("sorgente_dati", "glooko"),
            wizard_completato=bool(s.get("wizard_completato")),
            sync_stato=s.get("sync_stato", "mai"),
            sync_errore=s.get("sync_errore") or None,
            ultima_sync_utc=s.get("ultima_sync_utc"),
            ultimo_tentativo_utc=s.get("sync_tentativo_utc"),
            in_corso=bool(syncer and syncer.busy.is_set()),
            passi_stato=s.get("passi_stato", "mai"),
            passi_errore=s.get("passi_errore") or None,
            ultima_glicemia=s.last_glucose_local(),
            conteggi=s.counts(),
            soglie={"bassa": float(s.get("soglia_bassa", 70)), "alta": float(s.get("soglia_alta", 180))},
            tipi_pasto=list(MEAL_TYPES),
            oggi=today().isoformat(),
            ciqual={
                "stato": "in_corso" if ciq and ciq.busy else s.get("ciqual_stato", "mai"),
                "alimenti": ciqual.count(s),
                "versione": s.get("ciqual_versione"),
                "errore": s.get("ciqual_errore") or None,
            },
            off_import={
                "stato": "in_corso" if off_imp and off_imp.busy else s.get("off_import_stato", "mai"),
                "alimenti": off_import.count(s),
                "errore": s.get("off_import_errore") or None,
            },
            ia_foto={
                "disponibile": bool(opts.get("anthropic_api_key")),
                "modello": opts.get("ia_modello") or foto.DEFAULT_MODEL,
                **foto.costo_mese(s),
            },
        )

    @app.get("/api/integrazione")
    def integrazione_ha():
        """Riepilogo compatto per la integrazione Home Assistant (Fase 4):
        un solo sensore di stato/glicemia/TIR/insulina/carboidrati/ultimo pasto,
        pensato per il polling del DataUpdateCoordinator."""
        return jsonify(ha_snapshot(g.store, today()))

    @app.get("/api/ciqual")
    def ciqual_cerca():
        return jsonify(ciqual.search(g.store, request.args.get("q", "")))

    @app.post("/api/ciqual/importa")
    def ciqual_importa():
        if not ciq:
            return jsonify(errore="Import non disponibile in questa modalita'."), 409
        started = ciq.start(force=True)
        return jsonify(avviato=started, gia_in_corso=not started)

    @app.post("/api/alimenti/importa_italia")
    def importa_italia():
        if not off_imp:
            return jsonify(errore="Import non disponibile in questa modalita'."), 409
        started = off_imp.start()
        return jsonify(avviato=started, gia_in_corso=not started)

    @app.post("/api/sync")
    def sync_now():
        if not syncer:
            return jsonify(errore="Sync disattivata in questa modalita'."), 409
        started = syncer.trigger()
        return jsonify(avviata=started, gia_in_corso=not started)

    @app.get("/api/sorgente_dati")
    def sorgente_dati_get():
        s = g.store
        return jsonify(
            sorgente=s.get("sorgente_dati", "glooko"),
            nightscout_url=s.get("nightscout_url", ""),
            nightscout_token=s.get("nightscout_token", ""),
            xdrip_url=s.get("xdrip_url", ""),
        )

    @app.put("/api/sorgente_dati")
    def sorgente_dati_put():
        body = request.get_json(force=True) or {}
        sorgente = body.get("sorgente")
        if sorgente not in ("glooko", "nightscout", "xdrip"):
            abort(400, "Sorgente non valida.")
        g.store.set("sorgente_dati", sorgente)
        for campo in ("nightscout_url", "nightscout_token", "xdrip_url"):
            if campo in body:
                g.store.set(campo, body[campo] or "")
        if syncer:
            syncer.trigger()
        return jsonify(ok=True)

    @app.post("/api/sorgente_dati/test")
    def sorgente_dati_test():
        """Testa una sorgente SENZA salvarla: utile prima di cambiare quella attiva."""
        body = request.get_json(force=True) or {}
        sorgente = body.get("sorgente")
        tz = g.store.get("timezone", DEFAULT_TZ)
        try:
            if sorgente == "nightscout":
                url = (body.get("nightscout_url") or "").strip()
                if not url:
                    abort(400, "Indica l'indirizzo di Nightscout.")
                source = NightscoutDataSource(url, body.get("nightscout_token", ""), tz=tz)
            elif sorgente == "xdrip":
                url = (body.get("xdrip_url") or "").strip()
                if not url:
                    abort(400, "Indica l'indirizzo del servizio web di xDrip+.")
                source = XDripDataSource(url, tz=tz)
            else:
                abort(400, "Sorgente non valida per il test (Glooko si testa con 'Aggiorna ora').")
        except DataSourceError as exc:
            return jsonify(ok=False, errore=str(exc))
        return jsonify(source.status())

    @app.get("/api/soglie")
    def soglie_get():
        s = g.store
        return jsonify(
            bassa=float(s.get("soglia_bassa", 70)), alta=float(s.get("soglia_alta", 180)),
            molto_bassa=float(s.get("soglia_molto_bassa", 54)), molto_alta=float(s.get("soglia_molto_alta", 250)),
        )

    @app.put("/api/soglie")
    def soglie_put():
        body = request.get_json(force=True) or {}
        for campo, chiave in (("bassa", "soglia_bassa"), ("alta", "soglia_alta"),
                              ("molto_bassa", "soglia_molto_bassa"), ("molto_alta", "soglia_molto_alta")):
            if campo in body and body[campo] not in (None, ""):
                try:
                    g.store.set(chiave, float(body[campo]))
                except (TypeError, ValueError):
                    abort(400, f"Valore non valido per {campo}.")
        return jsonify(ok=True)

    @app.get("/api/wizard")
    def wizard_get():
        return jsonify(completato=bool(g.store.get("wizard_completato")))

    @app.post("/api/wizard/completa")
    def wizard_completa():
        g.store.set("wizard_completato", "1")
        return jsonify(ok=True)

    # -- dati -----------------------------------------------------------------

    @app.get("/api/giorno")
    def giorno():
        return jsonify(day_payload(g.store, _day(request.args.get("data"), today())))

    @app.get("/api/periodo")
    def periodo():
        day_to = _day(request.args.get("a"), today())
        day_from = _day(request.args.get("da"), day_to - timedelta(days=13))
        if day_from > day_to or (day_to - day_from).days > 366:
            abort(400, "Periodo non valido.")
        return jsonify(period_summary(g.store, day_from, day_to))

    @app.get("/api/passi")
    def passi_periodo():
        day_to = _day(request.args.get("a"), today())
        day_from = _day(request.args.get("da"), day_to - timedelta(days=13))
        if day_from > day_to or (day_to - day_from).days > 366:
            abort(400, "Periodo non valido.")
        return jsonify(steps_summary(g.store, day_from, day_to))

    @app.get("/api/dieta")
    def dieta_periodo():
        day_to = _day(request.args.get("a"), today())
        day_from = _day(request.args.get("da"), day_to - timedelta(days=6))
        if day_from > day_to or (day_to - day_from).days > 366:
            abort(400, "Periodo non valido.")
        return jsonify(diet_summary(g.store, day_from, day_to))

    @app.get("/api/calendario")
    def calendario():
        try:
            anno = int(request.args.get("anno"))
            mese = int(request.args.get("mese"))
        except (TypeError, ValueError):
            abort(400, "Indica anno e mese.")
        if not 1 <= mese <= 12:
            abort(400, "Mese non valido.")
        return jsonify(calendar_month(g.store, anno, mese))

    @app.get("/api/alimenti/<int:food_id>/occorrenze")
    def alimento_occorrenze(food_id):
        return jsonify(food_occurrences(g.store, food_id))

    @app.get("/api/agp")
    def agp():
        day_to = _day(request.args.get("a"), today())
        day_from = _day(request.args.get("da"), day_to - timedelta(days=13))
        if day_from > day_to or (day_to - day_from).days > 366:
            abort(400, "Periodo non valido.")
        return jsonify(agp_profile(g.store, day_from, day_to))

    # -- report PDF per il diabetologo -----------------------------------------

    @app.get("/api/report")
    def report_lista():
        return jsonify(report.list_reports(g.store))

    @app.post("/api/report")
    def report_crea():
        body = request.get_json(force=True) or {}
        day_to = _day(body.get("a"), today())
        if body.get("giorni"):
            try:
                giorni = int(body["giorni"])
            except (TypeError, ValueError):
                abort(400, "Numero di giorni non valido.")
            day_from = day_to - timedelta(days=giorni - 1)
        else:
            day_from = _day(body.get("da"), day_to - timedelta(days=29))
        if day_from > day_to or (day_to - day_from).days > 366:
            abort(400, "Periodo non valido.")
        kind = body.get("kind") or f"{(day_to - day_from).days + 1}gg"
        soglie = {"bassa": float(g.store.get("soglia_bassa", 70)), "alta": float(g.store.get("soglia_alta", 180))}
        domande = g.store.get("domande_visita", "")
        risultato = report.generate(g.store, REPORTS_DIR, day_from, day_to, kind, soglie, domande)
        return jsonify(risultato), 201

    @app.get("/api/report/<int:report_id>/pdf")
    def report_pdf(report_id):
        row = report.get_report(g.store, report_id)
        if not row:
            abort(404)
        resp = send_from_directory(REPORTS_DIR, row["filename"], mimetype="application/pdf")
        resp.headers["Content-Disposition"] = (
            f'inline; filename="diario-camaps-{row["day_from"]}_{row["day_to"]}.pdf"')
        return resp

    @app.get("/api/report/impostazioni")
    def report_impostazioni_get():
        return jsonify(data_ultima_visita=g.store.get("data_ultima_visita") or None,
                       domande_visita=g.store.get("domande_visita") or "")

    @app.put("/api/report/impostazioni")
    def report_impostazioni_put():
        body = request.get_json(force=True) or {}
        if "data_ultima_visita" in body:
            g.store.set("data_ultima_visita", body["data_ultima_visita"] or "")
        if "domande_visita" in body:
            g.store.set("domande_visita", body["domande_visita"] or "")
        return jsonify(ok=True)

    @app.get("/api/insights/settimana")
    def insights_settimana():
        fino_a = _day(request.args.get("fino_a"), insights.ultima_domenica(today()))
        return jsonify(insights.weekly_insights(g.store, fino_a))

    # -- alimenti -------------------------------------------------------------

    @app.get("/api/alimenti")
    def alimenti():
        return jsonify(search_foods(g.store, request.args.get("q", "")))

    @app.get("/api/alimenti/frequenti")
    def alimenti_frequenti():
        return jsonify(frequent_foods(g.store))

    @app.post("/api/ricette")
    def nuova_ricetta():
        return jsonify(save_recipe(g.store, request.get_json(force=True) or {})), 201

    @app.put("/api/ricette/<int:food_id>")
    def modifica_ricetta(food_id):
        return jsonify(save_recipe(g.store, request.get_json(force=True) or {}, food_id))

    @app.post("/api/alimenti")
    def nuovo_alimento():
        return jsonify(create_food(g.store, request.get_json(force=True) or {})), 201

    @app.get("/api/alimenti/<int:food_id>")
    def alimento(food_id):
        food = get_food(g.store, food_id)
        if not food:
            abort(404)
        return jsonify(food)

    @app.put("/api/alimenti/<int:food_id>")
    def modifica_alimento(food_id):
        return jsonify(update_food(g.store, food_id, request.get_json(force=True) or {}))

    @app.delete("/api/alimenti/<int:food_id>")
    def elimina_alimento(food_id):
        delete_food(g.store, food_id)
        return jsonify(ok=True)

    @app.get("/api/off/cerca")
    def off_cerca():
        return jsonify(off.search(request.args.get("q", "")))

    @app.get("/api/off/barcode/<code>")
    def off_barcode(code):
        local = food_by_barcode(g.store, code)
        if local:
            return jsonify(locale=local)
        product = off.barcode(code)
        if not product:
            return jsonify(errore="Prodotto non trovato su Open Food Facts. Puoi aggiungerlo a mano."), 404
        return jsonify(off=product)

    # -- pasti ----------------------------------------------------------------

    @app.get("/api/pasti/<int:meal_id>")
    def pasto(meal_id):
        meal = get_meal(g.store, meal_id)
        if not meal:
            abort(404)
        return jsonify(meal)

    @app.post("/api/pasti")
    def nuovo_pasto():
        return jsonify(save_meal(g.store, request.get_json(force=True) or {})), 201

    @app.put("/api/pasti/<int:meal_id>")
    def modifica_pasto(meal_id):
        return jsonify(save_meal(g.store, request.get_json(force=True) or {}, meal_id))

    @app.delete("/api/pasti/<int:meal_id>")
    def elimina_pasto(meal_id):
        delete_meal(g.store, meal_id)
        return jsonify(ok=True)

    # -- note del giorno -------------------------------------------------------

    @app.get("/api/note")
    def note_giorno():
        giorno = request.args.get("giorno") or today().isoformat()
        return jsonify(tipi=list(NOTE_KINDS), note=notes_for_day(g.store, giorno))

    @app.post("/api/note")
    def nuova_nota():
        return jsonify(create_note(g.store, request.get_json(force=True) or {})), 201

    @app.delete("/api/note/<int:note_id>")
    def elimina_nota(note_id):
        delete_note(g.store, note_id)
        return jsonify(ok=True)

    # -- ricerca nel diario -----------------------------------------------------

    @app.get("/api/ricerca")
    def ricerca_diario():
        return jsonify(search_diary(g.store, request.args.get("q", "")))

    # -- foto dei pasti ---------------------------------------------------------

    @app.post("/api/pasti/<int:meal_id>/foto")
    def carica_foto(meal_id):
        file = request.files.get("foto")
        if not file or not file.filename:
            abort(400, "Nessuna foto ricevuta.")
        photo = foto.save_photo(g.store, PHOTOS_DIR, meal_id, file.read())
        return jsonify(photo), 201

    @app.get("/api/foto/<int:photo_id>")
    def foto_file(photo_id):
        photo_row = foto.get_photo(g.store, photo_id)
        if not photo_row:
            abort(404)
        resp = send_from_directory(PHOTOS_DIR, photo_row["filename"])
        resp.headers["Cache-Control"] = "private, max-age=604800, immutable"
        return resp

    @app.delete("/api/foto/<int:photo_id>")
    def elimina_foto(photo_id):
        foto.delete_photo(g.store, PHOTOS_DIR, photo_id)
        return jsonify(ok=True)

    @app.post("/api/foto/<int:photo_id>/analizza")
    def analizza_foto(photo_id):
        hint = (request.get_json(silent=True) or {}).get("hint")
        photo_row = foto.get_photo(g.store, photo_id)
        if not photo_row:
            abort(404)
        analizzatore_foto.start(photo_id, hint)
        return jsonify(foto.get_photo(g.store, photo_id))

    @app.get("/api/foto/<int:photo_id>/stato")
    def stato_analisi_foto(photo_id):
        photo_row = foto.get_photo(g.store, photo_id)
        if not photo_row:
            abort(404)
        return jsonify(photo_row)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    opts = load_options()
    db_path = DATA_DIR / "diario.db"
    store = Store(db_path)  # crea o aggiorna lo schema prima di tutto
    demo = bool(opts.get("modalita_demo"))
    if demo and not store.get("demo_seminato"):
        log.info("Modalita' demo: genero circa %d giorni di dati sintetici...", demo_data.GIORNI_DEMO)
        demo_data.seed_demo_data(store, store.get("timezone", DEFAULT_TZ))
        log.info("Dati demo pronti.")
    store.close()
    syncer = None
    if demo:
        log.info("Modalita' demo attiva: sincronizzazione da una sorgente reale disattivata.")
    elif not os.environ.get("DIARIO_SENZA_SYNC"):
        syncer = Syncer(db_path, opts)
        syncer.start()
    restrict = OPTIONS_FILE.exists() and not os.environ.get("DIARIO_APERTO")
    ciq = CiqualImporter(db_path)
    if not os.environ.get("DIARIO_SENZA_CIQUAL"):
        ciq.start()
    off_imp = OffImporter(db_path, opts.get("off_contatto", ""))
    app = create_app(db_path, syncer, OpenFoodFacts(opts.get("off_contatto", "")), restrict, ciq, opts, off_imp)
    port = int(os.environ.get("PORT", "8099"))
    log.info("Diario CamAPS %s in ascolto sulla porta %d", __version__, port)
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
