// Copies static client assets (HTML/CSS) into dist/client alongside the
// tsc-compiled browser JavaScript. Kept as a tiny script instead of a
// bundler/copy dependency to keep the dependency surface minimal.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const srcPublic = path.join(scriptDir, '..', 'src', 'client', 'public');
const destDir = path.join(scriptDir, '..', 'dist', 'client');

fs.mkdirSync(destDir, { recursive: true });
fs.cpSync(srcPublic, destDir, { recursive: true });

// dist/client holds plain ES module output from tsconfig.client.json (the
// browser loads it via <script type="module">, which doesn't care about
// this file). This package.json exists so Node's own module loader also
// treats these .js files as ESM when a server-side test dynamically
// imports one directly (see src/server/__tests__/client-api-delete-media.test.ts,
// which imports the compiled client api.js to test its fetch-wrapping
// behavior without duplicating the logic server-side).
fs.writeFileSync(path.join(destDir, 'package.json'), JSON.stringify({ type: 'module' }, null, 2) + '\n');

console.log(`Copied static client assets from ${srcPublic} to ${destDir}`);
