/** Lon/lat → UTM metres (WGS 84), the projection the height rasters are stored in. */
const A = 6378137;
const F = 1 / 298.257223563;
const K0 = 0.9996;
const E2 = F * (2 - F);
const EP2 = E2 / (1 - E2);
const RAD = Math.PI / 180;

export const utmZone = (lon: number): number => Math.floor((lon + 180) / 6) + 1;

/** EPSG code of a UTM zone: 326xx north of the equator, 327xx south. */
export const utmEpsg = (zone: number, south: boolean): number => (south ? 32700 : 32600) + zone;

export function toUtm(lon: number, lat: number, zone: number, south: boolean): [number, number] {
  const phi = lat * RAD;
  const lam = lon * RAD;
  const lam0 = ((zone - 1) * 6 - 180 + 3) * RAD;
  const sin = Math.sin(phi);
  const cos = Math.cos(phi);
  const tan = Math.tan(phi);
  const n = A / Math.sqrt(1 - E2 * sin * sin);
  const t = tan * tan;
  const c = EP2 * cos * cos;
  const a = (lam - lam0) * cos;
  const m =
    A *
    ((1 - E2 / 4 - (3 * E2 ** 2) / 64 - (5 * E2 ** 3) / 256) * phi -
      ((3 * E2) / 8 + (3 * E2 ** 2) / 32 + (45 * E2 ** 3) / 1024) * Math.sin(2 * phi) +
      ((15 * E2 ** 2) / 256 + (45 * E2 ** 3) / 1024) * Math.sin(4 * phi) -
      ((35 * E2 ** 3) / 3072) * Math.sin(6 * phi));
  const x =
    K0 * n * (a + ((1 - t + c) * a ** 3) / 6 + ((5 - 18 * t + t * t + 72 * c - 58 * EP2) * a ** 5) / 120) +
    500000;
  const y =
    K0 *
    (m +
      n *
        tan *
        ((a * a) / 2 +
          ((5 - t + 9 * c + 4 * c * c) * a ** 4) / 24 +
          ((61 - 58 * t + t * t + 600 * c - 330 * EP2) * a ** 6) / 720));
  return [x, south ? y + 10000000 : y];
}

/** UTM metres → lon/lat (WGS 84). The inverse of `toUtm`, for sampling rasters stored in a UTM zone. */
export function fromUtm(x: number, y: number, zone: number, south: boolean): [number, number] {
  const easting = x - 500000;
  const northing = south ? y - 10_000_000 : y;
  const lam0 = ((zone - 1) * 6 - 180 + 3) * RAD;
  const e1 = (1 - Math.sqrt(1 - E2)) / (1 + Math.sqrt(1 - E2));
  const m = northing / K0;
  const mu = m / (A * (1 - E2 / 4 - (3 * E2 ** 2) / 64 - (5 * E2 ** 3) / 256));
  const phi1 =
    mu +
    ((3 * e1) / 2 - (27 * e1 ** 3) / 32) * Math.sin(2 * mu) +
    ((21 * e1 ** 2) / 16 - (55 * e1 ** 4) / 32) * Math.sin(4 * mu) +
    ((151 * e1 ** 3) / 96) * Math.sin(6 * mu) +
    ((1097 * e1 ** 4) / 512) * Math.sin(8 * mu);
  const sin = Math.sin(phi1);
  const cos = Math.cos(phi1);
  const tan = Math.tan(phi1);
  const n = A / Math.sqrt(1 - E2 * sin * sin);
  const rho = (A * (1 - E2)) / (1 - E2 * sin * sin) ** 1.5;
  const tt = tan * tan;
  const c = EP2 * cos * cos;
  const d = easting / (n * K0);
  const phi =
    phi1 -
    ((n * tan) / rho) *
      (d ** 2 / 2 -
        ((5 + 3 * tt + 10 * c - 4 * c * c - 9 * EP2) * d ** 4) / 24 +
        ((61 + 90 * tt + 298 * c + 45 * tt * tt - 252 * EP2 - 3 * c * c) * d ** 6) / 720);
  const lam =
    lam0 +
    (d -
      ((1 + 2 * tt + c) * d ** 3) / 6 +
      ((5 - 2 * c + 28 * tt - 3 * c * c + 8 * EP2 + 24 * tt * tt) * d ** 5) / 120) /
      cos;
  return [lam / RAD, phi / RAD];
}
