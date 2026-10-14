const TreeSitter = require('tree-sitter');
const Python = require('tree-sitter-python');
const fs = require('fs').promises;

async function parseCode(codePath) {
  const parser = new TreeSitter();
  parser.setLanguage(Python);
  const codeContent = await fs.readFile(codePath, 'utf8');
  const tree = parser.parse(codeContent);
  return tree.rootNode;
}

async function printASTNodeInfo(rootNode) {
  console.log(`Node type: ${rootNode.type}`);
  for (const child of rootNode.children) {
    console.log(` - Child node type: ${child.type}`);
    if (child.children.length > 0) {
      await printASTNodeInfo(child);
    } else {
      if (child.text) {
        console.log(` - Text content: ${child.text}`);
      }
    }
  }
}

async function main(codePath) {
  try {
    const rootNode = await parseCode(codePath);
    await printASTNodeInfo(rootNode);
  } catch (error) {
    console.error(`Failed to parse code: ${error.message}`);
  }
}

main(process.argv[2]);