"""SPIKE — can a man reach his nearest peer-support group for a Monday evening
session BY BUS, and get home again?

This is throwaway research code, deliberately kept OUT of ``src/``. Nothing in
the pipeline imports it. It exists so the numbers in
``docs/adr/0002-public-transport-feasibility-spike.md`` can be re-derived.

WHAT IT ANSWERS
    1. Is there a usable open timetable feed?  (BODS GTFS — yes, no API key)
    2. Is evening round-trip access continuous, like drive time, or binary?
    3. Does the answer survive changing the "what journey is acceptable"
       parameters, which are a value judgement rather than a measurement?
    4. Does the shape scale to 35,672 LSOAs x 354 groups?

WHY THE ROUND TRIP
    Andy's Man Club runs Mondays 19:00-21:00. Getting there is the easy half;
    the last bus home is usually the binding constraint. A one-way travel time
    would score an area as well-served when nobody can actually get back.

METHOD — Connection Scan Algorithm (Dibbelt et al.), not RAPTOR
    CSA is a single sorted scan over "connections" (one vehicle leg from stop A
    to stop B). It is far easier to get right than RAPTOR and fast enough here.
    Two scans PER GROUP, not per origin:
      * BACKWARD from the group, arriving 19:00  -> latest departure per stop
      * FORWARD  from the group, leaving  21:00  -> earliest arrival per stop
    Origins then join on by a walk to nearby stops. This is the shape that
    scales: cost grows with the ~354 GROUPS, not the 35,672 origins.

KNOWN LIMITATIONS (see the ADR — these are why nothing here is scored yet)
    * BODS is a BUS feed. Nationally it carries 3 rail routes and 57 tram; GB
      rail is effectively absent. Rural feasibility is therefore UNDERSTATED
      wherever a station would have served the trip.
    * Walk access is straight-line x 1.3, not routed. Ignores rivers, railways
      and dual carriageways, so it OVERSTATES walkability.
    * Regional feed files truncate journeys that cross a region boundary. Use
      ``--feed all`` for anything load-bearing.

USAGE
    python spikes/pt_evening_access.py --las "Nottingham|Boston"
    python spikes/pt_evening_access.py --las "Nottingham|Boston" --sweep
    python spikes/pt_evening_access.py --feed east_midlands --las Nottingham

The ~1.4GB national feed is cached under data/raw/real/gtfs/ (git-ignored).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import Config, load_config        # noqa: E402
from src.fetch import USER_AGENT                   # noqa: E402
from src.travel_time import haversine_km           # noqa: E402

BODS = "https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/{region}"

# --- What counts as a journey a man would actually make --------------------
# These are VALUE JUDGEMENTS, not measurements. The spike's third question is
# how much the answer moves when they move, so they are all overridable.
DEFAULTS = dict(
    session_start=19 * 3600,      # AMC sessions: Monday 19:00 ...
    session_end=21 * 3600,        #                       ... to 21:00
    earliest_depart=17 * 3600 + 30 * 60,   # leaving before 17:30 is not a real ask
    latest_home=24 * 3600 + 30 * 60,       # home by 00:30 or it does not count
    max_journey=90 * 60,          # per leg
    access_m=800,                 # walk to/from a stop
    transfer_m=400,               # walk when changing
    min_transfer=60,              # interchange buffer, seconds
    walk_only_m=2500,             # close enough to walk the whole way
)
WALK_MS = 4.8 * 1000 / 3600       # 4.8 km/h
CIRCUITY = 1.3                    # straight-line -> street distance
M_PER_DEG = 111_320.0


def walk_sec(metres):
    return metres * CIRCUITY / WALK_MS


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------
def fetch_feed(cfg: Config, region: str) -> Path:
    dest = cfg.path("real_raw") / "gtfs" / f"itm_{region}_gtfs.zip"
    if dest.exists():
        print(f"[feed] cached {dest.name} ({dest.stat().st_size/1e6:.0f} MB)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[feed] downloading {region} from BODS (no API key needed) ...")
    # Streamed, not buffered: the national file is ~1.4GB and src.fetch.get
    # would hold the whole body in memory.
    import requests
    tmp = dest.with_suffix(".tmp")
    with requests.get(BODS.format(region=region), stream=True, timeout=1800,
                      headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.rename(dest)
    print(f"[feed] {dest.name} ({dest.stat().st_size/1e6:.0f} MB)")
    return dest


def active_services(zf: zipfile.ZipFile, date: dt.date) -> set:
    """GTFS calendar + calendar_dates -> service_ids running on `date`."""
    ds, d = date.strftime("%Y%m%d"), int(date.strftime("%Y%m%d"))
    day = ["monday", "tuesday", "wednesday", "thursday", "friday",
           "saturday", "sunday"][date.weekday()]
    with zf.open("calendar.txt") as fh:
        cal = pd.read_csv(fh, dtype=str)
    act = set(cal[(cal[day] == "1")
                  & (cal.start_date.astype(int) <= d)
                  & (cal.end_date.astype(int) >= d)].service_id)
    with zf.open("calendar_dates.txt") as fh:
        cd = pd.read_csv(fh, dtype=str)
    cd = cd[cd.date == ds]
    act |= set(cd[cd.exception_type == "1"].service_id)
    act -= set(cd[cd.exception_type == "2"].service_id)
    return act


def load_window(zip_path: Path, date: dt.date, lo=15 * 3600, hi=27 * 3600):
    """Stop_times inside the evening band, on trips running on `date`.

    stop_times.txt is 5.4GB nationally, so it is streamed in pyarrow batches and
    filtered to active trips before anything is materialised as pandas.
    """
    zf = zipfile.ZipFile(zip_path)
    act = active_services(zf, date)
    with zf.open("trips.txt") as fh:
        trips = pd.read_csv(fh, dtype=str, usecols=["route_id", "service_id", "trip_id"])
    trips = trips[trips.service_id.isin(act)]
    with zf.open("routes.txt") as fh:
        routes = pd.read_csv(fh, dtype=str, usecols=["route_id", "route_type"])
    trips = trips.merge(routes, on="route_id", how="left")
    print(f"[feed] {len(act):,} services / {len(trips):,} trips run on {date} "
          f"({date:%A})")
    rt = trips.route_type.value_counts().to_dict()
    print(f"[feed] route_type mix on the day: {rt}   (3=bus 200=coach 0=tram 2=rail)")

    tid = pa.array(trips.trip_id.to_numpy(), type=pa.string())

    def to_sec(s):
        p = s.str.split(":", expand=True)
        return (pd.to_numeric(p[0], errors="coerce") * 3600
                + pd.to_numeric(p[1], errors="coerce") * 60
                + pd.to_numeric(p[2], errors="coerce"))

    keep, n_in, t0 = [], 0, time.time()
    with zf.open("stop_times.txt") as fh:
        reader = pv.open_csv(
            fh, read_options=pv.ReadOptions(block_size=256 << 20),
            convert_options=pv.ConvertOptions(column_types={
                "trip_id": pa.string(), "arrival_time": pa.string(),
                "departure_time": pa.string(), "stop_id": pa.string(),
                "stop_sequence": pa.int32()}))
        for batch in reader:
            n_in += batch.num_rows
            t = pa.Table.from_batches([batch]).select(
                ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
            t = t.filter(pc.is_in(t["trip_id"], value_set=tid))
            if t.num_rows == 0:
                continue
            df = t.to_pandas()
            df["arr"] = to_sec(df.arrival_time)
            df["dep"] = to_sec(df.departure_time)
            df = df[(df.arr >= lo) & (df.arr <= hi)]
            if len(df):
                keep.append(df[["trip_id", "stop_id", "stop_sequence", "arr", "dep"]])
    st = pd.concat(keep, ignore_index=True).sort_values(["trip_id", "stop_sequence"])
    print(f"[feed] stop_times {n_in:,} rows -> {len(st):,} in the evening band "
          f"({time.time()-t0:.0f}s)")
    with zf.open("stops.txt") as fh:
        stops = pd.read_csv(fh, dtype=str,
                            usecols=["stop_id", "stop_name", "stop_lat", "stop_lon"])
    for c in ("stop_lat", "stop_lon"):
        stops[c] = pd.to_numeric(stops[c], errors="coerce")
    return st, stops.dropna(subset=["stop_lat", "stop_lon"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Connection Scan
# ---------------------------------------------------------------------------
class Network:
    def __init__(self, st: pd.DataFrame, stops: pd.DataFrame, transfer_m: int):
        sid = pd.Index(stops.stop_id)
        st = st.assign(si=sid.get_indexer(st.stop_id))
        st = st[st.si >= 0]
        trip = st.trip_id.to_numpy()
        same = trip[1:] == trip[:-1]          # consecutive rows of one trip
        f = st.si.to_numpy()[:-1][same]
        t = st.si.to_numpy()[1:][same]
        dep = st.dep.to_numpy()[:-1][same]
        arr = st.arr.to_numpy()[1:][same]
        tr = pd.factorize(st.trip_id)[0][:-1][same]
        ok = arr >= dep
        f, t, dep, arr, tr = (a[ok] for a in (f, t, dep, arr, tr))
        self.n_trips = int(tr.max()) + 1 if len(tr) else 1
        o = np.argsort(dep, kind="stable")
        self.F = (f[o], t[o], dep[o], arr[o], tr[o])
        o = np.argsort(-arr, kind="stable")
        self.B = (f[o], t[o], dep[o], arr[o], tr[o])
        self.n_conn = len(f)

        self.ns = len(stops)
        lat = stops.stop_lat.to_numpy()
        self.cos = np.cos(np.radians(lat.mean()))
        self.xy = np.c_[stops.stop_lon.to_numpy() * M_PER_DEG * self.cos, lat * M_PER_DEG]
        self.tree = cKDTree(self.xy)
        pr = self.tree.query_pairs(transfer_m, output_type="ndarray")
        w = walk_sec(np.linalg.norm(self.xy[pr[:, 0]] - self.xy[pr[:, 1]], axis=1))
        a = np.concatenate([pr[:, 0], pr[:, 1]])
        b = np.concatenate([pr[:, 1], pr[:, 0]])
        w = np.concatenate([w, w])
        o = np.argsort(a, kind="stable")
        self.fp_b, self.fp_w = b[o], w[o]
        self.fp_start = np.searchsorted(a[o], np.arange(self.ns + 1))
        print(f"[net]  {self.n_conn:,} connections / {self.n_trips:,} trips / "
              f"{self.ns:,} stops / {len(self.fp_b):,} footpaths")

    def near(self, lon, lat, radius):
        p = np.array([lon * M_PER_DEG * self.cos, lat * M_PER_DEG])
        idx = self.tree.query_ball_point(p, radius)
        if not idx:
            return np.array([], int), np.array([])
        idx = np.array(idx)
        return idx, walk_sec(np.linalg.norm(self.xy[idx] - p, axis=1))

    def forward(self, src, src_t, min_transfer):
        """Earliest arrival at every stop, leaving `src` at `src_t`.

        Two clocks, because they are not the same thing: `a` is when you are
        PRESENT at a stop (what the caller wants), `b` is the earliest you may
        BOARD there. Arriving by vehicle costs `min_transfer` before you can
        board again; starting your journey there, or walking in, does not. A
        single clock would charge you an interchange penalty for getting on your
        first bus.
        """
        a = np.full(self.ns, np.inf)
        b = np.full(self.ns, np.inf)
        for s, t in zip(src, src_t):
            if t < a[s]:
                a[s] = b[s] = t
        for s in src:
            for k in range(self.fp_start[s], self.fp_start[s + 1]):
                n, c = self.fp_b[k], a[s] + self.fp_w[k]
                if c < a[n]:
                    a[n] = b[n] = c
        cf, ct, cd, ca, ctr = self.F
        on = np.zeros(self.n_trips, bool)
        for i in range(len(cf)):
            tr = ctr[i]
            if on[tr] or b[cf[i]] <= cd[i]:
                on[tr] = True
                j = ct[i]
                if ca[i] < a[j]:
                    a[j] = ca[i]
                    if ca[i] + min_transfer < b[j]:
                        b[j] = ca[i] + min_transfer
                    for k in range(self.fp_start[j], self.fp_start[j + 1]):
                        n, c = self.fp_b[k], ca[i] + self.fp_w[k]
                        if c < a[n]:
                            a[n] = c
                            if c < b[n]:
                                b[n] = c
        return a

    def backward(self, tgt, tgt_t, min_transfer):
        """Latest departure from every stop that still reaches `tgt` by `tgt_t`.

        The mirror of `forward`, with the same two clocks: `d` is the latest you
        may DEPART a stop (what the caller wants), `v` the latest a vehicle may
        drop you there. Alighting to change costs `min_transfer`; walking the
        last leg, or finishing there, does not.
        """
        d = np.full(self.ns, -np.inf)
        v = np.full(self.ns, -np.inf)
        for s, t in zip(tgt, tgt_t):
            if t > d[s]:
                d[s] = v[s] = t
        for s in tgt:
            for k in range(self.fp_start[s], self.fp_start[s + 1]):
                n, c = self.fp_b[k], d[s] - self.fp_w[k]
                if c > d[n]:
                    d[n] = v[n] = c
        cf, ct, cd, ca, ctr = self.B
        on = np.zeros(self.n_trips, bool)
        for i in range(len(cf)):
            tr = ctr[i]
            if on[tr] or ca[i] <= v[ct[i]]:
                on[tr] = True
                j = cf[i]
                if cd[i] > d[j]:
                    d[j] = cd[i]
                    if cd[i] - min_transfer > v[j]:
                        v[j] = cd[i] - min_transfer
                    for k in range(self.fp_start[j], self.fp_start[j + 1]):
                        n, c = self.fp_b[k], cd[i] - self.fp_w[k]
                        if c > d[n]:
                            d[n] = c
                            if c > v[n]:
                                v[n] = c
        return d


# ---------------------------------------------------------------------------
# The question
# ---------------------------------------------------------------------------
def evaluate(net: Network, origins: pd.DataFrame, groups: pd.DataFrame, P: dict):
    n = len(origins)
    best_in = np.full(n, np.inf)
    best_out = np.full(n, np.inf)
    home = np.full(n, np.inf)
    rt = np.zeros(n, bool)
    saw_in = np.zeros(n, bool)
    saw_out = np.zeros(n, bool)
    gname = np.full(n, "", object)
    mode = np.full(n, "none", object)
    ost = [net.near(r.centroid_lon, r.centroid_lat, P["access_m"])
           for r in origins.itertuples()]
    for g in groups.itertuples():
        gs, gw = net.near(g.lon, g.lat, P["access_m"])
        dkm = haversine_km(origins.centroid_lon.to_numpy(),
                           origins.centroid_lat.to_numpy(), g.lon, g.lat)
        wsec = walk_sec(dkm * 1000)
        walkable = dkm * 1000 <= P["walk_only_m"]
        if len(gs):
            ld = net.backward(gs, P["session_start"] - gw, P["min_transfer"])
            ea = net.forward(gs, P["session_end"] + gw, P["min_transfer"])
        else:
            ld = np.full(net.ns, -np.inf)
            ea = np.full(net.ns, np.inf)
        for i in range(n):
            si, sw = ost[i]
            ji = jo = hm = np.inf
            m = "bus"
            if len(si):
                leave = float(np.max(ld[si] - sw))
                if np.isfinite(leave):
                    saw_in[i] = True
                    if leave >= P["earliest_depart"]:
                        ji = P["session_start"] - leave
                h = float(np.min(ea[si] + sw))
                if np.isfinite(h):
                    saw_out[i] = True
                    hm = h
                    if h <= P["latest_home"]:
                        jo = h - P["session_end"]
            if walkable[i] and wsec[i] < ji:
                ji, m = wsec[i], "walk"
            if walkable[i] and wsec[i] < jo:
                jo, hm = wsec[i], P["session_end"] + wsec[i]
            if ji <= P["max_journey"] and ji < best_in[i]:
                best_in[i], gname[i], mode[i] = ji, g.name, m
            if ji <= P["max_journey"] and jo <= P["max_journey"]:
                rt[i] = True
                if jo < best_out[i]:
                    best_out[i], home[i] = jo, hm
    reason = np.where(rt, "ok",
              np.where(~saw_in & ~saw_out, "no service",
               np.where(best_in > P["max_journey"], "inbound too long/late",
                        "no way home")))
    return pd.DataFrame({
        "area_code": origins.area_code.to_numpy(),
        "la_name": origins.la_name.to_numpy(),
        "pt_in_min": np.where(np.isfinite(best_in), best_in / 60, np.nan),
        "pt_out_min": np.where(np.isfinite(best_out), best_out / 60, np.nan),
        "home_clock_h": np.where(np.isfinite(home), home / 3600, np.nan),
        "round_trip_ok": rt, "reason": reason, "pt_group": gname, "mode": mode})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--las", default="Nottingham|Boston|Newark and Sherwood|Mansfield",
                    help="pipe-separated LA names to test")
    ap.add_argument("--feed", default="all",
                    help="BODS region slug, or 'all' for the national file")
    ap.add_argument("--date", default="2026-09-14",
                    help="a Monday inside the feed's validity")
    ap.add_argument("--radius-km", type=float, default=45.0,
                    help="candidate groups within this distance of the LA centroid")
    ap.add_argument("--sweep", action="store_true",
                    help="re-run under looser/stricter acceptable-journey rules")
    ap.add_argument("--out", default=None, help="write per-LSOA results here (parquet)")
    args = ap.parse_args()

    cfg = load_config()
    date = dt.date.fromisoformat(args.date)
    st, stops = load_window(fetch_feed(cfg, args.feed), date)
    net = Network(st, stops, DEFAULTS["transfer_m"])

    geo = pd.read_parquet(cfg.path("interim") / "dim_geography.parquet")
    prov = pd.read_parquet(cfg.path("interim") / "dim_provision.parquet")
    score_fp = cfg.path("fact_score")
    score = pd.read_parquet(score_fp) if score_fp.exists() else None

    settings = {"baseline": {}}
    if args.sweep:
        settings.update({
            "generous":      dict(earliest_depart=16 * 3600, max_journey=120 * 60),
            "very generous": dict(earliest_depart=15 * 3600, max_journey=150 * 60,
                                  access_m=1200, latest_home=25 * 3600 + 30 * 60),
            "strict":        dict(earliest_depart=18 * 3600, max_journey=60 * 60),
        })

    frames = []
    for label, over in settings.items():
        P = {**DEFAULTS, **over}
        print(f"\n=== {label}: depart >= {P['earliest_depart']/3600:.1f}h, "
              f"max {P['max_journey']/60:.0f} min, walk <= {P['access_m']} m, "
              f"home by {P['latest_home']/3600:.1f}h")
        for la in args.las.split("|"):
            orig = geo[geo.la_name == la].reset_index(drop=True)
            if orig.empty:
                print(f"[warn] no LSOAs for LA {la!r}")
                continue
            clon, clat = orig.centroid_lon.mean(), orig.centroid_lat.mean()
            km = haversine_km(prov.lon.to_numpy(), prov.lat.to_numpy(), clon, clat)
            grp = prov.assign(km=km)
            grp = grp[grp.km <= args.radius_km].sort_values("km").reset_index(drop=True)
            t0 = time.time()
            res = evaluate(net, orig, grp, P)
            res["setting"] = label
            frames.append(res)
            print(f"  {la:<22} {len(orig):4d} LSOAs x {len(grp):2d} groups  "
                  f"{time.time()-t0:5.1f}s  round-trip {res.round_trip_ok.mean():6.1%}"
                  f"   ({(time.time()-t0)/max(len(grp),1):.2f}s per group)", flush=True)

    out = pd.concat(frames, ignore_index=True)
    if score is not None:
        cols = ["area_code", "travel_minutes", "no_car_share", "rank_reach",
                "male_working_age_pop", "supply_index", "priority_score"]
        out = out.merge(score[cols], on="area_code", how="left")
        base = out[out.setting == "baseline"]
        if len(base):
            print("\n--- baseline, against the car-only surface ---")
            print(base.groupby("la_name").agg(
                n=("area_code", "size"),
                round_trip=("round_trip_ok", "mean"),
                med_car_min=("travel_minutes", "median"),
                med_bus_min=("pt_in_min", "median"),
                med_no_car=("no_car_share", "median"),
                best_rank_reach=("rank_reach", "min")).round(3).to_string())
            ok = base.round_trip_ok.astype(float)
            if ok.nunique() > 1:
                print(f"\ncorr(no_car_share, round_trip_ok) = "
                      f"{base.no_car_share.corr(ok):.3f}")
            f = base[base.round_trip_ok]
            if len(f):
                r = (f.pt_in_min / f.travel_minutes).describe()
                print(f"bus/car time ratio where a round trip works: "
                      f"median {r['50%']:.2f}x  (IQR {r['25%']:.2f}-{r['75%']:.2f})")
    print("\n--- why infeasible ---")
    print(out.groupby(["setting", "la_name"]).reason.value_counts().to_string())
    if args.out:
        out.to_parquet(args.out, index=False)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
