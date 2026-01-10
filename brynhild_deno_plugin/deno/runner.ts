// deno/runner.ts
//
// Minimal "Python over stdin/stdout" runner implemented as a long-lived process:
// - Reads newline-delimited JSON requests from stdin
// - Executes Python using Pyodide (WASM)
// - Writes one JSON response per request to stdout
//
// Designed to be spawned by a Brynhild tool and reused across calls.
//
// Security:
// - Loads Pyodide from vendored local files (no network access needed)
// - Run with: deno run --no-remote --unstable-detect-cjs --allow-read=<plugin_root> runner.ts

// @ts-types="../vendor/pyodide/pyodide.d.ts"
import { loadPyodide } from "../vendor/pyodide/pyodide.mjs";

type Request = {
  code?: string;
  files?: Record<string, string>;
  packages?: string[];
  pythonpath?: string[];
  shutdown?: boolean;
};

type Response =
  | {
      ok: true;
      stdout: string;
      stderr: string;
      result: string | null;
      error: null;
    }
  | {
      ok: false;
      stdout: string;
      stderr: string;
      result: null;
      error: string;
    };

function sanitizeWorkPath(p: string): string {
  // Force all user file paths into /work and reject path traversal.
  let s = p.replaceAll("\\", "/").trim();
  while (s.startsWith("/")) s = s.slice(1);
  const parts = s.split("/").filter((x) => x.length > 0);
  for (const part of parts) {
    if (part === "." || part === "..") {
      throw new Error(`Invalid path segment: ${part}`);
    }
  }
  return "/work/" + parts.join("/");
}

// deno-lint-ignore no-explicit-any
function ensureDir(pyodide: any, absPath: string): void {
  // Create parent directories for an absolute path inside the Pyodide FS.
  const idx = absPath.lastIndexOf("/");
  const dir = idx <= 0 ? "/" : absPath.slice(0, idx);
  if (dir === "/") return;

  const segments = dir.split("/").filter((x) => x.length > 0);
  let cur = "";
  for (const seg of segments) {
    cur += "/" + seg;
    try {
      pyodide.FS.mkdir(cur);
    } catch (_e) {
      // ignore EEXIST
    }
  }
}

function buildPythonWrapper(code: string, pythonpath: string[]): string {
  // P0-B: Use base64 encoding to avoid string delimiter issues.
  // Raw triple-quotes (r'''...''') break if code contains ''' itself.
  // Base64 is safe for any content including triple quotes, backslashes, etc.
  
  // Handle Unicode: encode to UTF-8 bytes, then base64
  const encoder = new TextEncoder();
  const codeBytes = encoder.encode(code);
  const pathBytes = encoder.encode(JSON.stringify(pythonpath ?? []));
  
  // Convert Uint8Array to base64
  const codeB64 = btoa(String.fromCharCode(...codeBytes));
  const pathB64 = btoa(String.fromCharCode(...pathBytes));

  // Capture stdout/stderr and (REPL-like) final expression value.
  // Returns a JSON string as the final expression.
  return `
import ast, base64, contextlib, io, json, sys, traceback

_code = base64.b64decode("${codeB64}").decode("utf-8")
_pythonpath = json.loads(base64.b64decode("${pathB64}").decode("utf-8"))

_stdout = io.StringIO()
_stderr = io.StringIO()
_result = None
_ok = True
_err = None

_globals = globals().get("__BRYNHILD_USER_GLOBALS__")
if _globals is None:
    _globals = {}
    globals()["__BRYNHILD_USER_GLOBALS__"] = _globals

try:
    with contextlib.redirect_stdout(_stdout), contextlib.redirect_stderr(_stderr):
        for p in _pythonpath:
            if p and p not in sys.path:
                sys.path.insert(0, p)

        tree = ast.parse(_code, mode="exec")
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body.pop()
            exec(compile(tree, "<sandbox>", "exec"), _globals, _globals)
            _result = eval(compile(ast.Expression(last.value), "<sandbox>", "eval"), _globals, _globals)
        else:
            exec(compile(tree, "<sandbox>", "exec"), _globals, _globals)

    _ok = True
except Exception:
    _ok = False
    _err = traceback.format_exc()

json.dumps({
    "ok": _ok,
    "stdout": _stdout.getvalue(),
    "stderr": _stderr.getvalue(),
    "result": (repr(_result) if _ok else None),
    "error": (_err if not _ok else None),
})
`;
}

async function* iterLines(stream: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  let buf = "";
  for await (const chunk of stream) {
    buf += decoder.decode(chunk, { stream: true });
    while (true) {
      const idx = buf.indexOf("\n");
      if (idx === -1) break;
      const line = buf.slice(0, idx).replace(/\r$/, "");
      buf = buf.slice(idx + 1);
      yield line;
    }
  }
  // Flush final partial line (if any)
  if (buf.length > 0) yield buf;
}

async function main() {
  // Load Pyodide from vendored local files
  // indexURL is auto-detected from import location (../vendor/pyodide/pyodide.mjs)
  const pyodide = await loadPyodide();

  // Ensure /work exists and set it as working directory.
  try {
    pyodide.FS.mkdir("/work");
  } catch (_e) {
    // ignore
  }
  try {
    pyodide.FS.chdir("/work");
  } catch (_e) {
    // ignore
  }

  for await (const rawLine of iterLines(Deno.stdin.readable)) {
    const line = rawLine.trim();
    if (!line) continue;

    let req: Request;
    try {
      req = JSON.parse(line);
    } catch (e) {
      const resp: Response = {
        ok: false,
        stdout: "",
        stderr: "",
        result: null,
        error: `Invalid JSON input: ${(e as Error).message}`,
      };
      console.log(JSON.stringify(resp));
      continue;
    }

    if (req?.shutdown) {
      break;
    }

    const code = typeof req?.code === "string" ? req.code : "";
    const packages = Array.isArray(req?.packages) ? req.packages : [];
    const pythonpath = Array.isArray(req?.pythonpath) ? req.pythonpath : [];
    const files = req?.files && typeof req.files === "object" ? req.files : null;

    try {
      // Load requested packages (if present in the Pyodide distribution).
      if (packages.length > 0) {
        await pyodide.loadPackage(packages);
      }

      // Write provided files into /work.
      if (files) {
        const encoder = new TextEncoder();
        for (const [p, content] of Object.entries(files)) {
          const abs = sanitizeWorkPath(p);
          ensureDir(pyodide, abs);
          pyodide.FS.writeFile(abs, encoder.encode(String(content)));
        }
      }

      // Always run in /work.
      try {
        pyodide.FS.chdir("/work");
      } catch (_e) {
        // ignore
      }

      const wrapper = buildPythonWrapper(code, pythonpath);
      const raw = await pyodide.runPythonAsync(wrapper);
      const payload = JSON.parse(raw.toString());

      // payload already matches Response shape
      console.log(JSON.stringify(payload));
    } catch (e) {
      const resp: Response = {
        ok: false,
        stdout: "",
        stderr: "",
        result: null,
        error: (e as Error).message ?? String(e),
      };
      console.log(JSON.stringify(resp));
    }
  }
}

await main();

