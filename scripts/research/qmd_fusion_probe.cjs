// Run with a TypeScript compiler available via NODE_PATH. The extracted
// function is QMD's actual source; no models or store initialization run.
const fs = require('node:fs');
const ts = require('typescript');
const crypto = require('node:crypto');
const path = process.argv[2];
if (!path) throw new Error('usage: node qmd_fusion_probe.cjs /path/to/pinned/store.ts');
const source = fs.readFileSync(path, 'utf8');
const ast = ts.createSourceFile('store.ts', source, ts.ScriptTarget.Latest, true);
let fn;
const constants = [];
let gate;
function walk(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === 'reciprocalRankFusion') fn = node.getText(ast);
  if (ts.isVariableDeclaration(node)) {
    const name = node.name.getText(ast);
    if (['STRONG_SIGNAL_MIN_SCORE', 'STRONG_SIGNAL_MIN_GAP'].includes(name)) {
      constants.push(`const ${node.getText(ast)};`);
    }
    if (name === 'hasStrongSignal') gate = `const ${node.getText(ast)};`;
  }
  ts.forEachChild(node, walk);
}
walk(ast);
if (!fn || !gate || constants.length !== 2) throw new Error('pinned source shape changed');
const compiled = ts.transpileModule(fn, {compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022
}}).outputText;
const exported = {};
new Function('exports', compiled)(exported);
const row = (file, score) => ({file, score});
const original = [row('exact.md', 0.96), row('neighbor.md', 0.2)];
const expansion = [row('neighbor.md', 0.9)];
const strongGate = new Function('initialFts', 'intent',
  `${constants.join('\n')} const topScore = initialFts[0]?.score ?? 0;
   const secondScore = initialFts[1]?.score ?? 0; ${gate} return hasStrongSignal;`);
const originalOnly = exported.reciprocalRankFusion([original], [2]);
if (Math.abs(originalOnly[0].score - (2 / 61 + 0.05)) > 1e-12) {
  throw new Error('replay did not preserve the pinned top-rank bonus');
}
console.log(JSON.stringify({
  scope: 'actual RRF function and strong-signal predicate; not the full hybrid pipeline',
  source_sha256: crypto.createHash('sha256').update(source).digest('hex'),
  original_only: originalOnly,
  two_expansions: exported.reciprocalRankFusion([original, expansion, expansion], [2, 1, 1]),
  strong_query_skips_expansion: strongGate(original, undefined),
  explicit_intent_skips_expansion: strongGate(original, 'different context'),
  ambiguous_query_skips_expansion: strongGate([row('exact.md', 0.74), row('neighbor.md', 0.73)], undefined)
}, null, 2));
