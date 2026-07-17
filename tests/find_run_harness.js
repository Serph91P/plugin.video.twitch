#!/usr/bin/env node
"use strict";
// Harness that executes the find-run JavaScript from make-release.yml
// with mocked github, context, and core objects.
// Usage: node find_run_harness.js <scenario.json>
// Scenario format: { "script": "...", "context": {...}, "apiCalls": [...] }
// Output: JSON to stdout with { "outputs": {...}, "failed": bool, "failureMessage": str, "apiLog": [...] }

const fs = require("fs");
const os = require("os");
const path = require("path");

const scenarioPath = process.argv[2];
if (!scenarioPath) {
  process.stderr.write("Usage: node find_run_harness.js <scenario.json>\n");
  process.exit(1);
}

const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf-8"));
const script = scenario.script;
const ctx = scenario.context;

// Build API call log and response map
const apiLog = [];
const responses = scenario.apiCalls || [];

let responseIdx = 0;

const github = {
  rest: {
    actions: {
      listWorkflowRunsForRepo: async function (params) {
        apiLog.push({
          method: "listWorkflowRunsForRepo",
          params: { ...params },
        });
        if (responseIdx < responses.length) {
          const resp = responses[responseIdx];
          responseIdx++;
          return resp;
        }
        return { data: { workflow_runs: [] } };
      },
    },
  },
};

const context = {
  repo: { owner: ctx.owner || "test-owner", repo: ctx.repo || "test-repo" },
  ref: ctx.ref || "refs/tags/v1.0.0",
  sha: ctx.sha || "abc123",
};

const outputs = {};
const core = {
  setOutput: function (name, value) {
    outputs[name] = value;
  },
  setFailed: function (message) {
    outputs.__failed = true;
    outputs.__failureMessage = message;
  },
};

// Write script to a temp file and dynamically import it to preserve async/await
const tmpFile = path.join(os.tmpdir(), `find_run_test_${process.pid}.mjs`);
try {
  const wrappedScript = `
    export async function run(github, context, core) {
      ${script}
    }
  `;
  fs.writeFileSync(tmpFile, wrappedScript);

  (async () => {
    try {
      // Clear the module cache to ensure fresh import
      delete require.cache[tmpFile];
      const mod = await import(tmpFile);
      await mod.run(github, context, core);
    } catch (err) {
      outputs.__error = err.message;
    } finally {
      try { fs.unlinkSync(tmpFile); } catch (e) {}
    }

    const result = {
      outputs,
      failed: !!outputs.__failed,
      failureMessage: outputs.__failureMessage || null,
      error: outputs.__error || null,
      apiLog,
    };

    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  })();
} catch (err) {
  try { fs.unlinkSync(tmpFile); } catch (e) {}
  throw err;
}
