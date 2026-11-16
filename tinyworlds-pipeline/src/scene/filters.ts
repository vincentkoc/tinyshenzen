/**
 * Land use, building and place kinds the maps leave out: they are drawn as plain ground, and
 * the index lists nothing in or for them.
 */

/** Land use subtypes and classes left out. */
export const SKIPPED_LAND_USE = ["military", "prison"] as const;

/** Building classes left out of the index. */
export const SKIPPED_BUILDING_CLASSES = ["military", "barracks", "bunker", "prison"] as const;

/** Overture place categories left out of the index. */
export const SKIPPED_PLACE_CATEGORIES = [
  "military_site",
  "military_base",
  "jail_or_prison",
  "prison",
] as const;

const list = (values: readonly string[]): string => values.map((v) => `'${v}'`).join(", ");

/** A SQL condition over a land_use row: true for rows the maps keep. */
export const LAND_USE_KEPT = `coalesce(subtype, '') NOT IN (${list(SKIPPED_LAND_USE)})
  AND coalesce(class, '') NOT IN (${list(SKIPPED_LAND_USE)})
  AND coalesce(source_tags['amenity'], '') NOT IN (${list(SKIPPED_LAND_USE)})
  AND coalesce(source_tags['landuse'], '') NOT IN (${list(SKIPPED_LAND_USE)})
  AND source_tags['military'] IS NULL`;

/** The same condition for a building row. */
export const BUILDING_KEPT = `coalesce(class, '') NOT IN (${list(SKIPPED_BUILDING_CLASSES)})`;

/** The same condition for a place row. */
export const PLACE_KEPT = `coalesce(basic_category, '') NOT IN (${list(SKIPPED_PLACE_CATEGORIES)})`;
