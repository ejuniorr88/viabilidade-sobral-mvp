import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from shapely.geometry import shape, Point
from shapely.prepared import prep
from shapely.strtree import STRtree
from shapely.ops import transform
from pyproj import Transformer


def _safe_get(props: Dict[str, Any], keys: List[str], default=None):
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return props[k]
    return default


@dataclass
class ZoneHit:
    props: Dict[str, Any]


@dataclass
class StreetHit:
    props: Dict[str, Any]
    dist_m: float


class GeoEngine:
    """
    Motor geográfico (SPEC 3.2):
    - zona por point-in-polygon usando STRtree
    - rua por nearest em EPSG:3857 (distância em metros)
    """

    def __init__(self, zone_file: str, streets_file: str):
        self.zone_file = zone_file
        self.streets_file = streets_file

        self._zones = self._load_geojson(zone_file)
        self._streets = self._load_geojson(streets_file)

        # ---------- ZONAS ----------
        self._zone_geoms = [shape(f["geometry"]) for f in self._zones["features"]]
        self._zone_props = [f.get("properties", {}) for f in self._zones["features"]]
        self._zone_prepared = [prep(g) for g in self._zone_geoms]
        self._zone_index = STRtree(self._zone_geoms)
        self._zone_id_to_idx = {id(g): i for i, g in enumerate(self._zone_geoms)}

        # ---------- RUAS ----------
        self._to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
        self._streets_geoms_4326 = [shape(f["geometry"]) for f in self._streets["features"]]
        self._streets_props = [f.get("properties", {}) for f in self._streets["features"]]
        self._streets_geoms_3857 = [transform(self._to_3857, g) for g in self._streets_geoms_4326]
        self._streets_index = STRtree(self._streets_geoms_3857)
        self._street_id_to_idx = {id(g): i for i, g in enumerate(self._streets_geoms_3857)}

    def _load_geojson(self, path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # -------------------------
    # SPEC 3.2 - Funções base
    # -------------------------
    def find_zone_for_click(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        pt = Point(lon, lat)
        candidates = self._zone_index.query(pt)

        for geom in candidates:
            idx = self._zone_id_to_idx.get(id(geom))
            if idx is not None and self._zone_prepared[idx].contains(pt):
                return self._zone_props[idx]

        # fallback total (caso raro)
        for i, geom in enumerate(self._zone_geoms):
            if self._zone_prepared[i].contains(pt):
                return self._zone_props[i]

        return None

    def find_nearest_street(self, lat: float, lon: float, max_dist_m: float = 120) -> Optional[Dict[str, Any]]:
        pt_3857 = transform(self._to_3857, Point(lon, lat))
        candidates = self._streets_index.query(pt_3857)

        best_idx = None
        best_d = float("inf")

        for geom in candidates:
            d = pt_3857.distance(geom)
            if d < best_d:
                best_d = d
                best_idx = self._street_id_to_idx.get(id(geom))

        if best_idx is None:
            # fallback total (ruas.json costuma ser leve)
            for i, geom in enumerate(self._streets_geoms_3857):
                d = pt_3857.distance(geom)
                if d < best_d:
                    best_d = d
                    best_idx = i

        if best_idx is None or best_d > max_dist_m:
            return None

        props = dict(self._streets_props[best_idx])
        props["_dist_m"] = float(best_d)
        return props

    def compute_location(self, lat: float, lon: float) -> Dict[str, Any]:
        raw_zone = self.find_zone_for_click(lat, lon)
        raw_rua = self.find_nearest_street(lat, lon, max_dist_m=120)

        zona_sigla = _safe_get(raw_zone or {}, ["sigla", "SIGLA"], None)
        zona_nome = _safe_get(raw_zone or {}, ["zona", "nome", "NOME", "ZONA"], None)

        rua_nome = _safe_get(raw_rua or {}, ["log_ofic", "LOG_OFIC", "nome", "NOME"], None)
        hierarquia = _safe_get(raw_rua or {}, ["hierarquia", "HIERARQUIA"], None)

        return {
            "zona_sigla": zona_sigla,
            "zona_nome": zona_nome,
            "rua_nome": rua_nome,
            "hierarquia": hierarquia,
            "raw_zone": raw_zone,
            "raw_rua": raw_rua,
        }
