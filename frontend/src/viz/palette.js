/**
 * 可视化调色板（已用 dataviz 验证器实测通过，light 模式）。
 *
 * - 分类色：投标人身份用；按固定槽位顺序分配，绝不按排名重着色；超过 8 家用「其他」灰折叠
 * - 状态色：仅用于 通过/待复核/不通过 这类状态，且必须配文字标签（不能只靠颜色）
 * - 校验结果：分类前三槽 ALL PASS（aqua 对比度 2.74 WARN → 已配可见标签 + 表格视图兜底）
 *
 * 深色步骤已在参考调色板中验证，本期应用无深色主题，先只接 light。
 */

export const INK = {
  primary: '#0b0b0b',
  secondary: '#52514e',
  muted: '#898781',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
}

export const SURFACE = '#fcfcfb'

// 分类槽位（固定顺序）
export const CATEGORICAL = [
  '#2a78d6', // 1 blue
  '#eb6834', // 2 orange
  '#1baf7a', // 3 aqua
  '#eda100', // 4 yellow
  '#e87ba4', // 5 magenta
  '#008300', // 6 green
  '#4a3aa7', // 7 violet
  '#e34948', // 8 red
]
export const OTHER_COLOR = '#898781' // 第 9 家起折叠为「其他」

// 状态色（固定，不参与主题化）
export const STATUS = {
  passed: '#0ca30c', // good
  review: '#fab219', // warning
  failed: '#d03b3b', // critical
  pending: '#898781', // 未审 = 中性灰
}

export const STATE_LABELS = { passed: '通过', failed: '不通过', review: '待复核', pending: '未审' }

/**
 * 投标人 → 颜色映射。按投标人 id 升序固定分配槽位，
 * 保证筛选/换页后同一家颜色不变（颜色跟实体，不跟排名）。
 */
export function bidderColorMap(bidderIds, { cap = 8 } = {}) {
  const sorted = [...bidderIds].sort((a, b) => a - b)
  const map = new Map()
  sorted.forEach((id, i) => map.set(id, i < cap ? CATEGORICAL[i] : OTHER_COLOR))
  return map
}

/** 图例展示名（超过 cap 家时后段显示为「其他」） */
export function bidderLabel(name, index, cap = 8) {
  return index < cap ? name : '其他'
}
