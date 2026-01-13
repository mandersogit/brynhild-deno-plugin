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
  // P1-2.1: Use _LimitedStringIO to bound output at source (prevents memory exhaustion)
  return `
import ast, base64, contextlib, io, json, sys, traceback

_code = base64.b64decode("${codeB64}").decode("utf-8")
_pythonpath = json.loads(base64.b64decode("${pathB64}").decode("utf-8"))

# P1-2.1: Bounded output buffer to prevent memory exhaustion
# Limit set to 10000 chars (less than python_sandbox._max_output_chars=12000)
# so the truncation message remains visible after Python-side truncation
class _LimitedStringIO:
    def __init__(self, limit=10000):
        self._buf = []
        self._len = 0
        self._limit = limit
        self._truncated = False

    def write(self, s):
        if self._len >= self._limit:
            self._truncated = True
            return len(s)  # Pretend we wrote it
        take = min(len(s), self._limit - self._len)
        self._buf.append(s[:take])
        self._len += take
        if take < len(s):
            self._truncated = True
        return len(s)

    def getvalue(self):
        result = "".join(self._buf)
        if self._truncated:
            result += "\\n... (truncated at source, limit 10000 chars)"
        return result

    def flush(self):
        pass  # Required for redirect_stdout compatibility

_stdout = _LimitedStringIO(10000)
_stderr = _LimitedStringIO(10000)
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

  // P1-2.1: Request size limit to prevent memory exhaustion
  const MAX_REQUEST_SIZE = 1_000_000; // 1MB

  for await (const rawLine of iterLines(Deno.stdin.readable)) {
    const line = rawLine.trim();
    if (!line) continue;

    // P1-2.1: Reject oversized requests before parsing
    if (line.length > MAX_REQUEST_SIZE) {
      const resp: Response = {
        ok: false,
        stdout: "",
        stderr: "",
        result: null,
        error: `Request too large (${line.length} bytes, max ${MAX_REQUEST_SIZE})`,
      };
      console.log(JSON.stringify(resp));
      continue;
    }

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

    // P1-2.5: File injection limits
    const MAX_FILES = 100;
    const MAX_FILE_SIZE = 1_000_000; // 1MB per file
    const MAX_TOTAL_SIZE = 10_000_000; // 10MB total

    // Validate and pre-encode files (encode once, use for both validation and writing)
    let fileError: string | null = null;
    const encodedFiles: Array<{ path: string; absPath: string; data: Uint8Array }> = [];
    
    if (files) {
      const fileEntries = Object.entries(files);
      if (fileEntries.length > MAX_FILES) {
        fileError = `Too many files (${fileEntries.length}, max ${MAX_FILES})`;
      } else {
        let totalSize = 0;
        const encoder = new TextEncoder();
        for (const [path, content] of fileEntries) {
          if (!path || path.length === 0) {
            fileError = "Empty file path";
            break;
          }
          
          let absPath: string;
          try {
            absPath = sanitizeWorkPath(path);
          } catch (e) {
            fileError = (e as Error).message;
            break;
          }
          
          const data = encoder.encode(String(content));
          if (data.length > MAX_FILE_SIZE) {
            fileError = `File '${path}' too large (${data.length} bytes, max ${MAX_FILE_SIZE})`;
            break;
          }
          totalSize += data.length;
          encodedFiles.push({ path, absPath, data });
        }
        if (!fileError && totalSize > MAX_TOTAL_SIZE) {
          fileError = `Total file size too large (${totalSize} bytes, max ${MAX_TOTAL_SIZE})`;
        }
      }
    }

    if (fileError) {
      const resp: Response = {
        ok: false,
        stdout: "",
        stderr: "",
        result: null,
        error: fileError,
      };
      console.log(JSON.stringify(resp));
      continue;
    }

    try {
      // Load requested packages (if present in the Pyodide distribution).
      if (packages.length > 0) {
        await pyodide.loadPackage(packages);
      }

      // Write pre-validated files into /work (already encoded, no double-encoding)
      for (const { absPath, data } of encodedFiles) {
        ensureDir(pyodide, absPath);
        pyodide.FS.writeFile(absPath, data);
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

