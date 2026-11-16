import fs from 'node:fs';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
const out = path.join(root, 'capture', 'out');
const gen = path.join(root, 'src', 'generated');
fs.mkdirSync(gen, { recursive: true });
const layout = JSON.parse(fs.readFileSync(path.join(out, 'layout.json'), 'utf8'));
fs.writeFileSync(path.join(gen, 'layout.json'), JSON.stringify(layout, null, 1));
const seqRoot = path.join(root, 'public', 'textures', 'seq');
const seq = {};
if (fs.existsSync(seqRoot)) {
  for (const name of fs.readdirSync(seqRoot)) {
    const dir = path.join(seqRoot, name);
    const timesPath = path.join(dir, 'times.json');
    if (!fs.existsSync(timesPath)) continue;
    const times = JSON.parse(fs.readFileSync(timesPath, 'utf8'));
    const files = fs.readdirSync(dir).filter((f) => /\.(jpg|png)$/.test(f)).sort();
    seq[name] = { times, files: files.length };
  }
}
fs.writeFileSync(path.join(gen, 'seq.json'), JSON.stringify(seq, null, 1));
console.log('generated pages:', Object.keys(layout.pages).length, 'elements:', layout.elements.length, 'seq:', Object.keys(seq));