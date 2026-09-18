/**
 * 模板作用域检查：用 @vue/compiler-sfc 编译每个 SFC，
 * 扫出模板里引用但 <script setup> 未定义的标识符。
 *
 * 这类 bug（如模板写 router.push 但只 import 了 useRoute）vite build 不会报错，
 * 运行时点击才抛 ReferenceError —— 本工具用于构建前拦截。
 *
 * 用法（在 node 环境，需 @vue/compiler-sfc）：
 *   node scripts/check-vue-templates.mjs <srcDir>
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { parse, compileScript } from '@vue/compiler-sfc'

const IGNORE = new Set([
  '$router', '$route', '$event', '$slots', '$attrs', '$refs', '$emit', '$props', '$el',
  'true', 'false', 'null', 'undefined', 'Math', 'Date', 'JSON', 'Object', 'Array', 'String',
  'Number', 'Boolean', 'console', 'window', 'document', 'localStorage', 'parseInt', 'parseFloat',
  'setTimeout', 'clearTimeout', 'clearInterval', 'setInterval', 'encodeURIComponent', 'decodeURIComponent',
])

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (p.endsWith('.vue')) out.push(p)
  }
  return out
}

// 已知的运行时陷阱：构建期不报错，只在点击/交互时才出问题
const RISKY_PATTERNS = [
  {
    // Element Plus 的 el-radio / el-radio-button 把 null|undefined 当作「未提供值」，
    // 会回退去读 label 属性；若文字写在插槽里，点击后 emit 的是 undefined
    // （典型表现：点「全部」筛选后列表全空）。
    re: /<el-radio(?:-button)?[^>]*:value="(null|undefined)"/g,
    msg: 'el-radio 的 :value 用了 null/undefined —— Element Plus 会回退到 label 属性，点击后拿到 undefined',
  },
]

function checkRiskyPatterns(source) {
  const hits = []
  for (const { re, msg } of RISKY_PATTERNS) {
    const found = source.match(re)
    if (found) hits.push(`${msg}（${found.length} 处）`)
  }
  return hits
}

const srcDir = process.argv[2] || 'src'
let problems = 0

for (const file of walk(srcDir)) {
  const source = readFileSync(file, 'utf-8')
  const risky = checkRiskyPatterns(source)
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) {
    console.log(`✗ ${file}: 解析失败 ${errors.map((e) => e.message).join('; ')}`)
    problems++
    continue
  }
  if (!descriptor.scriptSetup) {
    risky.forEach((r) => {
      console.log(`✗ ${file}: ${r}`)
      problems++
    })
    continue
  }

  let compiled
  try {
    compiled = compileScript(descriptor, { id: file, inlineTemplate: true })
  } catch (e) {
    console.log(`✗ ${file}: 编译失败 ${e.message}`)
    problems++
    continue
  }

  const code = compiled.content
  const bad = new Set()
  // 未解析的模板标识符会被编译成 _ctx.xxx
  for (const m of code.matchAll(/_ctx\.([A-Za-z_$][\w$]*)/g)) {
    const name = m[1]
    if (!IGNORE.has(name)) bad.add(name)
  }
  if (bad.size) {
    console.log(`✗ ${file}: 模板引用了未定义的标识符 → ${[...bad].join(', ')}`)
    problems++
  } else if (risky.length) {
    risky.forEach((r) => {
      console.log(`✗ ${file}: ${r}`)
      problems++
    })
  } else {
    console.log(`✓ ${file}`)
  }
}

console.log(problems ? `\n发现 ${problems} 个文件存在模板作用域问题` : '\n全部 SFC 模板作用域正常')
process.exit(problems ? 1 : 0)
