"""Riga di comando del diario CamAPS.

    python -m camaps_diary sync --days 14          scarica da Glooko nel database
    python -m camaps_diary riepilogo --days 7      riepilogo giornaliero
    python -m camaps_diary import-raw <cartella>   importa un export di glooko_probe.py

Credenziali Glooko: variabili GLOOKO_EMAIL / GLOOKO_PASSWORD, altrimenti richieste.
"""

import argparse
import getpass
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .glooko import SERVERS, GlookoClient, GlookoError
from .stats import daily_summary, format_table
from .store import Store
from .sync import import_raw, sync
from .timeutil import DEFAULT_TZ, now_local


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="camaps_diary", description="Diario CamAPS FX")
    ap.add_argument("--db", default="diario.db", help="file del database (default diario.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="scarica i dati da Glooko")
    s.add_argument("--days", type=int, default=14, help="giorni da recuperare alla prima sync")
    s.add_argument("--server", choices=SERVERS, default="eu")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--base-url", help=argparse.SUPPRESS)
    s.add_argument("--web-origin", help=argparse.SUPPRESS)

    r = sub.add_parser("riepilogo", help="riepilogo giornaliero")
    r.add_argument("--days", type=int, default=7, help="ultimi N giorni (default 7)")
    r.add_argument("--from", dest="dfrom", help="dal giorno AAAA-MM-GG")
    r.add_argument("--to", dest="dto", help="al giorno AAAA-MM-GG")

    i = sub.add_parser("import-raw", help="importa la cartella di glooko_probe.py")
    i.add_argument("cartella")

    args = ap.parse_args(argv)
    store = Store(args.db)
    try:
        if args.cmd == "sync":
            email = os.environ.get("GLOOKO_EMAIL") or input("Email Glooko: ").strip()
            password = os.environ.get("GLOOKO_PASSWORD") or getpass.getpass("Password Glooko: ")
            client = GlookoClient(server=args.server,
                                  device_file=Path(args.db).resolve().with_name(".glooko_device.json"),
                                  api_base=args.base_url, web_origin=args.web_origin,
                                  verbose=args.verbose)
            try:
                client.login(email, password)
                print("-> login ok")
                sync(client, store, backfill_days=max(1, min(args.days, 90)))
            except GlookoError as exc:
                print(f"ERRORE: {exc}")
                return 1
            print("Totale nel database:", store.counts())

        elif args.cmd == "import-raw":
            import_raw(args.cartella, store)
            print("Totale nel database:", store.counts())

        elif args.cmd == "riepilogo":
            today = now_local(store.get("timezone", DEFAULT_TZ)).date()
            dto = datetime.strptime(args.dto, "%Y-%m-%d").date() if args.dto else today
            dfrom = (datetime.strptime(args.dfrom, "%Y-%m-%d").date() if args.dfrom
                     else dto - timedelta(days=args.days - 1))
            print(format_table(daily_summary(store, dfrom, dto)))
            last = store.get("ultima_sync_utc")
            if last:
                print(f"\nUltima sync: {last} (UTC)")
            print("Nota: i giorni con copertura CGM bassa sono parziali.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
