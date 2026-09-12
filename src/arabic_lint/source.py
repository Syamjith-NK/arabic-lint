"""Find the pre-shaping recipe in Python source, and judge whether it is a bug.

`detect.py` finds Arabic that was already corrupted and stored. This finds the code
that will corrupt it at render time, before anything has been stored at all.

The hard part is not finding the pattern. A grep for `get_display(reshape(` returns
thousands of files and almost all of that noise, because **whether the recipe is
wrong depends entirely on what draws the text**:

    matplotlib >= 3.11   shapes and runs bidi itself  -> pre-shaping REVERSES the text
    Pillow with Raqm     shapes and runs bidi itself  -> pre-shaping REVERSES the text
    Pillow without Raqm  does neither                 -> pre-shaping is REQUIRED
    ReportLab            does neither                 -> pre-shaping is REQUIRED
    print() to a tty     terminal-dependent           -> not our call

So a checker that flags every occurrence is worse than useless: it trains people to
ignore it. This one reports only where a shaping renderer is actually imported, and
stays silent everywhere else.

It also stays silent on a pre-shaping helper that is **defined but never called**.
That is not a hypothetical: it is the single most common false positive in the wild.

Standard library only. `ast`, no regex heuristics, no dependencies.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

RESHAPERS = {"reshape", "ArabicReshaper"}
BIDI = {"get_display"}

# Text-drawing calls. Importing matplotlib or PIL proves nothing on its own: a file
# can import Pillow to *load* an image and then print() the Arabic to a terminal,
# which is not our business. Something must actually draw.
MPL_CALLS = {
    "title", "suptitle", "xlabel", "ylabel", "set_title", "set_xlabel", "set_ylabel",
    "set_xticklabels", "set_yticklabels", "annotate", "legend", "bar_label",
}
PIL_CALLS = {"multiline_text", "Draw"}
AMBIGUOUS_CALLS = {"text"}                       # both libraries spell it `text`
DRAWING_CALLS = MPL_CALLS | PIL_CALLS | AMBIGUOUS_CALLS

# Modules whose text APIs shape and reorder complex scripts themselves. Handing
# them a pre-shaped string means the work is done twice.
SHAPING_SINKS = {
    "matplotlib": "matplotlib >= 3.11 shapes text with libraqm and applies bidi itself",
    "PIL": "Pillow built with Raqm shapes text and applies bidi itself",
}

# Modules that do no shaping and no bidi. The recipe is correct for these, and
# reporting them is how a linter loses its users.
PASSIVE_SINKS = {"reportlab", "fpdf", "fpdf2"}


@dataclass
class SourceFinding:
    line: int
    col: int
    snippet: str
    sink: str          # "matplotlib" | "PIL" | "unknown"
    reason: str
    confidence: str    # "high" | "conditional"
    fix: str | None = None        # replacement source for this expression, if safe
    unfixable_why: str | None = None
    end_line: int = 0             # full span of the call, for applying the rewrite
    end_col: int = 0

    def format(self, path: str) -> str:
        head = f"{path}:{self.line}:{self.col}: pre-shaped text passed to {self.sink}"
        if self.confidence == "conditional":
            head += "  [CONDITIONAL]"
        return f"{head}\n    {self.snippet}\n    {self.reason}"


@dataclass
class SourceReport:
    findings: list[SourceFinding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # why we stayed quiet

    @property
    def ok(self) -> bool:
        return not self.findings


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        # Local names bound to the reshaper / bidi entry points. Real code aliases
        # them (`from bidi.algorithm import get_display as _bidi_get_display`), and a
        # checker that only knows the canonical spelling silently passes the file.
        self.bidi_names: set[str] = set(BIDI)
        self.reshape_names: set[str] = set(RESHAPERS)
        self.referenced: set[str] = set()
        self.drawing: set[str] = set()
        # Variables holding the output of reshape(). The two halves of the recipe are
        # very often on separate lines:
        #     reshaped  = arabic_reshaper.reshape(segment)
        #     processed = get_display(reshaped)
        # Looking only inside the get_display() call misses every one of those.
        self.reshaped_vars: set[str] = set()
        self.preshape_calls: list[ast.Call] = []
        # name -> node, for helpers like `def arab(t): return get_display(reshape(t))`
        self.preshape_funcs: dict[str, ast.FunctionDef] = {}
        self.called_names: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.imports.add(a.name.split(".")[0])
            if a.asname and a.name.split(".")[0] in {"arabic_reshaper"}:
                self.reshape_names.add(a.asname)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".")[0]
            self.imports.add(root)
            for a in node.names:
                if a.name in BIDI and a.asname:
                    self.bidi_names.add(a.asname)
                if a.name in RESHAPERS and a.asname:
                    self.reshape_names.add(a.asname)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.referenced.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.referenced.add(node.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if _contains_preshape(node):
            self.preshape_funcs[node.name] = node
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if _contains_reshape(node.value, self.reshape_names):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.reshaped_vars.add(t.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee(node)
        if name:
            self.called_names.add(name)
            if name in DRAWING_CALLS:
                self.drawing.add(name)
        if name in self.bidi_names and (
            _contains_reshape(node, self.reshape_names)
            or any(isinstance(a, ast.Name) and a.id in self.reshaped_vars for a in node.args)
        ):
            self.preshape_calls.append(node)
        self.generic_visit(node)


def _callee(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _contains_reshape(node: ast.AST, names: set[str] | None = None) -> bool:
    names = names or RESHAPERS
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _callee(sub) in names:
            return True
    return False


def _contains_preshape(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _callee(sub) in BIDI and _contains_reshape(sub):
            return True
    return False


def _snippet(lines: list[str], lineno: int) -> str:
    try:
        return lines[lineno - 1].strip()[:110]
    except IndexError:
        return ""


def scan_source(text: str) -> SourceReport:
    """Scan one Python source file. Never raises on unparseable input."""
    report = SourceReport()
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        report.skipped.append(f"could not parse: {exc.msg}")
        return report

    v = _Visitor()
    v.visit(tree)
    if not v.preshape_calls:
        return report

    lines = text.splitlines()

    # Which renderer is in play? A file can import both; report the shaping ones.
    shaping = sorted(v.imports & set(SHAPING_SINKS))
    passive = sorted(v.imports & PASSIVE_SINKS)

    if not shaping:
        if passive:
            report.skipped.append(
                f"pre-shaping found, but the only renderer imported is {', '.join(passive)}, "
                "which does no shaping of its own. The recipe is correct here."
            )
        else:
            report.skipped.append(
                "pre-shaping found, but no shaping renderer is imported in this file. "
                "Nothing to say without knowing what draws the text."
            )
        return report

    # Order matters: classify the renderer first, then ask whether anything draws.
    # Checking "does it draw" first made the ReportLab branch unreachable and reported
    # a correct file with a misleading reason.
    if not v.drawing:
        report.skipped.append(
            "pre-shaping found and a shaping renderer is imported, but nothing in this "
            "file draws text with it. Importing Pillow to load an image and then "
            "print()ing the Arabic is not this tool's business."
        )
        return report

    # A helper that is defined and never called cannot corrupt anything. This is the
    # commonest false positive in real repositories: the import and the helper are
    # left behind after the code that used them was deleted.
    live_calls = []
    for call in v.preshape_calls:
        enclosing = _enclosing_func(tree, call)
        # Conservative on purpose. A helper invoked as `self.f()`, passed by name, or
        # called from another module is NOT dead, and a checker that guesses wrong here
        # goes quiet on a real bug. Only a name that appears nowhere outside its own
        # def counts as dead.
        if enclosing and enclosing not in v.referenced and _is_script(tree):
            report.skipped.append(
                f"{enclosing}() pre-shapes but is never called in this file (dead code)"
            )
            continue
        live_calls.append(call)

    # Which renderer, when a file imports both? Decide on the drawing calls seen, not
    # on alphabetical order, or a matplotlib bug gets reported against Pillow.
    if len(shaping) == 1:
        sink = shaping[0]
    elif v.drawing & MPL_CALLS:
        sink = "matplotlib"
    elif v.drawing & PIL_CALLS:
        sink = "PIL"
    else:
        sink = shaping[0]

    for call in live_calls:
        fix, why = _plan_fix(call, v, text)
        report.findings.append(
            SourceFinding(
                line=call.lineno,
                col=call.col_offset + 1,
                snippet=_snippet(lines, call.lineno),
                sink=sink,
                reason=SHAPING_SINKS[sink]
                + ", so this reorders an already-reordered string and the text renders reversed",
                # Neither condition is knowable from source alone: the installed
                # matplotlib version, and whether Pillow was built with Raqm.
                confidence="conditional",
                fix=fix,
                unfixable_why=why,
                end_line=getattr(call, "end_lineno", call.lineno),
                end_col=getattr(call, "end_col_offset", 0) + 1,
            )
        )
    return report


def _plan_fix(call: ast.Call, v: "_Visitor", text: str) -> tuple[str | None, str | None]:
    """Can this one call be rewritten mechanically, and if not, why not?

    Safe:   get_display(reshape(EXPR))   ->   EXPR
            Both halves of the recipe are inside this expression, so removing the
            expression removes the whole recipe. Nothing else refers to the result.

    NOT safe:  reshaped = reshape(x)  /  out = get_display(reshaped)
            Rewriting the second line to `out = reshaped` deletes the bidi half and
            LEAVES the shaping half applied. That is still wrong, and it is wrong in
            a way that looks fixed. The two lines have to go together, and which
            other code reads `reshaped` is not knowable from this expression.
    """
    if len(call.args) != 1:
        return None, "the call does not take exactly one argument"
    arg = call.args[0]

    # split-statement form: the argument is a variable assigned from reshape()
    if isinstance(arg, ast.Name) and arg.id in v.reshaped_vars:
        return None, (f"reshape() is applied on an earlier line to `{arg.id}`. Removing only "
                      "this call would leave the text shaped but not reordered, which is still "
                      "wrong. Delete both lines and pass the original string, or gate them "
                      "on the renderer version if you cannot require one.")

    inner = arg if isinstance(arg, ast.Call) and _callee(arg) in v.reshape_names else None
    if inner is None:
        return None, "the argument is not a direct reshape() call"
    if len(inner.args) != 1:
        return None, "reshape() does not take exactly one argument"

    try:
        replacement = ast.get_source_segment(text, inner.args[0])
    except Exception:
        replacement = None
    if not replacement:
        return None, "could not recover the original expression text"
    return replacement, None


def _is_script(tree: ast.AST) -> bool:
    """Does this module DO anything at import time, or is it only definitions?

    An unreferenced helper in a script is dead: nothing else imports a script. The
    same helper in a library module is almost certainly called from another file, and
    treating it as dead there is a false negative on real library code, which is the
    more expensive mistake.
    """
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                                 ast.AnnAssign, ast.Expr)):
            return True
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return True   # a bare top-level call is a script doing work
    return False


def _enclosing_func(tree: ast.AST, target: ast.AST) -> str | None:
    """Name of the function a node sits in, or None at module level."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if sub is target:
                    return node.name
    return None


def apply_fixes(text: str, findings: list[SourceFinding]) -> tuple[str, int]:
    """Rewrite the fixable findings in one file. Returns (new_text, count).

    Applied last-first so that an earlier rewrite never shifts the offsets of one
    still to come. Findings without a fix are left strictly alone.
    """
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln)

    def offset(line: int, col: int) -> int:
        return starts[line - 1] + (col - 1)

    todo = [f for f in findings if f.fix]
    todo.sort(key=lambda f: (f.line, f.col), reverse=True)
    out = text
    for f in todo:
        a, b = offset(f.line, f.col), offset(f.end_line, f.end_col)
        out = out[:a] + f.fix + out[b:]
    return out, len(todo)
