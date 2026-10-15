"""Lettura delle voci registrate da CamAPS FX.

CamAPS registra su Glooko:
- i boli dati con il calcolatore (insulina > 0, con o senza carboidrati);
- le voci di "Aggiungi pasto", senza insulina:
  * pasto o spuntino: una voce sola con i carboidrati;
  * trattamento dell'ipoglicemia: una voce sola, nel database non c'e' un
    campo che la distingua; la riconosciamo dalla glicemia bassa a
    quell'ora;
  * pasto a lenta digestione: CamAPS divide i carboidrati in 6 quote
    uguali ogni 30 minuti. Sembrano boli ma non lo sono: CamAPS aggiunge
    insulina automatica solo se la glicemia sale.

classify() trasforma le righe del database in voci con un tipo:
"bolo", "lento", "spuntino", "ipo".
"""

from bisect import bisect_left
from datetime import timedelta

from .timeutil import parse_local

SLOW_GAP_MIN = timedelta(minutes=25)
SLOW_GAP_MAX = timedelta(minutes=35)
SLOW_MIN_PARTS = 3
HYPO_MGDL = 80
NEAR = timedelta(minutes=10)


class GlucoseIndex:
    def __init__(self, points):
        self.points = sorted(points)
        self.times = [t for t, _ in self.points]

    def near(self, t):
        if not self.points:
            return None
        i = bisect_left(self.times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.points):
                d = abs(self.times[j] - t)
                if d <= NEAR and (best is None or d < best[0]):
                    best = (d, self.points[j][1])
        return best[1] if best else None


def classify(rows, glucose_points) -> list[dict]:
    """rows: righe della tabella bolus (dict con ts_local, insulin_delivered,
    carbs_input, bolus_type, rec_carbs, rec_correction, guid)."""
    gindex = glucose_points if isinstance(glucose_points, GlucoseIndex) else GlucoseIndex(glucose_points)
    items = []
    carb_only = []
    for r in sorted(rows, key=lambda x: x["ts_local"]):
        t = parse_local(r["ts_local"])
        if (r["insulin_delivered"] or 0) > 0:
            items.append({
                "tipo": "bolo", "t": t, "fine": t, "U": r["insulin_delivered"],
                "carbo": r["carbs_input"] or 0, "quote": 1, "righe": [(t, r["carbs_input"] or 0)],
                "rec_carbo_U": r.get("rec_carbs"), "rec_correzione_U": r.get("rec_correction"),
                "guid": r["guid"], "glicemia": gindex.near(t),
            })
        elif (r["carbs_input"] or 0) > 0:
            carb_only.append((t, r["carbs_input"], r["guid"]))

    i = 0
    while i < len(carb_only):
        run = [carb_only[i]]
        j = i + 1
        while (j < len(carb_only)
               and abs(carb_only[j][1] - run[-1][1]) < 0.15
               and SLOW_GAP_MIN <= carb_only[j][0] - run[-1][0] <= SLOW_GAP_MAX):
            run.append(carb_only[j])
            j += 1
        if len(run) >= SLOW_MIN_PARTS:
            items.append({
                "tipo": "lento", "t": run[0][0], "fine": run[-1][0], "U": 0.0,
                "carbo": round(sum(c for _, c, _ in run), 1), "quote": len(run),
                "righe": [(t, c) for t, c, _ in run], "guid": run[0][2],
                "glicemia": gindex.near(run[0][0]),
            })
            i = j
            continue
        t, c, guid = carb_only[i]
        g = gindex.near(t)
        items.append({
            "tipo": "ipo" if g is not None and g < HYPO_MGDL else "spuntino",
            "t": t, "fine": t, "U": 0.0, "carbo": c, "quote": 1, "righe": [(t, c)],
            "guid": guid, "glicemia": g,
        })
        i += 1
    items.sort(key=lambda it: it["t"])
    return items
