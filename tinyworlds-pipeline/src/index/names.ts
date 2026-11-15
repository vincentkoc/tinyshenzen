const HAN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/**
 * The English name to list beside a Chinese one, so 莲花山公园 can also be found as Lianhuashan
 * Park. Only Chinese names get one: elsewhere the primary name is what people type, and the
 * index stays as it was. Nothing when the data has no English name, or when the name already
 * carries it ("深圳灣公路大橋 Shenzhen Bay Bridge").
 */
export function englishAlias(primary: string, english: string | null | undefined): string | undefined {
  const en = english?.trim();
  if (!en || !HAN.test(primary) || HAN.test(en)) return undefined;
  return primary.toLowerCase().includes(en.toLowerCase()) ? undefined : en;
}
