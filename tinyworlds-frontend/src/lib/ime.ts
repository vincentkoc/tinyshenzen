/**
 * True while an input method (pinyin, zhuyin, kana) is still composing: the Enter or Escape then
 * belongs to the IME, which uses it to pick or drop a candidate, not to the search box. Safari
 * reports the composing keydown as keyCode 229 without setting isComposing.
 */
export function isComposing(e: {
  nativeEvent: { isComposing?: boolean };
  keyCode?: number;
}): boolean {
  return e.nativeEvent.isComposing === true || e.keyCode === 229;
}
