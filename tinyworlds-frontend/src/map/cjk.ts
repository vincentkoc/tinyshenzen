/** Han (Chinese) characters: the CJK unified ideographs and their first extension. */
export const HAN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/**
 * Traditional characters common in place names, and their simplified forms. Hong Kong and older
 * map data write 深圳灣 where a search box in Shenzhen types 深圳湾; both sides are folded to the
 * simplified form so either finds the other. Not a full converter: just the characters that turn
 * up in streets, stations, parks and buildings.
 */
const TRAD =
  "灣樓東門華龍園廣場車號橋頭區島灘嶺書館會國際學醫體線鐵鄉莊圍開發興廠業雲陽東陳劉黃鳳鳥" +
  "灣塘蓮葉環衛電話機關爾貝萬豐寶來長沒時間實現師樂歲過還邊遠運達選聯網紅綠藍銀錢鎮隊陸險隨" +
  "雙麗麥齊齒龜灑氣漢滬滿濟濱灣燈獅產畫監碼礎禮稅窩競筆節範築簡糧紀約級統絲經緑綜維網緣總縣" +
  "義習聖臺舊與藝蘇處虎號術衝補裝見規視覽觀計記許設證評試詩誠語說請論諸謝識護讀變貨貴費資賓" +
  "賣質贈軍軟輕輪轉辦農這進遊道遺郵鄧鄭醫鐘鋼錦閣閩闊隆雞頂順須頤頭題願類顯風飛飯館馬駕騰驗" +
  "魚鮮鴻鹽麗黨齡羅徑暉濤綉輝潤廈榮荊壟濕昇澤譽頌強薈揚賀顏瑩軒紐晉輞匯繞圖籃適掃曉繡岡迴隴崗慶" +
  "橫靈創雋";
const SIMP =
  "湾楼东门华龙园广场车号桥头区岛滩岭书馆会国际学医体线铁乡庄围开发兴厂业云阳东陈刘黄凤鸟" +
  "湾塘莲叶环卫电话机关尔贝万丰宝来长没时间实现师乐岁过还边远运达选联网红绿蓝银钱镇队陆险随" +
  "双丽麦齐齿龟洒气汉沪满济滨湾灯狮产画监码础礼税窝竞笔节范筑简粮纪约级统丝经绿综维网缘总县" +
  "义习圣台旧与艺苏处虎号术冲补装见规视览观计记许设证评试诗诚语说请论诸谢识护读变货贵费资宾" +
  "卖质赠军软轻轮转办农这进游道遗邮邓郑医钟钢锦阁闽阔隆鸡顶顺须颐头题愿类显风飞饭馆马驾腾验" +
  "鱼鲜鸿盐丽党龄罗径晖涛绣辉润厦荣荆垄湿升泽誉颂强荟扬贺颜莹轩纽晋辋汇绕图篮适扫晓绣冈回陇岗庆" +
  "横灵创隽";

const TO_SIMPLIFIED: ReadonlyMap<string, string> = new Map(
  [...TRAD].map((ch, i) => [ch, [...SIMP][i]] as const).filter(([t, s]) => t !== s),
);

/** Folds the traditional characters above to simplified; leaves everything else alone. */
export function toSimplified(text: string): string {
  let out = "";
  for (const ch of text) out += TO_SIMPLIFIED.get(ch) ?? ch;
  return out;
}

/** True when the text holds any Han character. */
export const hasHan = (text: string): boolean => HAN.test(text);
