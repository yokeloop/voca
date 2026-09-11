import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const root = path.resolve("src");

function imports(file) {
  const source = ts.createSourceFile(
    file,
    fs.readFileSync(file, "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  return source.statements.flatMap((statement) => {
    if (
      (ts.isImportDeclaration(statement) ||
        ts.isExportDeclaration(statement)) &&
      statement.moduleSpecifier &&
      ts.isStringLiteral(statement.moduleSpecifier)
    )
      return [statement.moduleSpecifier.text];
    return [];
  });
}

function files(directory) {
  return fs
    .readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) =>
      entry.isDirectory()
        ? files(path.join(directory, entry.name))
        : [path.join(directory, entry.name)],
    );
}

test("app enters the page through its public API and page API stays independent", () => {
  const app = path.join(root, "app/App.tsx");
  assert.deepEqual(imports(app), ["../pages/session-console"]);
  for (const file of files(path.join(root, "pages/session-console/api"))) {
    assert.equal(
      imports(file).some(
        (specifier) =>
          specifier.includes("/model/") ||
          specifier.includes("/ui/") ||
          specifier.includes("/app/"),
      ),
      false,
      file,
    );
  }
  for (const file of files(root)) {
    if (
      !file.includes(`${path.sep}pages${path.sep}session-console${path.sep}`) &&
      file !== app
    ) {
      assert.equal(
        imports(file).some(
          (specifier) =>
            specifier.includes("pages/session-console/") &&
            !specifier.endsWith("session-console"),
        ),
        false,
        file,
      );
    }
  }
  const ui = fs.readFileSync(
    path.join(root, "pages/session-console/ui/SessionConsole.tsx"),
    "utf8",
  );
  assert.equal(/\b(fetch|WebSocket|localStorage)\b/.test(ui), false);
  const owners = files(root).filter((file) =>
    fs.readFileSync(file, "utf8").includes("new WebSocket"),
  );
  assert.deepEqual(
    owners.map((file) => path.relative(root, file)),
    ["pages/session-console/api/control-connection.ts"],
  );
});
