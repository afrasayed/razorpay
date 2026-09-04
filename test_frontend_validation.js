const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'static', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');

console.log("=== 1. Checking JS Syntax in static/index.html ===");
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/i);
if (!scriptMatch) {
  console.error("ERROR: No <script> tag found!");
  process.exit(1);
}

const jsCode = scriptMatch[1];
try {
  new Function(jsCode);
  console.log("✓ JS syntax is valid!");
} catch (e) {
  console.error("✗ JS syntax error:", e.message);
  process.exit(1);
}

console.log("\n=== 2. Checking Element IDs referenced in JS against HTML ===");
const idRegex = /document\.getElementById\(['"]([^'"]+)['"]\)/g;
let match;
const referencedIds = new Set();
while ((match = idRegex.exec(jsCode)) !== null) {
  referencedIds.add(match[1]);
}

const querySelectorRegex = /querySelector\(['"]#([^'"]+)['"]\)/g;
while ((match = querySelectorRegex.exec(jsCode)) !== null) {
  referencedIds.add(match[1]);
}

let missingIds = [];
referencedIds.forEach(id => {
  // Check if ID exists in HTML
  const hasId = html.includes(`id="${id}"`) || html.includes(`id='${id}'`);
  if (!hasId) {
    missingIds.push(id);
  }
});

console.log("Referenced IDs count:", referencedIds.size);
if (missingIds.length > 0) {
  console.warn("⚠️ Warning: Missing IDs in HTML:", missingIds);
} else {
  console.log("✓ All referenced IDs exist in HTML!");
}

console.log("\n=== 3. Checking Function Declarations in JS ===");
const requiredFunctions = [
  'switchTab',
  'addCustomerOrderItem',
  'processCustomerOrder',
  'loadCustomerOrders',
  'checkInventoryThreshold',
  'triggerAutoRestock',
  'loadInventory',
  'loadCatalog',
  'checkout',
  'renderResult',
  'loadAudit',
  'toggleCatalog',
  'loadRestockOrders',
  'handleRestockAction',
  'approveHeldOrder'
];

let allFunctionsPresent = true;
requiredFunctions.forEach(fn => {
  const hasFn = jsCode.includes(`function ${fn}`) || jsCode.includes(`async function ${fn}`);
  if (hasFn) {
    console.log(`✓ Function ${fn} defined`);
  } else {
    console.error(`✗ Missing function ${fn}`);
    allFunctionsPresent = false;
  }
});

if (!allFunctionsPresent) process.exit(1);
console.log("\n✓ All validation checks passed!");
